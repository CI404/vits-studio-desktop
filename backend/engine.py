"""GPT-SoVITS 推理引擎：按模型目录加载权重并合成。

要点：
- 同一时刻只保留一个模型实例（12GB 显卡放两个会 OOM），切换模型时重建；
- 所有推理串行执行（外部用线程 + 锁）；
- 参考音语言用模型声明的 `ref_lang`（默认同 `language`）；
- **版本可配**：优先用 `model.json` 的 `engine_version`（兼容旧字段 `sovits_version`），
  其次用权重前 2 字节识别，最后回退 `v2Pro` 并打印明确警告；
  GPT-SoVITS 内部还会用权重内嵌版本做最终判定，本模块会把「声明版本 vs 实际版本」写进日志；
- **缺 GPT(s1) 时按版本选官方底模**：v1/v2 用各自官方 s1，v2Pro/v2ProPlus 用 s1v3.ckpt；
  对应底模缺失时回退 s1v3.ckpt 并提示；
- **输出归一化**：GPT-SoVITS 返回的是 int16 量级，必须缩放后再写 PCM16，
  否则会 100% 削波——听感就是爆音，而且三个模型听起来都像同一个人。
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import numpy as np

try:  # server.py 会把 backend 放进 sys.path；直接 import 时也兼容
    from registry import (KNOWN_VERSIONS, SUPPORTED_VERSIONS,
                          detect_sovits_version, missing_engine_deps,
                          normalize_version)
except ImportError:  # pragma: no cover
    from backend.registry import (KNOWN_VERSIONS, SUPPORTED_VERSIONS,  # type: ignore
                                  detect_sovits_version, missing_engine_deps,
                                  normalize_version)

REPO = Path(r"<VITS_ENV>\GPT-SoVITS")
PRETRAINED = REPO / "GPT_SoVITS" / "pretrained_models"
# 兼容开发模式（vits-upgrade/tools/_stubs）与打包模式（backend/_stubs）
_CANDIDATES = [
    Path(__file__).resolve().parent / "_stubs",
    Path(__file__).resolve().parents[2] / "vits-upgrade" / "tools" / "_stubs",
]
STUBS = next((p for p in _CANDIDATES if p.exists()), _CANDIDATES[0])

# 各版本缺 GPT(s1) 时的官方底模；优先按版本选，缺了再回退 s1v3.ckpt
S1_BASE_BY_VERSION = {
    "v1": PRETRAINED / "s1bert25hz-2kh-longer-epoch=68e-step=50232.ckpt",
    "v2": PRETRAINED / "gsv-v2final-pretrained" / "s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt",
    "v2Pro": PRETRAINED / "s1v3.ckpt",
    "v2ProPlus": PRETRAINED / "s1v3.ckpt",
    "v3": PRETRAINED / "s1v3.ckpt",
    "v4": PRETRAINED / "s1v3.ckpt",
    "v5dev": PRETRAINED / "s1v3.ckpt",
    "v5turbo": PRETRAINED / "s1v3.ckpt",
}
S1_FALLBACK = PRETRAINED / "s1v3.ckpt"

_sys_ready = False


def normalize_audio(audio) -> np.ndarray:
    """把模型输出规整到 ±0.95（处理 int16 量级）。"""
    a = np.asarray(audio, dtype=np.float32).reshape(-1)
    if a.size == 0:
        return a
    peak = float(np.abs(a).max())
    if peak > 1.5:                 # int16 量级
        a = a / 32768.0
        peak = float(np.abs(a).max())
    if peak > 0.99:
        a = a * (0.95 / peak)
    return a


def trim_silence(audio: np.ndarray, sr: int, thresh: float = 0.006, keep_ms: int = 30) -> np.ndarray:
    """裁掉首尾静音（保留 keep_ms 余量），避免"开头/结尾一大段空白"。"""
    a = np.asarray(audio, dtype=np.float32).reshape(-1)
    if a.size == 0:
        return a
    loud = np.where(np.abs(a) > thresh)[0]
    if loud.size == 0:
        return a
    margin = int(sr * keep_ms / 1000)
    start = max(0, int(loud[0]) - margin)
    end = min(a.size, int(loud[-1]) + margin)
    return a[start:end]


def _prepare_sys() -> None:
    global _sys_ready
    if _sys_ready:
        return
    for p in (str(REPO), str(REPO / "GPT_SoVITS"), str(REPO / "GPT_SoVITS" / "eres2net"), str(STUBS)):
        if Path(p).exists() and p not in sys.path:
            sys.path.insert(0, p)
    os.chdir(REPO)          # GPT-SoVITS 用大量相对路径
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    _sys_ready = True


def _read_text(path: Path) -> str:
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return Path(path).read_text(encoding=enc).strip()
        except Exception:  # noqa: BLE001
            continue
    return ""


def _resolve_version(info) -> tuple[str, str]:
    """返回 (实际用于 TTS 参数的版本, model.json 声明值)。缺省回退 v2Pro。"""
    raw = str(getattr(info, "engine_version", "") or getattr(info, "sovits_version", "") or "")
    version = normalize_version(raw)
    if not version:
        version = detect_sovits_version(getattr(info, "sovits", None))
    if not version:
        print("[engine] 警告：模型未声明 engine_version，且权重头无法识别版本，回退 v2Pro；"
              "若加载失败，请在 model.json 写 \"engine_version\": \"v1|v2|v2Pro|v2ProPlus\"", flush=True)
        version = "v2Pro"
    elif getattr(info, "version_source", "") == "default":
        print(f"[engine] 警告：{getattr(info, 'id', '模型')} 未声明 engine_version，"
              f"已按默认值回退 v2Pro；如权重不是 v2Pro，请在 model.json 写 "
              f"\"engine_version\": \"v1|v2|v2Pro|v2ProPlus\"", flush=True)
    return version, raw


def _check_version(version: str) -> None:
    if version in SUPPORTED_VERSIONS:
        return
    deps = missing_engine_deps(version)
    if deps:
        raise RuntimeError(
            f"引擎版本 {version} 暂不可用：缺少 {'、'.join(deps)}。"
            f"当前环境只装了 v1/v2/v2Pro/v2ProPlus 所需底模；"
            f"v3/v4 需要额外 vocoder 与 s2Gv3/v4 底模，详见 app/docs/ADD_MODEL.md")
    if version not in KNOWN_VERSIONS:
        raise RuntimeError(
            f"不支持的 engine_version={version!r}；GPT-SoVITS 支持 "
            f"v1 / v2 / v2Pro / v2ProPlus / v3 / v4 / v5dev / v5turbo，"
            f"当前可用 v1 / v2 / v2Pro / v2ProPlus")


def _pick_base_s1(version: str, model_id: str) -> Path:
    """模型目录没有 gpt.ckpt 时选官方 s1 底模。"""
    specific = S1_BASE_BY_VERSION.get(version)
    if specific and specific.exists():
        print(f"[engine] {model_id}: 未提供 gpt.ckpt，使用官方 s1 底模 {specific.name}（{version}）", flush=True)
        return specific
    if S1_FALLBACK.exists():
        if specific and specific != S1_FALLBACK:
            print(f"[engine] 警告：{model_id}: {version} 官方 s1 底模不存在（{specific}），"
                  f"回退 {S1_FALLBACK.name}；韵律可能略有差异，音色仍由 SoVITS 决定", flush=True)
        else:
            print(f"[engine] 警告：{model_id}: 未提供 gpt.ckpt，使用官方 s1 底模 {S1_FALLBACK.name}", flush=True)
        return S1_FALLBACK
    raise RuntimeError(
        f"模型 {model_id} 没有 gpt.ckpt，且官方 GPT(s1) 底模不存在：{specific or S1_FALLBACK}；"
        f"请把 s1 权重放进模型目录或补装 GPT-SoVITS 预训练模型")


class Engine:
    """串行推理引擎（单模型驻留，切模型时重载）。"""

    def __init__(self, device: str = "cuda"):
        self.device = device
        self._lock = threading.Lock()
        self._tts = None
        self._model_key: str | None = None
        self.last_load_seconds = 0.0
        self.max_retries = 2          # 近静音输出自动重试次数
        self.last_retries = 0
        self.loaded_model: str | None = None
        self.loaded_version: str | None = None
        self.loaded_s1: str | None = None
        self.last_load_error: str | None = None

    def load(self, info) -> None:
        """公开的加载入口（/api/models/load 用它做诊断）。"""
        with self._lock:
            self._load(info)

    def _load(self, info) -> None:
        _prepare_sys()
        from GPT_SoVITS.TTS_infer_pack.TTS import TTS  # 延迟导入，避免服务启动就吃显存

        key = str(info.folder)
        if self._model_key == key and self._tts is not None:
            return
        self._tts = None
        self._model_key = None
        self.last_load_error = None
        self.loaded_model = None
        self.loaded_version = None
        self.loaded_s1 = None
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass

        if not getattr(info, "sovits", None) or not Path(info.sovits).exists():
            self.last_load_error = f"缺少 SoVITS 权重：{getattr(info, 'sovits', None)}"
            raise RuntimeError(self.last_load_error)
        version, declared = _resolve_version(info)
        _check_version(version)

        gpt = info.gpt if (getattr(info, "gpt", None) and Path(info.gpt).exists()) else _pick_base_s1(version, info.id)
        cfg = {
            "custom": {
                "device": self.device,
                "is_half": str(self.device) != "cpu",
                "version": version,
                "t2s_weights_path": str(Path(gpt).resolve()),
                "vits_weights_path": str(Path(info.sovits).resolve()),
                "bert_base_path": str((PRETRAINED / "chinese-roberta-wwm-ext-large").resolve()),
                "cnhuhbert_base_path": str((PRETRAINED / "chinese-hubert-base").resolve()),
            }
        }
        notes = []
        if getattr(info, "config_file", ""):
            notes.append(f"config.json={info.config_file}")
        if getattr(info, "sampling_rate", 0):
            notes.append(f"sampling_rate={info.sampling_rate}")
        if getattr(info, "add_blank", None) is not None:
            notes.append(f"add_blank={info.add_blank}")
        if getattr(info, "symbols_count", 0):
            notes.append(f"symbols={info.symbols_count}")
        print(f"[engine] 加载 {info.id}: engine_version={version}"
              f"{'（model.json 声明 ' + declared + '）' if declared and declared != version else ''}"
              f"；s2={Path(info.sovits).name}；s1={Path(gpt).name}"
              f"{'；' + '，'.join(notes) if notes else ''}", flush=True)
        t0 = time.time()
        try:
            self._tts = TTS(cfg)
        except Exception as e:  # noqa: BLE001
            self._tts = None
            self._model_key = None
            self.last_load_error = (
                f"加载模型 {info.id} 失败（engine_version={version}，"
                f"s2={Path(info.sovits).name}，s1={Path(gpt).name}）："
                f"{type(e).__name__}: {e}")
            raise RuntimeError(self.last_load_error) from e
        self._model_key = key
        self.last_load_seconds = round(time.time() - t0, 1)
        self.loaded_model = info.id
        self.loaded_s1 = Path(gpt).name
        actual = str(getattr(getattr(self._tts, "configs", None), "version", "") or version)
        self.loaded_version = actual
        if actual != version:
            print(f"[engine] 警告：{info.id} 声明 engine_version={version}，"
                  f"但权重内嵌版本是 {actual}，已按 {actual} 加载（GPT-SoVITS 以权重头为准）", flush=True)
        sr = getattr(getattr(self._tts, "configs", None), "sampling_rate", None)
        print(f"[engine] 加载完成 {info.id}: 实际版本={actual}"
              f"{'，采样率=' + str(sr) if sr else ''}，耗时 {self.last_load_seconds}s", flush=True)

    def synthesize(self, info, text: str, lang: str | None = None, speed: float = 1.0,
                   ref_lang: str | None = None, top_k: int = 15,
                   temperature: float = 1.0) -> tuple[np.ndarray, int, float]:
        """返回 (音频 float32 ±0.95, 采样率, 耗时秒)。"""
        with self._lock:
            if not getattr(info, "ref_audio", None) or not Path(info.ref_audio).exists():
                raise RuntimeError(
                    f"模型 {info.id} 缺参考音（ref.wav）：社区包常只带权重，"
                    f"请放入角色本人 3–10 秒干净语音，并用「添加模型」补参考音")
            if not getattr(info, "ref_text", None) or not Path(info.ref_text).exists():
                raise RuntimeError(
                    f"模型 {info.id} 缺参考音文本（ref.txt）：必须写参考音里逐字说出的内容")
            ref_text = _read_text(Path(info.ref_text))
            if not ref_text:
                raise RuntimeError(f"模型 {info.id} 的参考音文本为空：{info.ref_text}")
            self._load(info)
            text = (text or "").strip()
            if not text:
                raise ValueError("文本为空")
            lang = (lang or info.language or "ja")[:2]
            inputs = {
                "text": text,
                "text_lang": lang,
                "ref_audio_path": str(Path(info.ref_audio).resolve()),
                "prompt_text": ref_text,
                "prompt_lang": (ref_lang or getattr(info, "ref_lang", None)
                                or info.language or "ja")[:2],
                "top_k": top_k,
                "top_p": 1.0,
                "temperature": temperature,
                "text_split_method": ("cut5" if len(text) > 40 else "cut0"),
                "batch_size": 1,
                "speed_factor": float(speed),
                "return_fragment": False,
            }
            t0 = time.time()
            audio = None
            # GPT 自回归偶发「秒 EOS」→ 输出近乎静音/极短，自动重试（对下游是必要保护）
            for attempt in range(self.max_retries + 1):
                results = list(self._tts.run(inputs))
                if not results:
                    continue
                sr, audio = results[-1]
                out = trim_silence(normalize_audio(audio), sr)
                rms = float(np.sqrt((out ** 2).mean())) if out.size else 0.0
                if out.size / sr >= 0.4 and rms >= 0.004:
                    return out, int(sr), round(time.time() - t0, 2)
                self.last_retries += 1
            # 全部失败也返回最后一次（至少不是空）
            if audio is None:
                raise RuntimeError(f"模型 {info.id} 合成失败：没有输出")
            sr, audio = results[-1]
            return trim_silence(normalize_audio(audio), int(sr)), int(sr), round(time.time() - t0, 2)

    def unload(self) -> None:
        with self._lock:
            self._tts = None
            self._model_key = None
            self.loaded_model = None
            self.loaded_version = None
            self.loaded_s1 = None
            try:
                import torch
                torch.cuda.empty_cache()
            except Exception:  # noqa: BLE001
                pass
