r"""「导出到手机」——把经典 VITS 模型做成鸿蒙端可导入的模型包（.zip）。

这个模块是桌面端（EXE / Web UI）与命令行工具之间唯一的桥：

  * **不重写转换逻辑**：导出 ONNX、转换 MindSpore Lite `.ms`、写 `model.json`、
    打 zip，全部直接调用 `harmony-ondevice/tools/make_ondevice_model.py` 里的函数
    （通过 import 复用同一份代码，CLI 与桌面端行为一致）。
  * 本模块只负责「桌面端需要的那一层」：任务管理、阶段/进度、日志采集、
    错误翻译（缺工具 / 缺权重 / 显存不足 …）、产物自检、包列表、hdc 推送。

对外接口（供 `server.py` 调用）：

    status()                         工具链 / hdc / 目录状态
    list_sources(app_models=None)    哪些模型能导出（经典 VITS）+ 哪些不能及原因
    start_build(...) -> job          后台任务（立即返回 job dict）
    job_snapshot(job_id)             进度、阶段、日志尾部、产物
    list_jobs(limit)                 最近任务
    list_packages(output_dir=None)   已生成的 zip 列表
    inspect_zip(path)                包自检（条目 / 必需文件 / model.json 摘要）
    reveal(path)                     在资源管理器里定位产物
    hdc_info() / push_to_device(...) 手机检测与 `hdc file send`

端上包格式见 `harmony-ondevice/docs/HANDOFF.md` §15：
    <id>/text2mel.ms
    <id>/mel2wav.t{128,256,512,1024}.ms
    <id>/model.json
    <id>/dur_ja.json / dur_zh.json
    <id>/README.txt
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import types
import zipfile
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------- 路径

APP_ROOT = Path(__file__).resolve().parents[1]          # app/（开发）/ _internal（打包后）


def _runtime_config() -> dict:
    """读 exe 同级 / cwd 的 config.json（可选），用于换机器时改工具链路径。"""
    for cand in (APP_ROOT.parent / "config.json", Path.cwd() / "config.json"):
        try:
            if cand.is_file():
                data = json.loads(cand.read_text(encoding="utf-8-sig"))
                if isinstance(data, dict):
                    return data
        except Exception:  # noqa: BLE001
            continue
    return {}


_CFG = _runtime_config()


def _find_project_root() -> Path:
    """定位工程根目录（含 `harmony-ondevice/tools` 与 `vits-upgrade/registry.json`）。

    开发态：`app/backend/ondevice.py` → parents[1] 是 `app`，父目录才是工程根。
    打包后：后端跑在 `_internal/backend/` 下，工程根还要再往上几层（dist → app → 工程根）。
    因此从 APP_ROOT 逐级向上找标记文件；换机器时可用 `VITS_PROJECT_ROOT` / `config.json`。
    """
    raw = (os.environ.get("VITS_PROJECT_ROOT") or _CFG.get("project_root") or "").strip()
    if raw:
        return Path(raw)
    markers = (Path("harmony-ondevice") / "tools" / "make_ondevice_model.py",
               Path("vits-upgrade") / "registry.json")
    cur = APP_ROOT
    for _ in range(8):
        for m in markers:
            try:
                if (cur / m).exists():
                    return cur
            except OSError:
                pass
        if cur.parent == cur:
            break
        cur = cur.parent
    return APP_ROOT.parent


PROJECT_ROOT = _find_project_root()

# 命令行工具的代码目录（只读复用，不修改它）
DEFAULT_TOOLS = PROJECT_ROOT / "harmony-ondevice" / "tools"
DEFAULT_VITS_ROOT = PROJECT_ROOT / "vits-upgrade"
DEFAULT_REGISTRY = DEFAULT_VITS_ROOT / "registry.json"
# converter_lite 2.5.0（PC 侧转换器，自带 DLL）
DEFAULT_CONVERTER = Path(r"<BUILD_DIR>\tools\mslite\converter-2.5.0")

TIERS = [128, 256, 512, 1024]
SAMPLE_RATE = 22050                 # 端上只支持经典 VITS v1（22.05 kHz / hop 256）
PACK_FORMAT = "vits-ondevice/1"
DEVICE_DOWNLOAD = "/storage/media/100/local/files/Docs/Download/"

CONFIG_NAMES = ("config.json", "finetune_speaker.json", "moegoe_config.json")
CKPT_GLOBS = ("G_*.pth", "G*.pth", "*_e*_s*.pth", "*.pth")
SKIP_CKPT_PREFIX = ("D_", "d_")

MAX_LOG_LINES = 400
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

STAGE_LABELS = {
    "queued": "排队中",
    "precheck": "检查环境",
    "export_onnx": "导出 ONNX",
    "convert_text2mel": "转换 text2mel",
    "convert_mel2wav": "转换 mel2wav",
    "pack": "打包",
    "inspect": "自检产物",
    "done": "完成",
    "error": "失败",
}


class OnDeviceError(RuntimeError):
    """带 hint 的可读错误（hint 会直接显示在 UI 上）。"""

    def __init__(self, message: str, hint: str = "", stage: str = ""):
        super().__init__(message)
        self.hint = hint
        self.stage = stage


# --------------------------------------------------------------------- 环境

def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def tools_dir() -> Path:
    return Path(_env("VITS_ONDEVICE_TOOLS") or _CFG.get("ondevice_tools") or str(DEFAULT_TOOLS))


def vits_root() -> Path:
    return Path(_env("VITS_ROOT") or _CFG.get("vits_root") or str(DEFAULT_VITS_ROOT))


def converter_dir() -> Path:
    return Path(_env("VITS_CONVERTER") or _CFG.get("converter") or str(DEFAULT_CONVERTER))


def registry_path() -> Path:
    return Path(_env("VITS_REGISTRY") or _CFG.get("vits_registry") or str(vits_root() / "registry.json"))


def _converter_exe(root: Path | None = None) -> Path | None:
    """converter_lite.exe 既可能在 <root>/converter/ 下，也可能直接在 <root>/。"""
    root = Path(root or converter_dir())
    for cand in (root / "converter" / "converter_lite.exe", root / "converter_lite.exe"):
        if cand.is_file():
            return cand
    return None


def _cli_path() -> Path:
    return tools_dir() / "make_ondevice_model.py"


def work_root() -> Path:
    """中间目录（ONNX / .ms）。

    ⚠️ converter_lite 2.5.0 对**非 ASCII 路径**不友好，所以中间目录必须是纯 ASCII；
    本机的工程目录是 `<PROJECT_ROOT>`（含中文），因此默认落在 `%LOCALAPPDATA%` 下，
    并逐级回退到其它 ASCII 路径。显式设置 `VITS_ONDEVICE_WORK` 时若含非 ASCII，
    `start_build()` 会直接报错（不静默换目录）。
    """
    cands: list[Path] = []
    raw = _env("VITS_ONDEVICE_WORK")
    if raw:
        cands.append(Path(raw))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        cands.append(Path(local) / "VITSStudio" / "ondevice-work")
    cands.append(Path(os.environ.get("TEMP") or r"C:\Windows\Temp") / "vits-ondevice-work")
    cands.append(Path(r"C:\vits-ondevice-work"))
    for c in cands:
        if str(c).isascii():
            try:
                c.mkdir(parents=True, exist_ok=True)
                return c
            except OSError:
                continue
    # 实在找不到：返回第一个但不保证成功（调用方会明确报错）
    return cands[0]


def check_work_env() -> None:
    """显式指定的中间目录必须存在且是 ASCII（converter_lite 的硬限制）。"""
    raw = _env("VITS_ONDEVICE_WORK")
    if not raw:
        return
    if not str(Path(raw)).isascii():
        raise OnDeviceError(
            f"VITS_ONDEVICE_WORK 含非 ASCII 字符：{raw}",
            hint="converter_lite 2.5.0 不支持非 ASCII 路径；改成纯英文目录，例如 D:\\vits_env")


def default_output_dir() -> Path:
    raw = _env("VITS_ONDEVICE_OUT") or str(_CFG.get("ondevice_out") or "").strip()
    if raw:
        return Path(raw)
    return APP_ROOT / "ondevice_out"


def _ensure_dir(p: Path) -> Path:
    p = Path(p).expanduser()
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise OnDeviceError(f"无法创建目录：{p}（{e}）",
                            hint="换一个可写的目录，例如 D:\\vits_packs") from e
    if not p.is_dir():
        raise OnDeviceError(f"不是目录：{p}")
    # 真写一个探测文件，避免只读目录到打包最后一步才失败
    probe = p / f".write_test_{os.getpid()}"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        raise OnDeviceError(f"目录不可写：{p}（{e}）",
                            hint="换一个可写的目录，例如 D:\\vits_packs") from e
    return p


# --------------------------------------------------------------------- 复用 CLI

_CLI_LOCK = threading.RLock()
_CLI_MODULE: Any = None


def _cli():
    """导入 `make_ondevice_model.py`（同一份实现，CLI / 桌面端共用）。

    首次导入会连带 import torch / numpy；耗时 3–10 秒，所以在后台线程里做，
    不在 HTTP 请求线程里做。
    """
    global _CLI_MODULE
    with _CLI_LOCK:
        if _CLI_MODULE is not None:
            return _CLI_MODULE

        if not _cli_path().is_file():
            raise OnDeviceError(
                f"找不到打包工具：{_cli_path()}",
                hint="把 harmony-ondevice\\tools 放到工程目录下，"
                     "或用环境变量 VITS_ONDEVICE_TOOLS 指向它")
        # CLI 读取这两个环境变量来决定 vits-upgrade 与 registry 位置
        os.environ.setdefault("VITS_ROOT", str(vits_root()))
        sys.path.insert(0, str(tools_dir()))
        try:
            import importlib
            mod = importlib.import_module("make_ondevice_model")
        except Exception as e:  # noqa: BLE001
            raise OnDeviceError(
                f"加载打包工具失败：{type(e).__name__}: {e}",
                hint="该工具依赖 torch / onnx / onnxruntime，请用装了这些的 Python 运行后端"
                     r"（本机：<VITS_ENV>\venv-modern\Scripts\python.exe）") from e
        _CLI_MODULE = mod
        return mod


def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", str(name or "")).strip("_").lower()
    return s or "model"


# --------------------------------------------------------------------- 模型来源

def _read_json(p: Path) -> dict:
    try:
        data = json.loads(Path(p).read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _config_info(cfg_path: Path | None) -> dict:
    """读 config 里的采样率 / add_blank / symbols（只做展示与可行性判断）。"""
    out = {"sample_rate": 0, "add_blank": None, "symbols_count": 0, "cleaner": ""}
    if not cfg_path or not Path(cfg_path).is_file():
        return out
    cfg = _read_json(Path(cfg_path))
    if not cfg:
        return out
    data = cfg.get("data") if isinstance(cfg.get("data"), dict) else cfg
    try:
        out["sample_rate"] = int(data.get("sampling_rate") or cfg.get("sampling_rate") or 0)
    except (TypeError, ValueError):
        out["sample_rate"] = 0
    ab = data.get("add_blank")
    if isinstance(ab, bool):
        out["add_blank"] = ab
    symbols = cfg.get("symbols") or data.get("symbols") or []
    if isinstance(symbols, (list, tuple)):
        out["symbols_count"] = len(symbols)
    for key in ("text_cleaners", "cleaners"):
        val = data.get(key) or cfg.get(key)
        if isinstance(val, (list, tuple)) and val:
            out["cleaner"] = str(val[0])
            break
    return out


def _pick_config(d: Path) -> Path | None:
    for n in CONFIG_NAMES:
        p = d / n
        if p.is_file():
            return p
    cands = sorted(p for p in d.glob("*.json")
                   if ("config" in p.name.lower() or "finetune" in p.name.lower())
                   and p.name.lower() not in ("model.json", "metrics.json", "manifest.json"))
    return cands[0] if cands else None


def _pick_checkpoint(d: Path) -> Path | None:
    for pat in CKPT_GLOBS:
        cands = [p for p in sorted(d.glob(pat))
                 if p.is_file() and not p.name.startswith(SKIP_CKPT_PREFIX)]
        if cands:
            return cands[0]
    return None


def _describe_dir(d: Path) -> dict:
    """扫描一个「经典 VITS 目录」（config + G_*.pth），返回来源描述。"""
    d = Path(d)
    cfg = _pick_config(d)
    ckpt = _pick_checkpoint(d)
    info = _config_info(cfg)
    sr = info["sample_rate"]
    reasons: list[str] = []
    if not cfg:
        reasons.append("缺 config（config.json / finetune_speaker.json / moegoe_config.json）")
    if not ckpt:
        reasons.append("缺生成器权重（G_*.pth）")
    if cfg and not sr:
        reasons.append("config 里读不到 sampling_rate")
    elif sr and sr != SAMPLE_RATE:
        reasons.append(f"采样率 {sr} Hz，端上只支持 {SAMPLE_RATE} Hz")
    exportable = not reasons
    name = re.sub(r"^inference[\s_\-]*", "", d.name, flags=re.I) or d.name
    return {
        "id": f"dir:{d.name}",
        "name": name,
        "kind": "dir",
        "kind_label": "目录",
        "config": str(cfg) if cfg else "",
        "checkpoint": str(ckpt) if ckpt else "",
        "folder": str(d),
        "sample_rate": sr,
        "add_blank": info["add_blank"],
        "symbols_count": info["symbols_count"],
        "cleaner": info["cleaner"],
        "exportable": exportable,
        "reason": "；".join(reasons),
        "size_mb": round(ckpt.stat().st_size / 1e6, 1) if ckpt and ckpt.exists() else 0.0,
    }


def _registry_sources() -> list[dict]:
    """读 vits-upgrade/registry.json（config + checkpoint 自洽的一批模型）。"""
    reg_file = registry_path()
    if not reg_file.is_file():
        return []
    body = _read_json(reg_file)
    entries = body.get("models") if isinstance(body.get("models"), list) else []
    root = reg_file.parent
    out: list[dict] = []
    for e in entries:
        if not isinstance(e, dict) or not e.get("name"):
            continue
        name = str(e["name"])

        def _abs(v) -> Path | None:
            if not v:
                return None
            p = Path(str(v))
            return p if p.is_absolute() else (root / p)

        cfg = _abs(e.get("config"))
        ckpt = _abs(e.get("checkpoint"))
        info = _config_info(cfg)
        sr = info["sample_rate"]
        reasons: list[str] = []
        if not cfg or not cfg.is_file():
            reasons.append("registry 里的 config 不存在")
        if not ckpt or not ckpt.is_file():
            reasons.append("registry 里的 checkpoint 不存在")
        if cfg and cfg.is_file() and not sr:
            reasons.append("config 里读不到 sampling_rate")
        elif sr and sr != SAMPLE_RATE:
            reasons.append(f"采样率 {sr} Hz，端上只支持 {SAMPLE_RATE} Hz")
        out.append({
            "id": f"registry:{name}",
            "name": name,
            "kind": "registry",
            "kind_label": "registry",
            "config": str(cfg) if cfg else "",
            "checkpoint": str(ckpt) if ckpt else "",
            "folder": str(ckpt.parent) if ckpt else "",
            "sample_rate": sr,
            "add_blank": info["add_blank"],
            "symbols_count": info["symbols_count"],
            "cleaner": info["cleaner"],
            "exportable": not reasons,
            "reason": "；".join(reasons),
            "size_mb": round(ckpt.stat().st_size / 1e6, 1) if ckpt and ckpt.exists() else 0.0,
            "note": str(e.get("note") or ""),
        })
    return out


def _extra_dirs() -> list[Path]:
    """除工程根目录下的 inference-* 外，还可以用环境变量加目录（; 分隔）。"""
    roots: list[Path] = [PROJECT_ROOT]
    raw = _env("VITS_ONDEVICE_DIRS")
    if raw:
        for part in re.split(r"[;]", raw):
            part = part.strip().strip('"')
            if part:
                roots.append(Path(part))
    out: list[Path] = []
    for root in roots:
        try:
            if not root.is_dir():
                continue
            for d in sorted(root.iterdir()):
                if d.is_dir() and re.match(r"^inference", d.name, re.I):
                    out.append(d)
        except OSError:
            continue
    return out


def list_sources(app_models: list[dict] | None = None) -> dict:
    """列出「能导出到手机」的模型，以及桌面端模型为什么不能导出。"""
    sources: list[dict] = []
    seen_ckpt: set[str] = set()

    for s in _registry_sources():
        key = str(Path(s["checkpoint"]).resolve()).lower() if s["checkpoint"] else ""
        if key:
            seen_ckpt.add(key)
        sources.append(s)
    for d in _extra_dirs():
        try:
            s = _describe_dir(d)
        except Exception:  # noqa: BLE001
            continue
        key = str(Path(s["checkpoint"]).resolve()).lower() if s["checkpoint"] else ""
        if key and key in seen_ckpt:
            continue                      # registry 已经有同一份权重
        if key:
            seen_ckpt.add(key)
        sources.append(s)

    # registry 排前面；同来源内可导出的优先
    sources.sort(key=lambda s: (s["kind"] != "registry", not s["exportable"], s["name"].lower()))

    unsupported: list[dict] = []
    folder_to_source = {str(Path(s["folder"]).resolve()).lower(): s["id"]
                        for s in sources if s.get("folder")}
    for m in (app_models or []):
        if not isinstance(m, dict) or not m.get("id"):
            continue
        mid = str(m["id"])
        folder = str(m.get("folder") or "").strip()
        # 同一个目录既是桌面端模型、又是经典 VITS 来源 → 可以导出
        if folder and Path(folder).resolve().as_posix().lower() in folder_to_source:
            continue
        sr = m.get("output_sr") or m.get("sampling_rate") or 32000
        similar = [s for s in sources if s["name"].lower() == mid.lower()]
        unsupported.append({
            "id": mid,
            "name": str(m.get("name") or mid),
            "engine": str(m.get("engine") or "GPT-SoVITS"),
            "output_sr": sr,
            "similar": [s["id"] for s in similar],
            "reason": ("GPT-SoVITS 需要 BERT + 自回归 GPT + s1 语义 token + 32 kHz 声码器，"
                       f"端上跑不了（该模型 {sr} Hz）；端上只支持经典 VITS 22.05 kHz 模型"
                       + (f"。同名经典 VITS 模型：{similar[0]['id']}，可在导出面板里选它"
                          if similar else "")),
        })

    return {
        "sources": sources,
        "exportable": [s for s in sources if s["exportable"]],
        "skipped": [s for s in sources if not s["exportable"]],
        "unsupported": unsupported,
        "tiers": list(TIERS),
        "sample_rate": SAMPLE_RATE,
        "hint": "端上只支持「经典 VITS v1（22.05 kHz / hop 256）」模型；"
                "GPT-SoVITS 模型（models_out / VITS标准模型 / 社区包）不能导出。",
    }


def resolve_source(model_id: str) -> dict:
    """把 API 里的 model_id 解析成来源：registry 名 / sources 里的 id / 目录 / ONNX 目录。"""
    raw = str(model_id or "").strip()
    if not raw:
        raise OnDeviceError("model_id 不能为空", hint="传 sources 里的 id，例如 registry:xya")

    for s in list_sources()["sources"]:
        if raw in (s["id"], s["name"]) or raw.lower() in (s["id"].lower(), s["name"].lower()):
            if not s["exportable"]:
                raise OnDeviceError(f"模型 {s['name']} 不能导出：{s['reason']}",
                                    hint="换一个 22.05 kHz 的经典 VITS 模型，见 /api/ondevice/sources")
            return s

    p = Path(raw).expanduser()
    if p.is_dir():
        if (p / "text2mel.onnx").is_file() and (p / "mel2wav.onnx").is_file():
            return {
                "id": f"onnx:{p.name}", "name": p.name, "kind": "onnx", "kind_label": "ONNX",
                "config": "", "checkpoint": "", "folder": str(p),
                "sample_rate": SAMPLE_RATE, "add_blank": None, "symbols_count": 0,
                "cleaner": "", "exportable": True, "reason": "", "size_mb": 0.0,
            }
        s = _describe_dir(p)
        if not s["exportable"]:
            raise OnDeviceError(f"目录 {p} 不能导出：{s['reason']}")
        return s

    raise OnDeviceError(
        f"未知模型：{raw}",
        hint="先 GET /api/ondevice/sources 看可导出的模型；也可以直接传经典 VITS 目录路径")


# --------------------------------------------------------------------- 任务

_JOBS: dict[str, dict] = {}
_JOBS_ORDER: list[str] = []
_JOBS_LOCK = threading.Lock()
BUILD_LOCK = threading.Lock()          # 同一时刻只跑一个打包（CPU/内存重）


def _set(job: dict, stage: str | None = None, progress: float | None = None,
         message: str | None = None) -> None:
    if stage:
        job["stage"] = stage
        job["stage_label"] = STAGE_LABELS.get(stage, stage)
    if progress is not None:
        job["progress"] = round(max(float(job.get("progress") or 0.0), min(1.0, float(progress))), 3)
    elif stage == "done":
        job["progress"] = 1.0
    if message is not None:
        job["message"] = str(message)


def _append_log(job: dict, line: str) -> None:
    # onnxruntime / converter 会往 stdout 打 ANSI 色码，日志里去掉，否则 UI 一屏乱码
    line = _ANSI_RE.sub("", str(line)).replace("\x00", "").rstrip()
    if not line:
        return
    logs = job.setdefault("logs", [])
    logs.append(line)
    if len(logs) > MAX_LOG_LINES:
        del logs[: len(logs) - MAX_LOG_LINES]


def _on_log_line(job: dict, line: str, kind: str = "out") -> None:
    line = _ANSI_RE.sub("", str(line)).replace("\x00", "")
    _append_log(job, ("! " if kind == "err" else "") + line)
    text = line.strip()
    # 阶段推进只做「前进」，避免日志顺序导致进度回退
    if "[src]" in text or "[build] id=" in text:
        _set(job, "export_onnx", 0.06, "导出 ONNX（text2mel / mel2wav）")
    elif "[t2m] onnx" in text:
        _set(job, "export_onnx", 0.18, "text2mel.onnx 已导出")
    elif "[t2m] verify" in text:
        _set(job, "export_onnx", 0.26, "text2mel 数值自检通过")
    elif "[m2w] onnx" in text:
        _set(job, "export_onnx", 0.32, "mel2wav.onnx 已导出")
    elif "[conv] text2mel" in text and "OK" in text:
        _set(job, "convert_mel2wav", 0.44, "text2mel.ms 转换完成")
    elif "[conv] mel2wav" in text and "OK" in text:
        tier_txt = ""
        idx = 0
        try:
            tag = text.split("[conv]", 1)[1].split(":", 1)[0].strip()
            tier_txt = tag.split(".t")[-1]
            idx = TIERS.index(int(tier_txt))
        except Exception:  # noqa: BLE001
            idx = 0
        total = max(1, len(TIERS))
        _set(job, "convert_mel2wav", 0.44 + 0.48 * (idx + 1) / total,
             f"mel2wav T{tier_txt} 转换完成" if tier_txt else "mel2wav 转换完成")
    elif "[pack]" in text:
        _set(job, "pack", 0.93, "写出模型包目录")
    elif "[zip ]" in text:
        _set(job, "pack", 0.97, "压缩成 zip")
    elif "[done]" in text:
        _set(job, "pack", 0.99, "打包完成")


class _Tee:
    """把 CLI 的 stdout/stderr 接到任务日志（同时照常写回 server.log）。"""

    def __init__(self, stream, job: dict, kind: str):
        self._stream = stream
        self._job = job
        self._kind = kind
        self._buf = ""
        self.encoding = "utf-8"
        self.errors = "replace"

    def write(self, data):  # noqa: ANN001
        text = data if isinstance(data, str) else str(data)
        try:
            if self._stream is not None:
                self._stream.write(text)
        except Exception:  # noqa: BLE001
            pass
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            _on_log_line(self._job, line, self._kind)
        return len(text)

    def flush(self) -> None:
        try:
            if self._stream is not None:
                self._stream.flush()
        except Exception:  # noqa: BLE001
            pass
        if self._buf.strip():
            line, self._buf = self._buf, ""
            _on_log_line(self._job, line, self._kind)

    def isatty(self) -> bool:
        return False

    def writable(self) -> bool:
        return True


def _last_error_line(job: dict) -> str:
    for line in reversed(job.get("logs") or []):
        m = re.search(r"\[error\]\s*(.+)$", str(line))
        if m:
            return m.group(1).strip()
    return ""


def _hint_for(message: str) -> str:
    msg = str(message or "")
    low = msg.lower()
    if "22.05" in msg or "22050" in msg:
        return "端上只支持经典 VITS v1（22.05 kHz / hop 256）；44.1/48 kHz 或其他结构需要重新训练/改造端上运行时"
    if "weights_only" in low or "unpickling" in low or "legacy .tar" in low:
        return ("权重文件是旧格式（legacy .tar）或已损坏；换同一次训练产出的 G_*.pth，"
                "确认文件没被截断。工具用 torch.load 读权重，PyTorch 2.6+ 默认 weights_only=True")
    if "converter" in low:
        return ("检查转换器：<BUILD_DIR>\\tools\\mslite\\converter-2.5.0 下应有 "
                "converter\\converter_lite.exe 与 lib\\；可用环境变量 VITS_CONVERTER 指定")
    if "out of memory" in low or "显存" in msg or "alloc" in low or "oom" in low:
        return "内存/显存不足：先关掉其它占显存的程序（包括正在合成的模型），或只导出 128/256 两档后再试"
    if "config" in low and "找不到" in msg:
        return "目录里需要 config.json / finetune_speaker.json / moegoe_config.json 之一"
    if "g_*.pth" in low or "g_" in low:
        return "目录里需要生成器权重（G_latest.pth / G_*.pth）；D_*.pth 是判别器，不能用"
    if "onnx" in low:
        return "ONNX 导出失败：确认后端 Python 里有 torch / onnx / onnxruntime（本机 venv-modern 已装）"
    if "cleaner" in low or "tokenize" in low or "示范句" in msg:
        return "该模型的文本 cleaner 不支持内置示范句；可先用同结构的模型试，或补一条中文/日文示范句"
    if "no such file" in low or "找不到" in msg:
        return "检查工具链与模型文件是否还在原路径"
    return ""


def _fail(job: dict, message: str, hint: str = "") -> None:
    job["status"] = "error"
    job["stage"] = "error"
    job["stage_label"] = STAGE_LABELS["error"]
    job["error"] = str(message)
    if hint:
        job["error_hint"] = hint
    job["message"] = "失败：" + str(message)
    _append_log(job, "[job] ERROR " + str(message))


def _norm_tiers(tiers) -> list[int]:
    if tiers is None or tiers == "" or tiers == []:
        return list(TIERS)
    if isinstance(tiers, str):
        parts = [p for p in re.split(r"[,\s]+", tiers) if p.strip()]
    else:
        parts = list(tiers)
    out: list[int] = []
    for p in parts:
        try:
            t = int(p)
        except (TypeError, ValueError):
            raise OnDeviceError(f"档位必须是整数：{p!r}") from None
        if t not in TIERS:
            raise OnDeviceError(f"档位只能是 {TIERS}，收到 {t}")
        if t not in out:
            out.append(t)
    if not out:
        raise OnDeviceError("至少选一个档位")
    return sorted(out)


def start_build(model_id: str, tiers=None, output_dir: str | None = None,
                display: str | None = None, speaker: str | None = None,
                language: str | None = None, keep_work: bool = False,
                no_fp16: bool = False, store: bool = False,
                force_id: str | None = None) -> dict:
    """创建后台打包任务并立即返回 job。

    能在创建前判断的错误（工具链/转换器/目录/档位）直接抛 OnDeviceError → HTTP 400，
    这样界面点「开始导出」就能立刻看到原因，不用等后台线程。
    """
    if not _cli_path().is_file():
        raise OnDeviceError(f"找不到打包工具：{_cli_path()}",
                            hint="把 harmony-ondevice\\tools 放到工程目录下，"
                                 "或用环境变量 VITS_ONDEVICE_TOOLS 指向它")
    if _converter_exe() is None:
        raise OnDeviceError(f"缺转换器：{converter_dir()} 下找不到 converter_lite.exe",
                            hint="把 converter-2.5.0（含 converter\\ 与 lib\\）放到该目录，"
                                 "或用环境变量 VITS_CONVERTER 指向它")
    check_work_env()
    wroot = work_root()
    if not str(wroot).isascii():
        raise OnDeviceError(f"中间目录含非 ASCII 字符：{wroot}",
                            hint="converter_lite 2.5.0 不支持非 ASCII 路径；用环境变量 "
                                 "VITS_ONDEVICE_WORK 指向纯英文目录，例如 D:\\vits_env")

    source = resolve_source(model_id)
    tier_list = _norm_tiers(tiers)
    out_dir = _ensure_dir(Path(output_dir) if output_dir else default_output_dir())
    mid = _slug(force_id or source["name"] or "model")

    job_id = f"{int(time.time() * 1000)}-{mid}"
    with _JOBS_LOCK:
        job = {
            "id": job_id,
            "status": "queued",
            "stage": "queued",
            "stage_label": STAGE_LABELS["queued"],
            "progress": 0.0,
            "message": "等待执行（打包是单任务串行）",
            "logs": [],
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "request": {
                "model_id": model_id,
                "tiers": tier_list,
                "output_dir": str(out_dir),
                "display": display or "",
                "language": language or "",
                "keep_work": bool(keep_work),
            },
            "source": {k: source.get(k) for k in
                       ("id", "name", "kind", "config", "checkpoint", "folder",
                        "sample_rate", "symbols_count", "size_mb")},
            "tiers": tier_list,
            "output_dir": str(out_dir),
            "model_id": mid,
            "zip_path": "",
            "result": None,
            "error": None,
        }
        _JOBS[job_id] = job
        _JOBS_ORDER.append(job_id)
        while len(_JOBS_ORDER) > 50:
            old = _JOBS_ORDER.pop(0)
            _JOBS.pop(old, None)

    opts = {"display": display or "", "speaker": speaker or "", "language": language or "",
            "keep_work": bool(keep_work), "no_fp16": bool(no_fp16), "store": bool(store)}
    th = threading.Thread(target=_worker, args=(job, source, tier_list, out_dir, opts),
                          name=f"ondevice-{mid}", daemon=True)
    th.start()
    return job_snapshot(job_id)


def _worker(job: dict, source: dict, tiers: list[int], out_dir: Path, opts: dict) -> None:
    with BUILD_LOCK:
        job["started_at"] = time.time()
        _set(job, "precheck", 0.01, "检查工具链与模型文件")
        try:
            _run_build(job, source, tiers, out_dir, opts)
            _set(job, "done", 1.0, f"完成：{Path(job.get('zip_path') or '').name}")
            job["status"] = "done"
        except SystemExit as e:                       # CLI 的 die()
            msg = _last_error_line(job) or f"打包工具退出（code={e.code}）"
            _fail(job, msg, _hint_for(msg))
        except OnDeviceError as e:
            _fail(job, str(e), e.hint or _hint_for(str(e)))
        except MemoryError:
            _fail(job, "内存不足（MemoryError）", _hint_for("out of memory"))
        except BaseException as e:                     # noqa: BLE001
            msg = f"{type(e).__name__}: {e}"
            if "out of memory" in str(e).lower() or "alloc" in str(e).lower():
                _fail(job, msg, _hint_for("out of memory"))
            else:
                _fail(job, f"打包失败：{msg}", _hint_for(msg))
        finally:
            job["finished_at"] = time.time()
            job["duration_s"] = round(job["finished_at"] - (job["started_at"] or job["finished_at"]), 1)
            # 收尾：失败时保留 work 方便排查；成功且 keep_work=false 时清掉 200 MB 中间产物
            if job["status"] == "done" and not opts.get("keep_work"):
                _cleanup_work(job)


def _cleanup_work(job: dict) -> None:
    """成功后清理中间目录（ONNX + .ms 共约 200 MB）。

    Windows 上 converter / 杀软偶尔会短暂占用刚写出的 .ms，删不掉就重试几次，
    仍失败也不算任务失败（只记一行日志）。
    """
    work = job.get("work_dir")
    if not work:
        return
    target = Path(work)

    def _onerror(func, path, exc_info):  # noqa: ANN001
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception:  # noqa: BLE001
            pass

    for attempt in range(3):
        if not target.exists():
            _append_log(job, f"[job] 已清理中间目录 {target}")
            return
        try:
            shutil.rmtree(target, onerror=_onerror)
        except Exception:  # noqa: BLE001
            pass
        if not target.exists():
            _append_log(job, f"[job] 已清理中间目录 {target}")
            return
        time.sleep(0.6 * (attempt + 1))
    _append_log(job, f"[job] 中间目录未能完全清理（可手动删除）：{target}")


def _build_args(cli, source: dict, mid: str, tiers: list[int], out_dir: Path,
                work: Path, opts: dict) -> types.SimpleNamespace:
    kind = source["kind"]
    model = source["name"] if kind == "registry" else ""
    dir_ = source["folder"] if kind == "dir" else ""
    onnx = source["folder"] if kind == "onnx" else ""
    return types.SimpleNamespace(
        model=model or None,
        dir=dir_ or None,
        onnx=onnx or None,
        registry=str(registry_path()),
        config="", ckpt="", meta="",
        id=mid,
        speaker=opts.get("speaker") or "",
        display=opts.get("display") or "",
        language=opts.get("language") or "",
        sid=None,
        tiers=",".join(str(t) for t in tiers),
        no_fp16=bool(opts.get("no_fp16")),
        converter=str(converter_dir()),
        out=str(out_dir),
        work=str(work),
        keep_work=bool(opts.get("keep_work")),
        store=bool(opts.get("store")),
    )


def _run_build(job: dict, source: dict, tiers: list[int], out_dir: Path, opts: dict) -> None:
    cli = _cli()
    exe = _converter_exe()
    if exe is None:
        raise OnDeviceError(
            f"缺转换器：{converter_dir()} 下找不到 converter_lite.exe",
            hint="把 converter-2.5.0（含 converter\\ 与 lib\\）放到该目录，"
                 "或用环境变量 VITS_CONVERTER 指向它")
    if source["kind"] != "onnx":
        for key, label in (("config", "config 文件"), ("checkpoint", "生成器权重")):
            if not source.get(key) or not Path(source[key]).is_file():
                raise OnDeviceError(f"缺{label}：{source.get(key) or '未找到'}",
                                    hint=_hint_for(f"找不到 {label}"))
        if int(source.get("sample_rate") or 0) != SAMPLE_RATE:
            raise OnDeviceError(
                f"该模型采样率 {source.get('sample_rate') or '未知'} Hz，端上只支持 {SAMPLE_RATE} Hz",
                hint=_hint_for("22.05 kHz"))

    work_root_path = work_root()
    if not str(work_root_path).isascii():
        raise OnDeviceError(
            f"中间目录含非 ASCII 字符：{work_root_path}",
            hint="converter_lite 2.5.0 不支持非 ASCII 路径；用环境变量 VITS_ONDEVICE_WORK "
                 r"指向纯英文目录，例如 D:\vits_env")
    mid = job["model_id"]
    work = work_root_path / mid
    try:
        work.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise OnDeviceError(f"无法创建中间目录：{work}（{e}）") from e
    job["work_dir"] = str(work)

    args = _build_args(cli, source, mid, tiers, out_dir, work, opts)
    _append_log(job, f"[job] 来源={source['id']} id={mid} tiers={tiers} out={out_dir}")
    _append_log(job, f"[job] work={work} converter={exe}")
    _set(job, "export_onnx", 0.03, "导出 ONNX（text2mel / mel2wav）")

    # ---- 给 CLI 的转换器调用挂一个进度钩子（复用同一份 convert_all）----
    orig_run_converter = cli.run_converter

    def patched(conv_root, cargs, tag):  # noqa: ANN001
        if tag == "text2mel":
            _set(job, "convert_text2mel", 0.34, "正在转换 text2mel.ms（10–20 秒）")
        else:
            tier_txt = str(tag).split(".t")[-1]
            try:
                idx = tiers.index(int(tier_txt))
            except Exception:  # noqa: BLE001
                idx = 0
            _set(job, "convert_mel2wav", 0.44 + 0.48 * idx / max(1, len(tiers)),
                 f"正在转换 mel2wav T{tier_txt}（第 {idx + 1}/{len(tiers)} 档）")
        ok = orig_run_converter(conv_root, cargs, tag)
        if not ok:
            _append_log(job, f"[conv] {tag} 失败")
        return ok

    cli.run_converter = patched
    stdout_tee = _Tee(sys.stdout, job, "out")
    stderr_tee = _Tee(sys.stderr, job, "err")
    try:
        with contextlib.redirect_stdout(stdout_tee), contextlib.redirect_stderr(stderr_tee):
            cli.cmd_build(args)          # ← CLI 的完整流程：导出 → 转换 → 打包
    finally:
        try:
            stdout_tee.flush()
            stderr_tee.flush()
        except Exception:  # noqa: BLE001
            pass
        cli.run_converter = orig_run_converter

    zip_path = out_dir / f"{mid}.zip"
    if not zip_path.is_file():
        err = _last_error_line(job)
        raise OnDeviceError(err or f"打包结束但没有产物：{zip_path}",
                            hint=_hint_for(err))
    job["zip_path"] = str(zip_path)
    _set(job, "inspect", 0.99, "校验产物")
    info = inspect_zip(zip_path)
    job["result"] = {
        "zip": str(zip_path),
        "zip_name": zip_path.name,
        "size_bytes": zip_path.stat().st_size,
        "size_mb": round(zip_path.stat().st_size / 1e6, 1),
        "output_dir": str(out_dir),
        "model_id": mid,
        "tiers": tiers,
        "inspect": info,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _append_log(job, f"[job] 产物 {zip_path} ({zip_path.stat().st_size / 1e6:.1f} MB)")
    if not info.get("ok"):
        raise OnDeviceError(
            "产物自检未通过：" + "、".join(info.get("problems") or ["未知问题"]),
            hint="zip 里缺必需文件，详见任务日志")


def job_snapshot(job_id: str) -> dict:
    with _JOBS_LOCK:
        job = _JOBS.get(str(job_id))
    if job is None:
        raise OnDeviceError(f"未知任务：{job_id}", hint="任务列表 GET /api/ondevice/build")
    snap = {k: v for k, v in job.items() if k not in ("logs",)}
    logs = job.get("logs") or []
    snap["logs_tail"] = logs[-120:]
    snap["log_count"] = len(logs)
    return snap


def list_jobs(limit: int = 20) -> dict:
    with _JOBS_LOCK:
        ids = list(reversed(_JOBS_ORDER))[: max(1, int(limit))]
        items = []
        for jid in ids:
            job = _JOBS.get(jid)
            if not job:
                continue
            items.append({
                "id": job["id"], "status": job["status"], "stage": job["stage"],
                "stage_label": job.get("stage_label"), "progress": job.get("progress"),
                "message": job.get("message"), "model_id": job.get("model_id"),
                "zip_path": job.get("zip_path"), "error": job.get("error"),
                "created_at": job.get("created_at"),
            })
    return {"jobs": items}


# --------------------------------------------------------------------- 产物

def inspect_zip(path) -> dict:
    """结构化自检一个端上模型包（与 CLI `inspect` 同一套判定规则）。"""
    p = Path(path).expanduser()
    out: dict = {
        "ok": False, "path": str(p), "name": p.name, "size_bytes": 0, "size_mb": 0.0,
        "entries": [], "missing": [], "problems": [], "meta": {}, "tiers": [],
        "dur_files": [], "has_readme": False,
    }
    if not p.is_file():
        out["problems"].append(f"文件不存在：{p}")
        return out
    out["size_bytes"] = p.stat().st_size
    out["size_mb"] = round(out["size_bytes"] / 1e6, 1)
    try:
        with zipfile.ZipFile(p) as z:
            names = z.namelist()
            out["entries"] = sorted(
                ({"name": n, "size": z.getinfo(n).file_size,
                  "size_mb": round(z.getinfo(n).file_size / 1e6, 2)} for n in names),
                key=lambda e: e["name"])
            metas = [n for n in names if n.endswith("model.json")]
            if not metas:
                out["problems"].append("包内没有 model.json（App 会拒绝导入）")
                return out
            meta_name = sorted(metas, key=lambda n: n.count("/"))[0]
            prefix = meta_name.rsplit("/", 1)[0] + "/" if "/" in meta_name else ""
            out["prefix"] = prefix
            try:
                meta = json.loads(z.read(meta_name).decode("utf-8"))
            except Exception as e:  # noqa: BLE001
                out["problems"].append(f"model.json 解析失败：{type(e).__name__}: {e}")
                return out
            out["meta"] = meta
            out["tiers"] = list(meta.get("tiers") or [])
            if not meta.get("id"):
                out["problems"].append("model.json 缺 id")
            if meta.get("format") and meta["format"] != PACK_FORMAT:
                out["problems"].append(f"format={meta['format']}（期望 {PACK_FORMAT}）")
            if int(meta.get("output_sr") or 0) != SAMPLE_RATE:
                out["problems"].append(f"output_sr={meta.get('output_sr')}（端上只支持 {SAMPLE_RATE}）")
            need = ["model.json", "text2mel.ms"] + [f"mel2wav.t{t}.ms" for t in out["tiers"]]
            out["missing"] = [n for n in need if prefix + n not in names]
            if out["missing"]:
                out["problems"].append("缺必需文件：" + "、".join(out["missing"]))
            if not (meta.get("prompts") or {}):
                out["problems"].append("model.json 没有 prompts（端上没有 G2P，生成不了示范句）")
            out["dur_files"] = [n for n in names if n.startswith(prefix + "dur_") and n.endswith(".json")]
            out["has_readme"] = prefix + "README.txt" in names
            if not out["has_readme"]:
                out["problems"].append("缺 README.txt（不影响导入，但建议保留）")
            out["ok"] = not out["missing"] and not any(
                not x.startswith("缺 README.txt") for x in out["problems"])
            out["meta_summary"] = {
                "id": meta.get("id"), "speaker": meta.get("speaker"),
                "display": meta.get("display"), "language": meta.get("language"),
                "languages": meta.get("languages"), "output_sr": meta.get("output_sr"),
                "hop": meta.get("hop"), "add_blank": meta.get("add_blank"),
                "tiers": out["tiers"], "sid": meta.get("sid"), "n_speakers": meta.get("n_speakers"),
                "profile": meta.get("profile"), "symbols": len(meta.get("symbols") or []),
                "prompts": sorted((meta.get("prompts") or {}).keys()),
                "built_at": meta.get("built_at"), "source": meta.get("source"),
                "config": meta.get("config"), "checkpoint": meta.get("checkpoint"),
                "tool": meta.get("tool"),
            }
    except zipfile.BadZipFile as e:
        out["problems"].append(f"不是合法 zip：{e}")
    return out


def list_packages(output_dir=None) -> dict:
    d = _ensure_dir(Path(output_dir) if output_dir else default_output_dir())
    items = []
    try:
        zips = sorted(d.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        zips = []
    for p in zips[:60]:
        try:
            st = p.stat()
        except OSError:
            continue
        meta: dict = {}
        try:
            with zipfile.ZipFile(p) as z:
                name = next((n for n in z.namelist() if n.endswith("model.json")), None)
                if name:
                    meta = json.loads(z.read(name).decode("utf-8"))
        except Exception:  # noqa: BLE001
            meta = {}
        items.append({
            "name": p.name,
            "path": str(p),
            "size_bytes": st.st_size,
            "size_mb": round(st.st_size / 1e6, 1),
            "mtime": st.st_mtime,
            "mtime_text": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
            "model_id": meta.get("id") or "",
            "display": meta.get("display") or meta.get("speaker") or "",
            "tiers": meta.get("tiers") or [],
            "built_at": meta.get("built_at") or "",
        })
    return {"output_dir": str(d), "packages": items, "count": len(items)}


def reveal(path) -> dict:
    p = Path(path).expanduser()
    if not p.exists():
        raise OnDeviceError(f"路径不存在：{p}")
    if sys.platform.startswith("win"):
        try:
            subprocess.Popen(["explorer", f"/select,{p}"])
            return {"ok": True, "revealed": str(p)}
        except Exception as e:  # noqa: BLE001
            raise OnDeviceError(f"打开资源管理器失败：{type(e).__name__}: {e}") from e
    raise OnDeviceError("当前系统不支持「打开所在文件夹」")


# --------------------------------------------------------------------- 手机（hdc）

def find_hdc() -> Path | None:
    cands: list[Path] = []
    raw = _env("HDC")
    if raw:
        cands.append(Path(raw))
    which = shutil.which("hdc")
    if which:
        cands.append(Path(which))
    cands += [
        Path(r"C:\Program Files\Huawei\DevEco Studio\sdk\default\openharmony\toolchains\hdc.exe"),
        Path(r"C:\Program Files\Huawei\DevEco Studio\sdk\default\hms\toolchains\hdc.exe"),
    ]
    for c in cands:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


def hdc_info() -> dict:
    exe = find_hdc()
    info: dict = {"available": False, "path": str(exe) if exe else "",
                  "devices": [], "message": ""}
    if exe is None:
        info["message"] = "未检测到 hdc（HarmonyOS 设备工具）；用 DevEco Studio 的 hdc 或设环境变量 HDC"
        return info
    info["available"] = True
    try:
        p = subprocess.run([str(exe), "list", "targets"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=15)
        devices = [ln.strip() for ln in (p.stdout or "").splitlines()
                   if ln.strip() and "[Empty]" not in ln]
        info["devices"] = devices
        info["message"] = ("已连接：" + "、".join(devices)) if devices else "没有连接手机（USB 调试 / 无线调试）"
    except Exception as e:  # noqa: BLE001
        info["message"] = f"hdc 调用失败：{type(e).__name__}: {e}"
    return info


def push_to_device(zip_path, device: str | None = None) -> dict:
    p = Path(zip_path).expanduser()
    if not p.is_file():
        raise OnDeviceError(f"找不到要推送的 zip：{p}",
                            hint="先在桌面端导出，或从 /api/ondevice/packages 里选一个")
    exe = find_hdc()
    if exe is None:
        raise OnDeviceError("未检测到 hdc",
                            hint="安装 DevEco Studio（自带 hdc）或把 HDC 环境变量指向 hdc.exe")
    info = hdc_info()
    devices = info.get("devices") or []
    dev = (device or "").strip() or (devices[0] if devices else "")
    if not dev:
        raise OnDeviceError("没有连接手机",
                            hint="用 USB 线连接手机并打开「开发者模式 → USB 调试」，然后重试")
    target = DEVICE_DOWNLOAD + p.name
    t0 = time.time()
    p_run = subprocess.run([str(exe), "-t", dev, "file", "send", str(p), target],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600)
    out = ((p_run.stdout or "") + (p_run.stderr or "")).strip()
    ok = p_run.returncode == 0 and "error" not in out.lower()
    return {
        "ok": ok,
        "device": dev,
        "target": target,
        "seconds": round(time.time() - t0, 1),
        "output": out[-4000:],
        "hint": ("" if ok else "推送失败：确认手机已解锁、已授权调试，且 hdc 版本与系统匹配"),
    }


# --------------------------------------------------------------------- 状态

def status() -> dict:
    tools = tools_dir()
    exe = _converter_exe()
    try:
        wroot = work_root()
        work_ok = str(wroot).isascii()
    except Exception:  # noqa: BLE001
        wroot, work_ok = Path(""), False
    try:
        out_dir = _ensure_dir(default_output_dir())
        out_ok = True
    except OnDeviceError as e:
        out_dir, out_ok = default_output_dir(), False
        out_error = str(e)
    else:
        out_error = ""
    return {
        "ok": bool(_cli_path().is_file() and exe and work_ok and out_ok),
        "tool": {
            "cli": str(_cli_path()),
            "cli_ok": _cli_path().is_file(),
            "tools_dir": str(tools),
            "vits_root": str(vits_root()),
            "registry": str(registry_path()),
            "registry_ok": registry_path().is_file(),
            "converter": str(exe or converter_dir()),
            "converter_ok": bool(exe),
        },
        "work_dir": str(wroot),
        "work_ascii": work_ok,
        "output_dir": str(out_dir),
        "output_ok": out_ok,
        "output_error": out_error,
        "tiers": list(TIERS),
        "sample_rate": SAMPLE_RATE,
        "format": PACK_FORMAT,
        "device_download": DEVICE_DOWNLOAD,
        "hdc": hdc_info(),
        "building": BUILD_LOCK.locked(),
    }
