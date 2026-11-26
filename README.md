# VITS Studio · 桌面端

**本地 GPT-SoVITS（v1 / v2 / v2Pro / v2ProPlus）角色模型的 Windows 桌面应用。**
双击 → 原生窗口 → 输入文字 → 生成 WAV。不联网、不出现命令行、不出现浏览器地址栏。

> 这是父仓库 `vits-studio` 的子模块。总览、模型放哪里、许可说明见父仓库 README 与 `NOTICE.md`。
> **本仓库不含任何模型权重。**

---

## 1. 架构

```text
        用户双击 VITS Studio.exe
                  │
                  ▼
┌───────────────────────────────────────────────────────────────┐
│  VITS Studio.exe  (desktop.py, PyInstaller onedir 启动器)     │
│                                                               │
│   ┌──────────────────────┐     HTTP      ┌─────────────────┐  │
│   │ pywebview 原生窗口    │ ◄───────────► │ 后端子进程       │  │
│   │ 加载 web/ (SPA)      │  JSON / WAV   │ FastAPI         │  │
│   └──────────────────────┘               │ backend/server  │  │
│                                          └────────┬────────┘  │
└───────────────────────────────────────────────────┼───────────┘
                                                    │ 串行函数调用
                                                    ▼
                                          ┌──────────────────┐
                                          │ Engine           │
                                          │ 单模型驻留 + 加锁 │
                                          └────────┬─────────┘
                                                   ▼
                                        ┌──────────────────────┐
                                        │ GPT-SoVITS 权重       │
                                        │ v1/v2/v2Pro/v2ProPlus│
                                        │ <MODELS_DIR>/<id>/   │
                                        └──────────────────────┘

  鸿蒙 App ── 局域网 HTTP（同一套 API）──► 同一个 FastAPI
```

一句话链条：**Web UI (`web/`) ←→ FastAPI (`backend/`) ←→ Engine → GPT-SoVITS 权重**。

- 桌面 EXE 只是**启动器**：拉起 pywebview 窗口（Windows 上走 Edge WebView2），并在**子进程**里隐藏运行
  FastAPI，绑定 `127.0.0.1:<随机端口>`；窗口一关，服务一起退出（会调 `/api/shutdown`）。
- HTTP API 依然存在，两个消费者：① 窗口里的 UI；② 鸿蒙 App（手机经局域网访问）。
  完整契约见 [`docs/API.md`](docs/API.md)。
- **GPU 策略**：12 GB 显存同一时刻只驻留一个模型，推理串行排队；换模型要重载（约 15 s）。
- **运行依赖不在 EXE 里**：EXE 不含 Python / torch / GPT-SoVITS 仓库 / 模型权重。

---

## 2. 目录结构

```text
vits-studio-desktop/
├── backend/
│   ├── server.py          HTTP 路由：/api/health、/api/models*、/api/tts、/api/audio/*、/api/ondevice/*
│   ├── engine.py          推理引擎：串行加锁、单模型驻留、调 GPT-SoVITS
│   ├── registry.py        模型发现：扫描模型目录、读写 model.json、识别版本
│   ├── installer.py       模型安装 / 删除
│   ├── ondevice.py        「导出到手机」：生成鸿蒙端上模型包
│   └── _stubs/            无 GUI / 缺重依赖时的可选依赖桩
├── web/
│   ├── index.html         页面结构
│   ├── style.css          样式
│   └── app.js             交互：模型卡片、合成、历史、播放、下载、端上导出
├── docs/
│   ├── API.md             REST API 规格（含 curl / fetch / ArkTS 示例）
│   ├── ADD_MODEL.md       新增模型指南（含社区包与零样本）
│   ├── APP_README.md      用户向说明（功能、常见问题、打包）
│   └── CONTEXT.md         开发者交接（架构、端点、引擎坑）
├── tools/
│   ├── install_community_model.py   命令行装社区模型
│   └── ondevice_ui_smoke.mjs        端上导出面板 UI 冒烟
├── desktop.py             桌面启动器
├── build_exe.ps1          PyInstaller 打包脚本
└── config.example.json    config.json 样例
```

> **关于 `docs/` 里四份历史开发文档的相对链接**
> `API.md`、`ADD_MODEL.md`、`APP_README.md`、`CONTEXT.md` 是从内部工作区**原样搬来**的开发文档
> （仅做了路径脱敏），因此其中部分相对链接指向**未包含在本仓库**的内容，例如：
> 旧 ArkTS 骨架 `app/harmony/`、`models_out/` 模型清单、`trainer/`、`docs/evidence/` 截图。
> 这些链接点开会 404，**属于预期**；正文里的接口契约、参数与命令是准确的。

---

## 3. 运行

### 3.1 前提（一次性）

| 依赖 | 说明 |
|---|---|
| **Python 3.10+ 环境** | 需装 `torch`(CUDA)、`fastapi`、`uvicorn`、`soundfile`、`pywebview`；路径用环境变量 `VITS_PYTHON` 指定 |
| **GPT-SoVITS 仓库** | v2Pro 代码 + `pretrained_models/`（底模） |
| **模型目录** | 形如 `<MODELS_DIR>/<model_id>/{model.json, *.pth, *.ckpt, ref.wav, ref.txt}` |
| **显卡** | 建议 ≥ 8 GB 显存；CPU 也能跑但很慢 |
| Windows | Windows 10/11 + WebView2 运行时（Win11 自带） |

### 3.2 两个必填环境变量

所有默认路径都是**脱敏占位符**（`<VITS_ENV>` / `<PROJECT_ROOT>` / `<USER>` 等），
第一次跑请显式指定：

```powershell
# 模型环境里的 python
$env:VITS_PYTHON  = '<VITS_ENV>\venv-modern\Scripts\python.exe'
# 模型目录（`;` 分隔多个）
$env:VITS_MODELS  = '<MODELS_DIR>\models_out;<MODELS_DIR>\VITS标准模型'
```

或者把它们写进 exe / 仓库同级目录的 `config.json`（结构见 `config.example.json`）：

```json
{
  "python": "<VITS_ENV>\\venv-modern\\Scripts\\python.exe",
  "models_dir": "<MODELS_DIR>\\models_out"
}
```

> `backend/*.py` 与 `desktop.py` 里出现的 `<VITS_ENV>` / `<PROJECT_ROOT>` 就是原开发机上的绝对路径，
> 被批量替换成了占位符。**不设置上面两个变量时程序会以「找不到模型环境」退出**，这是预期行为。

### 3.3 场景 A：双击使用（推荐）

```powershell
# 1) 打包（一次即可）
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1

# 2) 双击
.\dist\VITS Studio\VITS Studio.exe
```

窗口出现（首次 5–15 s，后端在后台启动；启动器最多等 90 s，超时弹错误框；日志写在 exe 同级 `logs\`）。
左侧选角色 → 输入文本（≤500 字）→ 选语言（`ja` / `zh` / `en`）→「生成语音」→ 播放 / 下载 WAV。
**关窗即退出**，后台服务一并结束。

> 「下载 WAV」保存到 `%USERPROFILE%\Downloads\VITS Studio\<模型id>_<时间戳>.wav`；
> 同一段音频再点一次不会重复写盘，只提示「文件已存在」；保存失败会弹红色错误，不静默。

### 3.4 场景 B：只起后端（调试 / 手机联调）

```powershell
# 固定端口，只本机可见
& $env:VITS_PYTHON -u .\backend\server.py --port 7788 --models "$env:VITS_MODELS"

# 浏览器调试
Start-Process http://127.0.0.1:7788

# 停止
curl.exe -s -X POST http://127.0.0.1:7788/api/shutdown
```

常用参数：

```text
--port 7788            端口（默认 7788）
--host 127.0.0.1       监听地址（手机联调要 0.0.0.0）
--models "<DIR>"       模型目录（`;` 分隔）
--device cuda|cpu      推理设备
--preload              启动即加载第一个模型
```

> ⚠️ 不要同时开 EXE 和手工后端：两个进程各占一份显存，12 GB 会 OOM。

### 3.5 场景 C：鸿蒙 App 联调

1. 电脑上绑定所有网卡、固定端口起后端（`--host 0.0.0.0 --port 7788`）；
2. 手机与电脑同一局域网，App 里填 `http://<电脑局域网 IP>:7788`；
3. 防火墙放行该端口。

完整步骤见父仓库 `vits-studio-harmony/docs/DEPLOY.md`。

---

## 4. 已验证能力（2026-11-26 实测，12 GB 显存）

| 项 | 结果 |
|---|---|
| 三模型（HK416 / Tong / xya）零样本合成 | ✅ 32 kHz WAV |
| 首次合成 | 约 15 s 加载 + 5.25 s 推理（12 字） |
| 之后每句 | 1–5 s |
| 模型切换 | 约 15 s 重载；同一时刻只驻留一个 |
| 生成 / 播放 / 下载 / 历史 | ✅ |
| 「导出到手机」端上模型包 | ✅（`/api/ondevice/*`） |
| 社区模型（v1 / v2Pro / v2ProPlus）加载 | ✅ 见 `docs/ADD_MODEL.md` |
| 全程不出网 | ✅ 绑定 127.0.0.1 |

---

## 5. 打包（`build_exe.ps1`）

PyInstaller **onedir**（不是 onefile —— onefile 每次启动都要解压，慢且杀软误报多）：

- 入口 `desktop.py`；`datas` 带上 `backend/` 与 `web/`；
- `hiddenimports` 收集全部 `webview` 子模块 + `webview.platforms.edgechromium`；
- 产物：`dist/VITS Studio/VITS Studio.exe` + `_internal/`（**必须与 exe 放在一起**）。

```powershell
# 干净重建（先确认没有 VITS Studio.exe 在跑）
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1 -Clean
```

> 需要 `pip install pyinstaller pywebview`，并保证 `VITS_PYTHON` 指向的环境能 import 到它们。

---

## 6. 限制

1. **不含任何模型权重**（见父仓库 `NOTICE.md`）——克隆下来没有权重不会出声。
2. **只验证了 Windows**：依赖 `ctypes.windll`、`CREATE_NO_WINDOW`、Edge WebView2。
3. EXE 不含 Python 运行时与 torch；目标机需要能用的 `VITS_PYTHON` 环境。
4. pywebview 不可用时会退回「系统浏览器打开」——功能一样，只是有浏览器外框。
5. 中文文本前端依赖 jieba / opencc 等可选依赖；缺依赖时用 `backend/_stubs/` 里的桩，效果会退化。
6. 单句文本上限 500 字（UI 限制）；更长请自行分句。
7. 文档中的 `<...>` 均为脱敏占位符，替换成你自己的路径 / 账号。

---

## 7. 许可

代码 MIT（见仓库根 `LICENSE`）。模型权重与角色音色不属于本仓库，权利归各自所有者，仅个人研究用途。
