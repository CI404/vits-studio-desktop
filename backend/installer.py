"""模型安装器：扫描本地目录 / 从 HuggingFace 下载，并把模型装进模型库。

供两种调用方式：
  - 桌面 UI（server.py 的 /api/models/scan、/api/models/install、/api/models/import_hf）
  - 命令行（app/tools/install_community_model.py）

约定：一个模型 = 一个目录，含 sovits 权重、gpt 权重、ref.wav、ref.txt、model.json。
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from registry import detect_sovits_version

HF_BASE = "https://hf-mirror.com"
LIBRARY = Path(r"<PROJECT_ROOT>\community_models")
WEIGHT_EXT = {".pth", ".pt", ".ckpt"}
JOB_LOCK = threading.Lock()
JOBS: dict[str, dict] = {}


def _safe_name(name: str) -> str:
    name = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", (name or "").strip())
    return name.strip("_") or f"model_{int(time.time())}"


def scan_source(path_str: str) -> dict:
    """扫描一个目录，列出可用的权重/参考音/文本，并给出推荐选择。"""
    root = Path(path_str).expanduser()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"目录不存在：{root}")

    weights, wavs, texts = [], [], []
    for f in sorted(root.rglob("*")):
        if not f.is_file():
            continue
        size = f.stat().st_size
        ext = f.suffix.lower()
        if ext in WEIGHT_EXT and size >= 1024:
            is_gpt = ext == ".ckpt" or "gpt" in f.name.lower() or f.name.lower().startswith("s1")
            weights.append({
                "path": str(f),
                "name": f.name,
                "mb": round(size / 1e6, 1),
                "kind": "gpt" if is_gpt else "sovits",
                # 只有 SoVITS 权重才有版本头（.ckpt 是 GPT，不参与版本判定）
                "version": "" if is_gpt else detect_sovits_version(f),
            })
        elif ext == ".wav" and size >= 1024:
            wavs.append({"path": str(f), "name": f.name,
                         "sec": round(_wav_seconds(f), 2)})
        elif ext in (".txt", ".lab", ".list") and size >= 2:
            body = _read_text(f)
            texts.append({"path": str(f), "name": f.name,
                          "preview": body[:80], "text": body[:500]})
    if not weights:
        raise ValueError("这个目录里没有 .pth/.ckpt 权重")

    sovits = [w for w in weights if w["kind"] == "sovits"]
    sovits_known = [w for w in sovits if w["version"]]
    sovits.sort(key=lambda x: -x["mb"])
    sovits_known.sort(key=lambda x: -x["mb"])
    gpts = sorted([w for w in weights if w["kind"] == "gpt"], key=lambda x: -x["mb"])
    best_s2 = (sovits_known or sovits or weights)[0]
    best_s1 = (gpts or [w for w in weights if w is not best_s2])[0]
    return {
        "root": str(root),
        "weights": weights,
        "wavs": wavs,
        "texts": texts,
        "suggest": {
            "s2": best_s2["path"],
            "s1": best_s1["path"],
            "ref_wav": wavs[0]["path"] if wavs else "",
            "ref_txt": texts[0]["path"] if texts else "",
        },
    }


def _wav_seconds(p: Path) -> float:
    try:
        import soundfile as sf
        info = sf.info(str(p))
        return info.frames / float(info.samplerate or 1)
    except Exception:  # noqa: BLE001
        return 0.0


def _read_text(p: Path) -> str:
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return p.read_text(encoding=enc).strip()
        except Exception:  # noqa: BLE001
            continue
    return ""


def install_local(source_dir: str, s2: str, s1: str, ref_wav: str, ref_txt: str,
                  name: str, speaker: str, language: str, mode: str = "copy",
                  ref_text_override: str = "") -> dict:
    """把已有权重安装进模型库。

    mode="copy"     拷贝文件进 community_models/<name>/（推荐，自包含）
    mode="register" 只写 model.json 指向原路径（零拷贝，适合大模型）
    """
    src_dir = Path(source_dir).expanduser()
    s2p, s1p = Path(s2), Path(s1)
    if not s2p.exists():
        raise ValueError(f"找不到 s2 权重：{s2p}")
    if not s1p.exists():
        raise ValueError(f"找不到 s1 权重：{s1p}")
    if not ref_wav or not Path(ref_wav).exists():
        raise ValueError("必须提供参考音 ref.wav")
    version = detect_sovits_version(s2p)
    if not version:
        version = "v2Pro"

    fname = _safe_name(name or speaker or "model")
    dest = LIBRARY / fname
    if dest.exists() and (dest / "model.json").exists():
        raise ValueError(f"模型已存在：{fname}（改个名字或先移除）")

    if mode == "copy":
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(s2p, dest / s2p.name)
        shutil.copyfile(s1p, dest / s1p.name)
        shutil.copyfile(ref_wav, dest / "ref.wav")
        s2_name, s1_name = s2p.name, s1p.name
    else:
        dest.mkdir(parents=True, exist_ok=True)
        s2_name, s1_name = str(s2p.resolve()), str(s1p.resolve())
        shutil.copyfile(ref_wav, dest / "ref.wav")

    text = (ref_text_override or "").strip()
    if not text and ref_txt and Path(ref_txt).exists():
        text = _read_text(Path(ref_txt))
    if not text:
        raise ValueError("必须提供参考音对应的文本（ref.txt 内容）")
    (dest / "ref.txt").write_text(text, encoding="utf-8")

    meta = {
        "speaker": speaker or fname,
        "name": speaker or fname,
        "language": (language or "ja")[:2],
        "ref_lang": (language or "ja")[:2],
        "finetuned": "s1+s2",
        "engine_version": version,
        "sovits_version": version,
        "version": "local-1",
        "engine": "GPT-SoVITS",
        "output_sr": 32000,
        "sovits_weights": s2_name,
        "gpt_weights": s1_name,
        "ref_audio": "ref.wav",
        "ref_text": "ref.txt",
        "note": f"本地安装，来源 {src_dir}",
    }
    (dest / "model.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"id": fname, "folder": str(dest), "sovits_version": version,
            "engine_version": version, "mode": mode}


# ---------------- HuggingFace 下载 ----------------

def _api(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def _is_sovits_weight(path: str) -> bool:
    p = path.lower()
    name = Path(p).name
    if not p.endswith((".pth", ".pt")):
        return False
    if "log" in p or "/logs/" in p or name.startswith(("g_", "d_", "disc")):
        return False
    return "sovits" in p or "/s2" in p or "_s2" in p or "s2g" in name


def _is_gpt_weight(path: str) -> bool:
    p = path.lower()
    if not p.endswith((".ckpt", ".pt", ".pth")):
        return False
    if "log" in p or "/logs/" in p:
        return False
    return "gpt" in p or p.endswith(".ckpt")


def hf_list_files(repo: str) -> list[dict]:
    info = _api(f"{HF_BASE}/api/models/{repo}?blobs=true")
    out = []
    for s in info.get("siblings", []):
        fn = s["rfilename"]
        if fn.lower().endswith((".pth", ".ckpt", ".pt")):
            out.append({"path": fn, "mb": round((s.get("size") or 0) / 1e6, 1)})
    return out


def hf_pick_weights(files: list[dict]) -> tuple[str, str]:
    """从仓库文件里挑出 s2(SoVITS) 与 s1(GPT)。"""
    sov = [f for f in files if _is_sovits_weight(f["path"])]
    gpt = [f for f in files if _is_gpt_weight(f["path"])]
    if not sov:   # 兜底：最大的非 GPT 权重
        sov = [f for f in files if not _is_gpt_weight(f["path"])] or files
    if not gpt:   # 兜底：最大的 .ckpt
        gpt = [f for f in files if f["path"].lower().endswith(".ckpt")] or files
    s2 = max(sov, key=lambda f: f["mb"])["path"]
    s1 = max(gpt, key=lambda f: f["mb"])["path"]
    return s2, s1


def start_hf_import(repo: str, name: str = "", speaker: str = "", language: str = "ja",
                    s2: str = "", s1: str = "", ref_wav: str = "",
                    ref_text: str = "") -> str:
    job_id = f"{int(time.time() * 1000)}"
    with JOB_LOCK:
        JOBS[job_id] = {"id": job_id, "repo": repo, "status": "running", "progress": 0.0,
                        "message": "准备中", "model": None, "error": None}
    threading.Thread(target=_hf_worker, daemon=True,
                     args=(job_id, repo, name, speaker, language, s2, s1, ref_wav, ref_text)).start()
    return job_id


def job_status(job_id: str) -> dict:
    return JOBS.get(job_id) or {"id": job_id, "status": "unknown"}


def _hf_worker(job_id, repo, name, speaker, language, s2, s1, ref_wav, ref_text):
    job = JOBS[job_id]

    def upd(pct: float, msg: str):
        job["progress"] = round(pct, 3)
        job["message"] = msg

    try:
        files = hf_list_files(repo)
        if not files:
            raise ValueError("仓库里没有权重文件")
        if not s2 or not s1:
            auto_s2, auto_s1 = hf_pick_weights(files)
            s2 = s2 or auto_s2
            s1 = s1 or auto_s1
        total = sum(f["mb"] for f in files if f["path"] in (s2, s1)) or 1
        upd(0.01, f"下载 {Path(s2).name}")

        fname = _safe_name(name or speaker or repo.split("/")[-1].replace("GPT-SoVITS-", "").lower())
        dest = LIBRARY / fname
        dest.mkdir(parents=True, exist_ok=True)

        done = 0.0
        for remote in (s2, s1):
            local = dest / Path(remote).name
            if local.exists() and local.stat().st_size > 1e6:
                done += local.stat().st_size / 1e6
                continue
            _download(repo, remote, local,
                      lambda got, tot: upd((done + got / 1e6) / total, f"下载 {local.name}"))
            done += local.stat().st_size / 1e6

        version = detect_sovits_version(dest / Path(s2).name) or "v2Pro"
        upd(0.98, "写配置")
        if not ref_wav:
            lend = Path(r"<PROJECT_ROOT>\models_out\Tong\ref.wav")
            if lend.exists():
                shutil.copyfile(lend, dest / "ref.wav")
                ref_text = ref_text or _read_text(lend.with_suffix(".txt"))
        else:
            shutil.copyfile(ref_wav, dest / "ref.wav")
        (dest / "ref.txt").write_text(ref_text or "こんにちは。", encoding="utf-8")

        meta = {
            "speaker": speaker or fname, "name": speaker or fname,
            "language": (language or "ja")[:2], "ref_lang": "ja",
            "finetuned": "s1+s2", "engine_version": version, "sovits_version": version,
            "version": "community-1",
            "engine": "GPT-SoVITS", "output_sr": 32000,
            "sovits_weights": Path(s2).name, "gpt_weights": Path(s1).name,
            "ref_audio": "ref.wav", "ref_text": "ref.txt",
            "note": f"社区开源模型 {repo}",
        }
        (dest / "model.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        job["model"] = {"id": fname, "sovits_version": version,
                        "engine_version": version, "folder": str(dest)}
        upd(1.0, "完成")
        job["status"] = "done"
    except Exception as e:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = f"{type(e).__name__}: {e}"


def _download(repo: str, remote: str, local: Path, on_progress=None) -> None:
    url = f"{HF_BASE}/{repo}/resolve/main/{urllib.parse.quote(remote)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=300) as r, local.open("wb") as fh:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        last = 0.0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            fh.write(chunk)
            got += len(chunk)
            if on_progress and total and time.time() - last > 0.5:
                last = time.time()
                on_progress(got, total)
    if on_progress and total:
        on_progress(total, total)
