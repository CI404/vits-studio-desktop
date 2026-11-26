"""模型发现：扫描模型根目录，读出每个模型的元信息。

支持三类目录（可同时挂载多个根目录）：
  1. `models_out/<id>/`            —— 训练产物目录（model.json + sovits.pth/gpt.ckpt + ref.*）
  2. `VITS标准模型/<name>_VITS模型/` —— 打包后的标准模型包（同名文件 + metrics.json + MODEL_CARD.md）
  3. `community_models/<name>/`     —— 社区模型包（文件名五花八门，常常没有 model.json）

发现策略（社区包友好）：
  - 有 `model.json` 时按字段读，路径相对于模型目录解析；
  - 没有 `model.json` 时在目录里（最多 3 层）尽力探测 `*.pth/*.pt/*.ckpt` 和
    `ref*.wav` / `*.txt`，也支持音频在 `dataset/xxx.wav` 子目录里的布局；
  - 引擎版本优先读 `model.json` 的 `engine_version`（兼容旧的 `sovits_version`），
    其次读权重前 2 字节（GPT-SoVITS 官方约定），再次读 `config.json`，最后回退 `v2Pro`；
  - 缺什么就写进 `missing` 列表，供 UI 显示「未就绪 + 缺什么」。

根目录若有 `manifest.json`，会读其中的版本号与评测指标（CER / timbre_self / RTF），
与单模型目录里的 `metrics.json` 一起合并进 API 输出。

新增模型 = 往任一模型根目录丢一个符合结构的文件夹，调一次 /api/models/reload。
"""
from __future__ import annotations

import json
import os
import wave
from dataclasses import dataclass, field
from pathlib import Path

BASE_MODEL_DIR = Path(r"<VITS_ENV>\GPT-SoVITS\GPT_SoVITS\pretrained_models")
BASE_S1 = BASE_MODEL_DIR / "s1v3.ckpt"
BASE_SOVITS = BASE_MODEL_DIR / "v2Pro" / "s2Gv2Pro.pth"

WEIGHT_EXT = {".pth", ".pt", ".ckpt"}
AUDIO_EXT = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}
TEXT_EXT = {".txt", ".lab", ".list"}

# 不参与探测的目录：日志/试听/训练中间产物，避免把 eval wav 或 D_*.pth 当模型
SKIP_DIRS = {
    ".git", "__pycache__", "logs", "log", "runs", "wandb", "_removed", ".idea",
    "node_modules", "site-packages", "sample", "samples", "eval", "evaluation",
    "output", "outputs", "temp", "tmp",
}

# 权重前 2 字节 -> 引擎版本（与 GPT-SoVITS process_ckpt.head2version 一致）
HEAD2VERSION = {
    b"00": "v1", b"01": "v2", b"02": "v3", b"03": "v3",
    b"04": "v4", b"05": "v2Pro", b"06": "v2ProPlus",
    b"07": "v5dev", b"08": "v5turbo",
}

KNOWN_VERSIONS = ("v1", "v2", "v3", "v4", "v5dev", "v5turbo", "v2Pro", "v2ProPlus")
SUPPORTED_VERSIONS = ("v1", "v2", "v2Pro", "v2ProPlus")

_VERSION_ALIASES = {
    "v1": "v1", "1": "v1", "v1.0": "v1",
    "v2": "v2", "2": "v2", "v2.0": "v2",
    "v2pro": "v2Pro", "pro": "v2Pro", "v2pro+": "v2ProPlus",
    "v2proplus": "v2ProPlus", "v2proplus+": "v2ProPlus", "plus": "v2ProPlus",
    "v3": "v3", "3": "v3",
    "v4": "v4", "4": "v4",
    "v5dev": "v5dev", "v5turbo": "v5turbo",
}


def _read_json(p: Path) -> dict:
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def normalize_version(value) -> str:
    """把 model.json / config.json 里的版本写法归一化；未知值原样返回。"""
    raw = str(value or "").strip()
    if not raw:
        return ""
    key = raw.lower().replace("_", "").replace("-", "").replace(" ", "")
    if key in _VERSION_ALIASES:
        return _VERSION_ALIASES[key]
    # 兼容 "GPT-SoVITS v2Pro" / "v2Pro (finetune)" 这类描述
    for k, v in _VERSION_ALIASES.items():
        if len(k) > 2 and k in key:
            return v
    return raw


def detect_sovits_version(path) -> str:
    """读 SoVITS 权重前 2 字节判断版本（GPT-SoVITS 官方约定）。

    新版权重前两字节是 "00"~"08"；老版权重直接是 zip（"PK"），按文件大小粗判
    （v1 ≈ 84.9 MB 以下、v2 ≈ 84.9–700 MB、v3 ≈ 750 MB）。
    """
    try:
        p = Path(path)
        if not p or not p.exists() or p.suffix.lower() not in (".pth", ".pt"):
            return ""
        with p.open("rb") as fh:
            head = fh.read(2)
        if head in HEAD2VERSION:
            return HEAD2VERSION[head]
        if head == b"PK":
            size = p.stat().st_size
            if size < 82_978 * 1024:
                return "v1"
            if size < 700 * 1024 * 1024:
                return "v2"
            return "v3"
        return ""
    except Exception:  # noqa: BLE001
        return ""


def missing_engine_deps(version: str) -> list[str]:
    """v3/v4/v5 推理需要的额外底模 / vocoder 是否齐全。

    返回缺失项（中文短标签）；v1/v2/v2Pro/v2ProPlus 返回空列表。
    """
    version = normalize_version(version)
    if version in SUPPORTED_VERSIONS or not version:
        return []
    p = BASE_MODEL_DIR
    need: list[tuple[str, Path]] = []
    if version == "v3":
        need = [
            ("v3 底模 s2Gv3.pth", p / "s2Gv3.pth"),
            ("v3 vocoder（bigvgan 24k）", p / "models--nvidia--bigvgan_v2_24khz_100band_256x"),
        ]
    elif version == "v4":
        need = [
            ("v4 底模 s2Gv4.pth", p / "gsv-v4-pretrained" / "s2Gv4.pth"),
            ("v4 vocoder", p / "gsv-v4-pretrained" / "vocoder.pth"),
        ]
    elif version in ("v5dev", "v5turbo"):
        need = [
            (f"{version} 底模", p / "gsv-v5-pretrained" / f"s2G{version}.pth"),
        ]
    else:
        return [f"未知引擎版本 {version}"]
    return [label for label, path in need if not path.exists()]


def _ver_tuple(v: str) -> tuple:
    out = []
    for part in str(v or "0").replace("v", "").split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple((out + [0, 0, 0])[:3])


def _iter_files(root: Path, max_depth: int = 3):
    """遍历目录（跳过日志/试听/缓存等），最多 max_depth 层。"""
    try:
        root = Path(root)
        for dirpath, dirnames, filenames in os.walk(root):
            rel = Path(dirpath).relative_to(root)
            if len(rel.parts) > max_depth:
                dirnames[:] = []
                continue
            dirnames[:] = [
                d for d in dirnames
                if d.lower() not in SKIP_DIRS and not d.startswith(".")
            ]
            for name in filenames:
                if name.startswith("."):
                    continue
                yield Path(dirpath) / name
    except Exception:  # noqa: BLE001
        return


def _audio_seconds(p: Path) -> float:
    try:
        with wave.open(str(p), "rb") as w:
            rate = float(w.getframerate() or 1)
            return w.getnframes() / rate
    except Exception:  # noqa: BLE001
        pass
    try:  # mp3/flac 等交给 soundfile（本机有；失败就按大小粗估）
        import soundfile as sf  # noqa: PLC0415
        info = sf.info(str(p))
        return info.frames / float(info.samplerate or 1)
    except Exception:  # noqa: BLE001
        return 0.0


def _resolve_path(folder: Path, value) -> Path | None:
    if not value:
        return None
    p = Path(str(value)).expanduser()
    if not p.is_absolute():
        p = folder / p
    return p


def _warn_missing_path(folder: Path, p: Path) -> str:
    try:
        rel = p.relative_to(folder)
        return rel.as_posix()
    except Exception:  # noqa: BLE001
        return str(p)


def _probe(folder: Path) -> dict:
    """在没有 model.json 时尽力探测权重 / 参考音 / 文本。"""
    weights: list[dict] = []
    audio: list[Path] = []
    texts: list[Path] = []
    for f in _iter_files(folder, 3):
        try:
            size = f.stat().st_size
        except Exception:  # noqa: BLE001
            continue
        ext = f.suffix.lower()
        name = f.name.lower()
        if ext in WEIGHT_EXT and size >= 1024 * 1024:
            if name.startswith(("g_", "d_", "disc")) or "optim" in name:
                continue
            is_gpt = ext == ".ckpt" or "gpt" in name or "s1" in name or "t2s" in name
            weights.append({
                "path": f,
                "kind": "gpt" if is_gpt else "sovits",
                "size": size,
                "version": "" if is_gpt else detect_sovits_version(f),
            })
        elif ext in AUDIO_EXT and size >= 1024:
            audio.append(f)
        elif ext in TEXT_EXT and size >= 1:
            texts.append(f)

    sovits = _pick_sovits(weights)
    gpt = _pick_gpt(weights)
    ref_audio = _pick_ref_audio(folder, audio)
    ref_text = _pick_ref_text(texts, ref_audio)
    return {"sovits": sovits, "gpt": gpt, "ref_audio": ref_audio,
            "ref_text": ref_text, "weights": weights, "audio": audio, "texts": texts}


def _pick_sovits(weights: list[dict]) -> Path | None:
    items = [w for w in weights if w["kind"] == "sovits"]
    if not items:
        return None

    def score(w: dict) -> float:
        name = w["path"].name.lower()
        s = w["size"] / 1e6
        if name in ("sovits.pth", "sovits.pt"):
            s += 1000
        if "sovits" in name:
            s += 500
        if name.startswith("s2") or "_s2" in name or "s2g" in name:
            s += 300
        if w.get("version") in ("v2Pro", "v2ProPlus"):
            s += 100
        if w.get("version"):
            s += 30
        return s

    return max(items, key=score)["path"]


def _pick_gpt(weights: list[dict]) -> Path | None:
    items = [w for w in weights if w["kind"] == "gpt"]
    if not items:
        return None

    def score(w: dict) -> float:
        name = w["path"].name.lower()
        s = w["size"] / 1e6
        if name in ("gpt.ckpt", "gpt.pth"):
            s += 1000
        if "gpt" in name:
            s += 500
        if name.startswith("s1") or "t2s" in name:
            s += 300
        return s

    return max(items, key=score)["path"]


def _pick_ref_audio(folder: Path, audio: list[Path]) -> Path | None:
    if not audio:
        return None

    def score(p: Path) -> float:
        name = p.name.lower()
        try:
            rel = p.relative_to(folder).as_posix().lower()
        except Exception:  # noqa: BLE001
            rel = name
        s = 0.0
        if "ref" in name or "reference" in name:
            s += 100
        if "ref" in rel or "reference" in rel or "prompt" in rel:
            s += 60
        if "dataset" in rel or "data" in rel:
            s += 20
        if "sample" in rel or "eval" in rel or "output" in rel:
            s -= 80
        sec = _audio_seconds(p)
        if 2.5 <= sec <= 10:
            s += 40
        elif sec > 0:
            s -= abs(sec - 6) * 2
        # 同分时优先浅层目录
        s -= len(rel.split("/")) * 0.5
        return s

    return max(audio, key=score)


def _pick_ref_text(texts: list[Path], audio: Path | None) -> Path | None:
    if not texts:
        return None
    if audio:
        stem = audio.stem.lower()
        same_dir = [t for t in texts if t.parent == audio.parent]
        same_stem = [t for t in same_dir if t.stem.lower() == stem]
        if same_stem:
            return sorted(same_stem, key=lambda p: (p.suffix.lower() != ".txt", p.name))[0]
        any_stem = [t for t in texts if t.stem.lower() == stem]
        if any_stem:
            return sorted(any_stem, key=lambda p: (p.suffix.lower() != ".txt", p.name))[0]
        if same_dir:
            return sorted(same_dir, key=lambda p: (p.suffix.lower() != ".txt", p.name))[0]
    refs = [t for t in texts if "ref" in t.name.lower() or "reference" in t.name.lower()]
    if refs:
        return sorted(refs, key=lambda p: (p.suffix.lower() != ".txt", p.name))[0]
    return sorted(texts, key=lambda p: (p.suffix.lower() != ".txt", len(p.parts), p.name))[0]


def _extract_config(cfg: dict) -> dict:
    """从社区 config.json 里抽取推理可用/可展示的字段（结构五花八门，尽量都认）。"""
    if not isinstance(cfg, dict):
        return {}
    body = cfg.get("config") if isinstance(cfg.get("config"), dict) else cfg
    if isinstance(body.get("custom"), dict):
        body = body["custom"]
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    train = body.get("train") if isinstance(body.get("train"), dict) else {}
    model = body.get("model") if isinstance(body.get("model"), dict) else {}

    def first(*vals):
        for v in vals:
            if v is not None:
                return v
        return None

    out: dict = {}
    sr = first(data.get("sampling_rate"), body.get("sampling_rate"), train.get("sampling_rate"))
    if sr is not None:
        try:
            out["sampling_rate"] = int(sr)
        except (TypeError, ValueError):
            pass
    add_blank = first(data.get("add_blank"), body.get("add_blank"), train.get("add_blank"))
    if isinstance(add_blank, bool):
        out["add_blank"] = add_blank
    symbols = first(body.get("symbols"), data.get("symbols"), train.get("symbols"))
    if isinstance(symbols, (list, tuple)):
        out["symbols"] = list(symbols)
        out["symbols_count"] = len(symbols)
    ver = first(model.get("version"), body.get("version"), body.get("engine_version"),
                train.get("version"))
    if ver:
        out["version"] = normalize_version(ver)
    nspk = first(data.get("n_speakers"), body.get("n_speakers"))
    if nspk is not None:
        try:
            out["n_speakers"] = int(nspk)
        except (TypeError, ValueError):
            pass
    return out


def _read_model_config(folder: Path, meta: dict) -> tuple[dict, Path | None]:
    candidates: list[Path] = []
    raw = meta.get("config_path") or meta.get("config")
    if raw:
        p = _resolve_path(folder, raw)
        if p:
            candidates.append(p)
    candidates += [folder / "config.json", folder / "s2_config.json",
                   folder / "configs" / "config.json"]
    for f in _iter_files(folder, 2):
        if (f.suffix.lower() == ".json" and "config" in f.name.lower()
                and f.name.lower() not in ("model.json", "metrics.json", "manifest.json")):
            candidates.append(f)
    seen: set[Path] = set()
    for f in candidates:
        try:
            f = f.resolve()
        except Exception:  # noqa: BLE001
            pass
        if f in seen or not f.is_file():
            continue
        seen.add(f)
        cfg = _read_json(f)
        if cfg:
            return _extract_config(cfg), f
    return {}, None


@dataclass
class ModelInfo:
    id: str
    name: str
    language: str = "ja"
    ref_lang: str = "ja"
    finetuned: str = ""
    note: str = ""
    train_lines: int = 0
    version: str = ""                 # 模型包版本（1.2.0 / community-1）
    engine_version: str = ""          # 实际交给 GPT-SoVITS 的引擎版本
    sovits_version: str = ""          # 从权重头识别出的版本（权威）
    version_source: str = ""          # model.json | weights | config.json | default
    engine: str = "GPT-SoVITS"
    output_sr: int = 32000
    sampling_rate: int = 0            # config.json 里的训练采样率
    add_blank: bool | None = None     # config.json 里的训练开关（仅展示，权重已内嵌）
    symbols_count: int = 0            # config.json 里的自定义 symbols 数量
    config_file: str = ""
    cer: float | None = None
    timbre: float | None = None
    rtf: float | None = None
    folder: Path = field(default_factory=Path)
    source_root: str = ""
    source_kind: str = "local"
    gpt: Path | None = None
    sovits: Path | None = None
    ref_audio: Path | None = None
    ref_text: Path | None = None
    missing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unsupported: bool = False

    @property
    def ready(self) -> bool:
        return (not self.missing and not self.unsupported
                and bool(self.sovits and self.sovits.exists()
                         and self.ref_audio and self.ref_audio.exists()
                         and self.ref_text and self.ref_text.exists()))

    @property
    def status(self) -> str:
        if self.unsupported:
            return "unsupported"
        return "ready" if self.ready else "missing"

    @property
    def source_label(self) -> str:
        return "社区" if self.source_kind == "community" else "本地"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "language": self.language,
            "ref_lang": self.ref_lang,
            "finetuned": self.finetuned,
            "note": self.note,
            "train_lines": self.train_lines,
            "version": self.version,
            "engine_version": self.engine_version,
            "sovits_version": self.sovits_version,
            "version_source": self.version_source,
            "engine": self.engine,
            "output_sr": self.output_sr,
            "sampling_rate": self.sampling_rate,
            "add_blank": self.add_blank,
            "symbols_count": self.symbols_count,
            "config_file": self.config_file,
            "cer": self.cer,
            "timbre_self": self.timbre,
            "rtf": self.rtf,
            "ready": self.ready,
            "status": self.status,
            "missing": list(self.missing),
            "warnings": list(self.warnings),
            "has_gpt": bool(self.gpt and self.gpt.exists()),
            "source": self.source_root,
            "source_kind": self.source_kind,
            "source_label": self.source_label,
            "size_mb": round(sum(p.stat().st_size for p in (self.sovits, self.gpt)
                                 if p and p.exists()) / 1e6, 1),
        }


def _source_kind(root: Path, meta: dict) -> str:
    raw = str(meta.get("source_kind") or meta.get("source") or "").lower()
    if raw in ("community", "community_models") or "community" in raw:
        return "community"
    if "community" in root.name.lower():
        return "community"
    return "local"


def _build(d: Path, root: Path, manifest_entry: dict) -> ModelInfo | None:
    mj = d / "model.json"
    meta = _read_json(mj) if mj.exists() else {}
    probe = _probe(d)
    # 没有 model.json、也探测不到权重/参考音 -> 不是模型目录
    if not mj.exists() and not probe["weights"] and not probe["ref_audio"]:
        return None
    metrics = _read_json(d / "metrics.json")
    man_metrics = (manifest_entry or {}).get("metrics") or {}
    cfg, cfg_file = _read_model_config(d, meta)

    warnings: list[str] = []
    if mj.exists() and not meta:
        warnings.append("model.json 解析失败（JSON 语法错误？），已回退到自动探测")

    def choose(meta_key: str, probe_path: Path | None, label: str) -> Path | None:
        raw = meta.get(meta_key)
        if not raw:
            return probe_path
        p = _resolve_path(d, raw)
        if p and p.exists():
            return p
        if probe_path is not None:
            warnings.append(
                f"model.json 的 {meta_key} 指向 {_warn_missing_path(d, p) if p else raw} 不存在，"
                f"已自动改用探测到的 {_warn_missing_path(d, probe_path)}")
            return probe_path
        warnings.append(f"model.json 的 {meta_key} 指向的文件不存在：{raw}")
        return p

    sovits = choose("sovits_weights", probe["sovits"], "SoVITS 权重")
    gpt = choose("gpt_weights", probe["gpt"], "GPT 权重")
    ref_audio = choose("ref_audio", probe["ref_audio"], "参考音")
    ref_text = choose("ref_text", probe["ref_text"], "参考音文本")

    # 参考音与文本是同一段话：如果只给了音频，自动找同名 txt
    if ref_audio and ref_audio.exists() and (not ref_text or not ref_text.exists()):
        sibling = ref_audio.with_suffix(".txt")
        if sibling.exists():
            ref_text = sibling
            warnings.append(f"未显式提供参考音文本，已自动配对 {sibling.name}")

    detected = detect_sovits_version(sovits) if sovits else ""
    explicit_raw = meta.get("engine_version") or meta.get("sovits_version") or ""
    explicit = normalize_version(explicit_raw)
    cfg_ver = normalize_version(cfg.get("version") or "")
    if explicit and explicit in KNOWN_VERSIONS:
        engine_version, version_source = explicit, "model.json"
    elif explicit and detected in KNOWN_VERSIONS:
        # 写了无法识别的版本（常见是手滑/旧文档写法）：不砖，按权重头来，但明确提示
        engine_version, version_source = detected, "weights"
        warnings.append(
            f"model.json 的 engine_version={explicit} 无法识别，"
            f"已按权重头识别为 {detected} 加载")
    elif explicit:
        engine_version, version_source = "v2Pro", "default"
        warnings.append(
            f"model.json 的 engine_version={explicit} 无法识别，且权重头也识别不出，"
            f"已回退 v2Pro")
    elif detected:
        engine_version, version_source = detected, "weights"
    elif cfg_ver:
        engine_version, version_source = cfg_ver, "config.json"
    else:
        engine_version, version_source = "v2Pro", "default"
        warnings.append("未声明 engine_version 且无法从权重/config.json 识别版本，已回退 v2Pro")

    if explicit in KNOWN_VERSIONS and detected and explicit != detected:
        warnings.append(
            f"model.json 声明 engine_version={explicit}，但权重头识别为 {detected}；"
            f"GPT-SoVITS 以权重内嵌版本为准（实际按 {detected} 加载）")
    if cfg_ver and detected and cfg_ver != detected:
        warnings.append(f"config.json 的版本 {cfg_ver} 与权重头 {detected} 不一致，以权重头为准")

    missing: list[str] = []
    unsupported = False
    if not sovits or not sovits.exists():
        missing.append("SoVITS 权重（sovits.pth 或 *.pth）")
    if not ref_audio or not ref_audio.exists():
        missing.append("参考音（ref.wav；社区包常缺，需放角色本人的 3–10 秒干净语音）")
    if not ref_text or not ref_text.exists():
        missing.append("参考音文本（ref.txt，必须与参考音逐字一致）")

    if engine_version not in KNOWN_VERSIONS:
        unsupported = True
        missing.append(f"未知引擎版本 {engine_version}（支持 v1/v2/v2Pro/v2ProPlus）")
    else:
        deps = missing_engine_deps(engine_version)
        if deps:
            unsupported = True
            missing.append(f"引擎 {engine_version} 所需依赖：{'、'.join(deps)}")

    expected_symbols = {"v1": 322, "v2": 732, "v2Pro": 732, "v2ProPlus": 732}.get(engine_version)
    if cfg.get("symbols_count") and expected_symbols and cfg["symbols_count"] != expected_symbols:
        warnings.append(
            f"config.json 的 symbols 有 {cfg['symbols_count']} 个，与引擎内置 {engine_version} 词表"
            f"（{expected_symbols}）不一致，可能加载失败")
    if cfg.get("add_blank") is False:
        warnings.append("config.json 里 add_blank=false；推理不读该训练开关，权重已内嵌，仅作提示")

    output_sr = 0
    try:
        output_sr = int(meta.get("output_sr") or 0)
    except (TypeError, ValueError):
        output_sr = 0
    output_sr = output_sr or cfg.get("sampling_rate") or 32000

    name = str(meta.get("speaker") or meta.get("name") or d.name.replace("_VITS模型", ""))
    info = ModelInfo(
        id=str(meta.get("id") or name),
        name=name,
        language=str(meta.get("language") or "ja")[:2],
        ref_lang=str(meta.get("ref_lang") or meta.get("language") or "ja")[:2],
        finetuned=str(meta.get("finetuned", "")),
        note=str(meta.get("note", "")),
        train_lines=_to_int(meta.get("train_lines") or manifest_entry.get("train_lines")),
        version=str(meta.get("version") or manifest_entry.get("version") or ""),
        engine_version=engine_version,
        sovits_version=detected,
        version_source=version_source,
        engine=str(meta.get("engine") or "GPT-SoVITS"),
        output_sr=output_sr,
        sampling_rate=int(cfg.get("sampling_rate") or 0),
        add_blank=cfg.get("add_blank"),
        symbols_count=int(cfg.get("symbols_count") or 0),
        config_file=_warn_missing_path(d, cfg_file) if cfg_file else "",
        folder=d,
        source_root=root.name,
        source_kind=_source_kind(root, meta),
        gpt=gpt,
        sovits=sovits,
        ref_audio=ref_audio,
        ref_text=ref_text,
        missing=missing,
        warnings=warnings,
        unsupported=unsupported,
    )
    # 指标：优先单模型 metrics.json，其次 manifest
    info.cer = _num(metrics.get("cer_mean"), man_metrics.get("cer_mean"),
                    (manifest_entry.get("files") or {}).get("cer"))
    info.timbre = _num(metrics.get("timbre_self"), man_metrics.get("timbre_self"))
    info.rtf = _num(metrics.get("rtf_mean"), man_metrics.get("rtf_mean"))
    return info


def _to_int(v) -> int:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def _num(*vals):
    for v in vals:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


def discover(roots) -> list[ModelInfo]:
    """扫描一个或多个模型根目录，按 id 去重（保留版本号最高、可用的优先）。"""
    if isinstance(roots, (str, Path)):
        roots = [roots]
    found: dict[str, ModelInfo] = {}
    for raw in roots:
        root = Path(raw)
        if not root.exists() or not root.is_dir():
            continue
        manifest = _read_json(root / "manifest.json")
        entries = {str(m.get("id")): m for m in (manifest.get("models") or [])
                   if isinstance(m, dict)}
        for d in sorted(root.iterdir()):
            if not d.is_dir() or d.name.startswith(".") or d.name.lower() in SKIP_DIRS:
                continue
            try:
                info = _build(d, root, entries.get(d.name, {}))
            except Exception as e:  # noqa: BLE001
                print(f"[registry] 跳过 {d.name}: {type(e).__name__}: {e}", flush=True)
                continue
            if info is None:
                continue
            key = info.id.lower()
            old = found.get(key)
            if old is None:
                found[key] = info
                continue
            # 去重：版本高者优先；同版本时可用者优先；再相同保留先扫到的（根目录顺序即优先级）
            if ((_ver_tuple(info.version), info.ready)
                    > (_ver_tuple(old.version), old.ready)):
                found[key] = info
    items = list(found.values())
    items.sort(key=lambda m: (m.ready, _ver_tuple(m.version), m.name), reverse=True)
    return items
