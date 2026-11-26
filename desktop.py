"""VITS Studio 桌面应用（集成 EXE 版本）。

用户体验：双击 → 直接弹出应用窗口（无浏览器、无命令行、看不到 http 地址）。

实现：
- 用 pywebview 创建原生窗口（Windows 上是 Edge WebView2）；
- 后端 FastAPI 服务由本进程以**子进程**方式拉起（用模型环境里的 python），
  绑定 127.0.0.1 随机端口，只服务于这个窗口；
- 关窗口 → 关服务 → 退出。

打包：`build_exe.ps1` 把它打成 `dist/VITS Studio/VITS Studio.exe`。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import json

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent

# ---------- 打包后（PyInstaller onedir）与开发模式路径不同 ----------
if getattr(sys, "frozen", False):
    BUNDLE = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    RUNTIME_DIR = Path(sys.executable).parent          # 可写：exe 所在目录
else:
    BUNDLE = APP_DIR
    RUNTIME_DIR = APP_DIR

CONFIG = RUNTIME_DIR / "config.json"
_CFG = {}
if CONFIG.exists():
    try:
        _CFG = json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        _CFG = {}

PY = Path(os.environ.get("VITS_PYTHON", _CFG.get("python", r"<VITS_ENV>\venv-modern\Scripts\python.exe")))
SERVER = BUNDLE / "backend" / "server.py"
LOGS = RUNTIME_DIR / "logs"
MODELS = os.environ.get("VITS_MODELS", _CFG.get("models_dir", r"<PROJECT_ROOT>\models_out;<PROJECT_ROOT>\VITS标准模型;<PROJECT_ROOT>\community_models"))
HOST = "127.0.0.1"
TITLE = "VITS Studio"


def log(msg: str) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with (LOGS / "desktop.log").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def wait_health(port: int, timeout: float = 60.0) -> bool:
    url = f"http://{HOST}:{port}/api/health"
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


def fatal(msg: str) -> None:
    log("FATAL " + msg)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, f"{TITLE} 启动失败", 0x10)
    except Exception:  # noqa: BLE001
        pass


def start_server(port: int) -> subprocess.Popen:
    LOGS.mkdir(parents=True, exist_ok=True)
    out = (LOGS / "server.log").open("w", encoding="utf-8", errors="replace")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    # 「导出到手机」的 zip 落在 exe 同级 ondevice_out\（用户能找到；可用环境变量覆盖）
    env.setdefault("VITS_ONDEVICE_OUT", str(RUNTIME_DIR / "ondevice_out"))
    cmd = [str(PY), "-u", str(SERVER), "--port", str(port), "--models", str(MODELS)]
    log("spawn " + " ".join(cmd))
    return subprocess.Popen(
        cmd, cwd=str(APP_DIR), env=env, stdout=out, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


class HostApi:
    """暴露给网页的原生能力（pywebview js_api）。"""

    def pick_dir(self) -> str:
        try:
            import webview
            if webview.windows:
                res = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)
                if res:
                    return res[0] if isinstance(res, (list, tuple)) else str(res)
        except Exception as e:  # noqa: BLE001
            log(f"pick_dir 失败: {e}")
        return ""


def run_window(url: str) -> bool:
    """打开原生窗口；成功返回 True。"""
    try:
        import webview
    except Exception as e:  # noqa: BLE001
        log(f"pywebview 不可用: {type(e).__name__}: {e}")
        return False
    try:
        win = webview.create_window(
            TITLE, url, width=1180, height=820, min_size=(920, 640),
            background_color="#0b0d12", text_select=True, js_api=HostApi(),
        )
        webview.start(debug=False)
        del win
        return True
    except Exception as e:  # noqa: BLE001
        log(f"窗口启动失败: {type(e).__name__}: {e}")
        return False


def main() -> int:
    if not PY.exists():
        fatal(f"找不到模型环境：\n{PY}\n\n请在 app/CONTEXT.md 查看如何配置 VITS_PYTHON。")
        return 1
    if not SERVER.exists():
        fatal(f"找不到后端：{SERVER}")
        return 1
    if not any(Path(p).exists() for p in str(MODELS).split(";")):
        fatal(f"找不到模型目录：{MODELS}")
        return 1

    port = free_port()
    proc = start_server(port)
    if not wait_health(port, 90.0):
        proc.terminate()
        fatal(f"后端启动超时，详见：\n{LOGS / 'server.log'}")
        return 1

    url = f"http://{HOST}:{port}/"
    log(f"server ready: {url}")

    ok = run_window(url)
    if not ok:
        log("回退到系统浏览器")
        import webbrowser
        webbrowser.open(url)
        input("按回车退出（会关闭本地服务）…")

    # 窗口关闭 → 结束服务
    try:
        urllib.request.urlopen(f"{url}api/shutdown", data=b"", timeout=3)
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        pass
    log("bye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
