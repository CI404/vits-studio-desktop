"""VITS Studio 后端：给桌面端 / 鸿蒙 APP 共用的 HTTP API。

启动：
    python app/backend/server.py --port 7788 --models <PROJECT_ROOT>\\models_out

接口：
    GET  /api/health
    GET  /api/models                模型列表
    POST /api/models/reload         重新扫描模型目录
    POST /api/tts                   合成，返回 audio/wav
    GET  /api/tts/meta              上次合成的耗时等（调试用）
    POST /api/audio/save            把 WAV 保存到 下载/VITS Studio（桌面「下载 WAV」用）
    POST /api/audio/reveal          在资源管理器里定位保存的 WAV
    GET  /api/ondevice/*            导出到手机：打包任务 / 进度 / 包列表 / hdc 推送
    GET  /                          网页 UI
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

sys_dir = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(sys_dir))

from engine import Engine              # noqa: E402
from registry import discover            # noqa: E402
import installer                        # noqa: E402
import ondevice                         # noqa: E402

WEB_DIR = Path(__file__).resolve().parents[1] / "web"

app = FastAPI(title="VITS Studio", version="1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

STATE = {
    "models": [],
    "model_dir": None,
    "engine": None,
    "last": {},
    "last_wav": {},        # 最近一次 /api/tts 的 WAV（供 /api/audio/save 复用）
    "saved_wav": {},       # sha256 -> 已保存路径（同一段音频不重复写盘）
    "last_save": {},
    "started_at": time.time(),
}


class TTSRequest(BaseModel):
    model: str
    text: str
    lang: str | None = None
    speed: float = 1.0
    temperature: float = 1.0


def _refresh(model_dir) -> None:
    """model_dir 支持多个根目录（用 ; 或 , 分隔）。"""
    if isinstance(model_dir, (list, tuple)):
        roots = [str(p) for p in model_dir]
    else:
        roots = [p for p in str(model_dir).replace(",", ";").split(";") if p.strip()]
    STATE["model_dir"] = ";".join(roots)
    old = STATE.get("engine")
    if old is not None:
        try:
            old.unload()          # 先释放显存里的旧模型，避免 reload 后叠加占用
        except Exception:  # noqa: BLE001
            pass
    STATE["models"] = discover(roots)
    STATE["engine"] = Engine(device=STATE.get("device", "cuda"))
    print(f"[models] {len(STATE['models'])} 个:",
          [(m.id, m.engine_version, m.ready) for m in STATE["models"]], flush=True)


def _find(model_id: str):
    for m in STATE["models"]:
        if m.id == model_id or m.name == model_id:
            return m
    return None


@app.get("/api/health")
def health():
    engine = STATE.get("engine")
    return {
        "ok": True,
        "uptime_s": round(time.time() - STATE["started_at"]),
        "models": len(STATE["models"]),
        "model_dir": STATE["model_dir"],
        "gpu": _gpu_name(),
        "loaded_model": getattr(engine, "loaded_model", None),
        "loaded_engine_version": getattr(engine, "loaded_version", None),
    }


def _gpu_name() -> str:
    try:
        import torch
        return torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu"
    except Exception:  # noqa: BLE001
        return "unknown"


@app.get("/api/models")
def list_models():
    return {"models": [m.to_dict() for m in STATE["models"]]}


@app.post("/api/models/reload")
def reload_models():
    _refresh(STATE["model_dir"])
    return {"models": [m.to_dict() for m in STATE["models"]]}


class LoadReq(BaseModel):
    id: str


@app.post("/api/models/load")
def load_model(req: LoadReq):
    """只加载模型（不合成），用于诊断：返回声明版本 / 实际版本 / 加载耗时。"""
    info = _find(req.id)
    if info is None:
        raise HTTPException(404, f"未知模型: {req.id}")
    if not info.ready:
        why = "、".join(info.missing) if info.missing else "缺权重或参考音"
        raise HTTPException(400, f"模型 {info.id} 未就绪：{why}")
    engine: Engine = STATE["engine"]
    try:
        engine.load(info)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"加载失败: {type(e).__name__}: {e}")
    return {
        "ok": True,
        "id": info.id,
        "engine_version": info.engine_version,
        "sovits_version": info.sovits_version,
        "loaded_engine_version": getattr(engine, "loaded_version", None),
        "s1": getattr(engine, "loaded_s1", None),
        "load_seconds": getattr(engine, "last_load_seconds", None),
    }


@app.get("/api/tts/meta")
def tts_meta():
    return STATE["last"]


@app.post("/api/tts")
async def tts(req: TTSRequest):
    info = _find(req.model)
    if info is None:
        raise HTTPException(404, f"未知模型: {req.model}")
    if not info.ready:
        why = "、".join(info.missing) if info.missing else "缺权重或参考音"
        raise HTTPException(400, f"模型 {info.id} 未就绪：{why}")
    if not (req.text or "").strip():
        raise HTTPException(400, "文本为空")
    if len(req.text) > 500:
        raise HTTPException(400, "文本过长（>500 字），请分段")

    engine: Engine = STATE["engine"]
    loop = asyncio.get_running_loop()
    try:
        audio, sr, cost = await loop.run_in_executor(
            None, lambda: engine.synthesize(info, req.text, req.lang, req.speed,
                                            temperature=req.temperature)
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"合成失败: {type(e).__name__}: {e}")

    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
    data = buf.getvalue()
    STATE["last"] = {
        "model": info.id, "chars": len(req.text), "seconds": cost,
        "audio_s": round(len(audio) / sr, 2), "sr": sr, "at": time.time(),
        "engine_version": getattr(engine, "loaded_version", None),
    }
    # 留给 /api/audio/save：桌面端「下载 WAV」由后端写盘，不再依赖 WebView2 的下载通道
    STATE["last_wav"] = {
        "data": data, "model": info.id, "sr": sr,
        "audio_s": round(len(audio) / sr, 2), "at": time.time(),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    return Response(content=data, media_type="audio/wav", headers={
        "X-Audio-Seconds": str(round(len(audio) / sr, 2)),
        "X-Render-Seconds": str(cost),
        "X-Sample-Rate": str(sr),
        "X-Engine-Version": str(getattr(engine, "loaded_version", "") or ""),
        "X-Model-Id": info.id,
        "Content-Disposition": f'inline; filename="{info.id}.wav"',
    })


# ---------------- 下载音频（桌面端「下载 WAV」落盘） ----------------

class SaveAudioRequest(BaseModel):
    """POST /api/audio/save 的请求体。"""

    filename: str | None = None   # 期望文件名（可省略扩展名；不带目录）
    data: str | None = None       # base64 编码的 WAV；省略则保存服务端最近一次合成的音频
    model: str | None = None      # 生成默认文件名时用的模型 id


class RevealAudioRequest(BaseModel):
    """POST /api/audio/reveal 的请求体。"""

    path: str = ""


SAVE_LOCK = threading.Lock()


def _download_dir() -> Path:
    """音频落盘目录：`%USERPROFILE%\\Downloads\\VITS Studio`，可用 VITS_DOWNLOAD_DIR 覆盖。"""
    raw = os.environ.get("VITS_DOWNLOAD_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    return home / "Downloads" / "VITS Studio"


def _safe_wav_name(name: str | None, model_id: str) -> str:
    """清洗成安全的 .wav 文件名；空名用 `<模型id>_<时间戳>.wav`。"""
    raw = str(name or "").strip()
    if not raw:
        raw = f"{model_id or 'audio'}_{time.strftime('%Y%m%d_%H%M%S')}.wav"
    raw = Path(raw.replace("\\", "/")).name          # 丢掉任何目录部分
    raw = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", raw).strip(" .")
    if not raw:
        raw = f"audio_{time.strftime('%Y%m%d_%H%M%S')}.wav"
    if not raw.lower().endswith(".wav"):
        raw += ".wav"
    return (raw[:-4][:100] or "audio") + ".wav"


def _write_atomic(target: Path, data: bytes) -> None:
    """先写临时文件再 os.replace，避免留下半截 WAV。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".vits_", suffix=".part", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _saved_reply(path: Path, data: bytes, digest: str, duplicate: bool) -> dict:
    return {
        "ok": True,
        "path": str(path),
        "filename": path.name,
        "dir": str(path.parent),
        "bytes": len(data),
        "sha256": digest,
        "duplicate": duplicate,
    }


@app.post("/api/audio/save")
def save_audio(req: SaveAudioRequest):
    """把一段 WAV 保存到用户「下载\\VITS Studio」目录，返回绝对路径。

    - 不传 `data`：保存服务端**最近一次** `/api/tts` 的音频（桌面 UI 就这么用）；
    - 同一段音频（SHA256 相同）重复保存只返回已有路径，不产生重复文件；
    - 磁盘/权限失败返回 500，`detail` 带目标路径与原因，前端必须展示。
    """
    if req.data:
        try:
            raw = base64.b64decode(req.data, validate=True)
        except Exception:  # noqa: BLE001
            raise HTTPException(400, "data 不是合法的 base64")
        model_id = req.model or "audio"
    else:
        last = STATE.get("last_wav") or {}
        raw = last.get("data") or b""
        model_id = req.model or last.get("model") or "audio"
    if not raw:
        raise HTTPException(404, "服务端还没有可保存的音频，请先合成一句再下载")
    if len(raw) < 44 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise HTTPException(400, "不是合法的 WAV 数据（缺少 RIFF/WAVE 文件头）")

    digest = hashlib.sha256(raw).hexdigest()
    outdir = _download_dir()
    with SAVE_LOCK:
        known = STATE["saved_wav"].get(digest)
        if known:
            p = Path(known)
            try:
                same = p.is_file() and p.stat().st_size == len(raw)
            except OSError:
                same = False
            if same:
                return _saved_reply(p, raw, digest, True)

        target = outdir / _safe_wav_name(req.filename, model_id)
        if target.exists():
            # 缓存丢了（例如后端重启）但同内容已在磁盘：仍按重复处理，不重复写
            try:
                if (target.stat().st_size == len(raw)
                        and hashlib.sha256(target.read_bytes()).hexdigest() == digest):
                    STATE["saved_wav"][digest] = str(target)
                    return _saved_reply(target, raw, digest, True)
            except OSError:
                pass
            # 同名但内容不同：加序号，绝不覆盖
            stem, suffix = target.stem, target.suffix
            for i in range(1, 1000):
                cand = target.with_name(f"{stem}_{i}{suffix}")
                if not cand.exists():
                    target = cand
                    break
            else:
                raise HTTPException(409, f"同名文件过多，无法保存：{target}")

        try:
            outdir.mkdir(parents=True, exist_ok=True)
            _write_atomic(target, raw)
        except OSError as e:
            raise HTTPException(
                500,
                f"保存失败（目录不可写或磁盘已满）：{target}：{type(e).__name__}: {e}",
            )
        STATE["saved_wav"][digest] = str(target)
        STATE["last_save"] = {"path": str(target), "bytes": len(raw),
                              "sha256": digest, "at": time.time()}
    print(f"[save] {target} {len(raw)} B sha256={digest[:12]}", flush=True)
    return _saved_reply(target, raw, digest, False)


@app.post("/api/audio/reveal")
def reveal_audio(req: RevealAudioRequest):
    """在资源管理器里定位刚保存的 WAV（只允许下载目录内的文件）。"""
    raw = (req.path or "").strip()
    if not raw:
        raw = str((STATE.get("last_save") or {}).get("path") or "")
    if not raw:
        raise HTTPException(404, "还没有保存过音频")
    p = Path(raw).expanduser()
    outdir = _download_dir().expanduser()
    try:
        p_resolved = p.resolve()
        inside = p_resolved.is_relative_to(outdir.resolve())
    except OSError:
        p_resolved, inside = p, False
    if not inside:
        raise HTTPException(400, f"只能打开下载目录里的文件：{outdir}")
    if not p_resolved.is_file():
        raise HTTPException(404, f"文件不存在：{p_resolved}")
    try:
        if sys.platform.startswith("win"):
            # 只给路径加引号：若让 Python 把整个 "/select,<路径>" 参数加引号，
            # Explorer 的 shell 解析会失败并打开「文档」等无关目录（实测）。
            subprocess.Popen(f'explorer /select,"{p_resolved}"')
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(p_resolved)])
        else:
            subprocess.Popen(["xdg-open", str(p_resolved.parent)])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"打开文件夹失败：{type(e).__name__}: {e}")
    print(f"[reveal] {p_resolved}", flush=True)
    return {"ok": True, "revealed": str(p_resolved), "dir": str(p_resolved.parent)}


@app.get("/")
def index():
    p = WEB_DIR / "index.html"
    if p.exists():
        return FileResponse(p)
    return {"hint": "web/index.html 不存在"}


# ---------------- 添加模型 ----------------

class ScanReq(BaseModel):
    path: str


class InstallReq(BaseModel):
    source_dir: str = ""
    s2: str
    s1: str
    ref_wav: str = ""
    ref_txt: str = ""
    ref_text: str = ""
    name: str = ""
    speaker: str = ""
    language: str = "ja"
    mode: str = "copy"          # copy | register


class HFReq(BaseModel):
    repo: str
    name: str = ""
    speaker: str = ""
    language: str = "ja"
    s2: str = ""
    s1: str = ""
    ref_text: str = ""


class RemoveReq(BaseModel):
    id: str


@app.post("/api/models/scan")
def scan_model_dir(req: ScanReq):
    try:
        return installer.scan_source(req.path)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))


@app.post("/api/models/install")
def install_model(req: InstallReq):
    try:
        res = installer.install_local(req.source_dir, req.s2, req.s1, req.ref_wav, req.ref_txt,
                                      req.name, req.speaker, req.language, req.mode,
                                      req.ref_text)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))
    _refresh(STATE["model_dir"])
    return {**res, "models": [m.to_dict() for m in STATE["models"]]}


@app.post("/api/models/import_hf")
def import_hf(req: HFReq):
    if not req.repo or "/" not in req.repo:
        raise HTTPException(400, "repo 需要形如 owner/name")
    job = installer.start_hf_import(req.repo, req.name, req.speaker, req.language,
                                    req.s2, req.s1, ref_text=req.ref_text)
    return {"job": job}


@app.get("/api/models/import_hf/{job_id}")
def import_hf_status(job_id: str):
    st = installer.job_status(job_id)
    if st.get("status") == "done":
        _refresh(STATE["model_dir"])
        st["models"] = [m.to_dict() for m in STATE["models"]]
    return st


@app.post("/api/models/remove")
def remove_model(req: RemoveReq):
    """把模型目录移到 _removed（不直接删除，安全可恢复）。"""
    target = None
    for m in STATE["models"]:
        if m.id == req.id:
            target = m.folder
            break
    if target is None or not target.exists():
        raise HTTPException(404, f"未知模型: {req.id}")
    trash = Path(STATE["model_dir"].split(";")[0]).parent / "_removed"
    trash.mkdir(parents=True, exist_ok=True)
    dest = trash / f"{target.name}_{int(time.time())}"
    target.rename(dest)
    _refresh(STATE["model_dir"])
    return {"ok": True, "moved_to": str(dest),
            "models": [m.to_dict() for m in STATE["models"]]}


# ---------------- 导出到手机（鸿蒙端模型包） ----------------

class OnDeviceBuildReq(BaseModel):
    """POST /api/ondevice/build 的请求体。

    model_id 取 `/api/ondevice/sources` 里的 id（如 `registry:xya` / `dir:inference-HK416`），
    也可以直接给经典 VITS 目录路径。
    """
    model_config = ConfigDict(protected_namespaces=())

    model_id: str
    tiers: list[int] | None = None
    output_dir: str | None = None
    display: str | None = None
    speaker: str | None = None
    language: str | None = None
    keep_work: bool = False
    no_fp16: bool = False


class OnDevicePathReq(BaseModel):
    path: str = ""
    device: str = ""


class OnDeviceInstallReq(BaseModel):
    zip_path: str = ""
    name: str = ""
    device: str = ""


def _ondevice_error(e: Exception) -> HTTPException:
    detail = str(e)
    hint = getattr(e, "hint", "") or ""
    if hint:
        detail = f"{detail}（建议：{hint}）"
    return HTTPException(400, detail)


def _app_models_with_folder() -> list[dict]:
    """给 ondevice 用：to_dict() 不含磁盘目录，这里补上 folder 便于判断来源。"""
    out = []
    for m in STATE["models"]:
        d = m.to_dict()
        d["folder"] = str(m.folder)
        out.append(d)
    return out


@app.get("/api/ondevice/status")
def ondevice_status():
    """工具链 / 转换器 / 产物目录 / hdc 是否就绪。"""
    return ondevice.status()


@app.get("/api/ondevice/sources")
def ondevice_sources():
    """哪些模型能导出到手机（经典 VITS 22.05 kHz），哪些不能以及原因。"""
    try:
        return ondevice.list_sources(_app_models_with_folder())
    except ondevice.OnDeviceError as e:
        raise _ondevice_error(e)


@app.get("/api/ondevice/build")
def ondevice_build_list(limit: int = 20):
    return ondevice.list_jobs(limit)


@app.post("/api/ondevice/build")
def ondevice_build(req: OnDeviceBuildReq):
    """创建后台打包任务：立即返回 job（含 id），用 GET .../build/{id} 轮询进度。"""
    try:
        return ondevice.start_build(
            req.model_id, tiers=req.tiers, output_dir=req.output_dir,
            display=req.display, speaker=req.speaker, language=req.language,
            keep_work=req.keep_work, no_fp16=req.no_fp16)
    except ondevice.OnDeviceError as e:
        raise _ondevice_error(e)


@app.get("/api/ondevice/build/{job_id}")
def ondevice_build_status(job_id: str):
    """打包进度：阶段（export_onnx/convert_text2mel/convert_mel2wav/pack）、日志尾部、产物。"""
    try:
        return ondevice.job_snapshot(job_id)
    except ondevice.OnDeviceError as e:
        raise HTTPException(404, str(e))


@app.get("/api/ondevice/packages")
def ondevice_packages(output_dir: str | None = None):
    """已生成的端上包（zip）列表：大小 / 时间 / 档位。"""
    try:
        return ondevice.list_packages(output_dir)
    except ondevice.OnDeviceError as e:
        raise _ondevice_error(e)


@app.post("/api/ondevice/inspect")
def ondevice_inspect(req: OnDevicePathReq):
    """结构化自检一个 zip（条目、必需文件、model.json 摘要）。"""
    return ondevice.inspect_zip(req.path)


@app.post("/api/ondevice/reveal")
def ondevice_reveal(req: OnDevicePathReq):
    """在资源管理器里定位产物（桌面 EXE 用）。"""
    try:
        return ondevice.reveal(req.path)
    except ondevice.OnDeviceError as e:
        raise _ondevice_error(e)


@app.get("/api/ondevice/devices")
def ondevice_devices():
    """检测 hdc 与已连接的鸿蒙设备。"""
    return ondevice.hdc_info()


@app.post("/api/ondevice/install")
def ondevice_install(req: OnDeviceInstallReq):
    """把 zip 推到手机的 Download 目录（hdc file send，设备侧再走 App 的导入）。"""
    target = req.zip_path
    if not target and req.name:
        target = str(ondevice.default_output_dir() / req.name)
    if not target:
        raise HTTPException(400, "要给 zip_path 或 name")
    try:
        return ondevice.push_to_device(target, req.device or None)
    except ondevice.OnDeviceError as e:
        raise _ondevice_error(e)


@app.post("/api/shutdown")
def shutdown():
    """桌面端「退出应用」调用：优雅结束进程（仅本机调用，无鉴权，绑定 127.0.0.1）。"""
    import os
    import threading

    def _die():
        time.sleep(0.3)
        os._exit(0)

    threading.Thread(target=_die, daemon=True).start()
    return {"ok": True, "message": "server shutting down"}


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7788)
    p.add_argument("--models", default=r"<PROJECT_ROOT>\models_out;<PROJECT_ROOT>\VITS标准模型;<PROJECT_ROOT>\community_models")
    p.add_argument("--device", default="cuda")
    p.add_argument("--preload", action="store_true", help="启动时先加载第一个模型")
    args = p.parse_args()

    STATE["device"] = args.device
    _refresh(args.models)
    if args.preload and STATE["models"]:
        try:
            STATE["engine"].load(STATE["models"][0])
            print("[engine] 预加载完成", STATE["models"][0].id, flush=True)
        except Exception as e:  # noqa: BLE001
            print("[engine] 预加载失败:", e, flush=True)

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    raise SystemExit(main())
