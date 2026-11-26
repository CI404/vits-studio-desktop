# VITS Studio

**本地 GPT-SoVITS（v1 / v2 / v2Pro / v2ProPlus）角色模型的桌面应用（HK416 / xya / Tong），可扩展、可加载社区开源模型。**

双击 `VITS Studio.exe` → 原生窗口 → 输入文字 → 生成 WAV。不用命令行、不用浏览器、不联网。
三个角色已内置；往模型目录里丢一个文件夹就能多一个角色，见 [docs/ADD_MODEL.md](docs/ADD_MODEL.md)。

---

## 架构

```text
     桌面用户双击 VITS Studio.exe
                │
                ▼
┌──────────────────────────────────────────────────────────────┐
│  VITS Studio.exe（app/desktop.py，PyInstaller 打包的启动器） │
│                                                              │
│   ┌──────────────────────┐      HTTP       ┌───────────────┐ │
│   │ pywebview 原生窗口   │ ◄─────────────► │ 后端子进程    │ │
│   │ 加载 Web UI(app/web) │  JSON / WAV     │ FastAPI       │ │
│   └──────────────────────┘                 │ (app/backend) │ │
│                                            └───────┬───────┘ │
└────────────────────────────────────────────────────┼─────────┘
                                                     │ 串行函数调用
                                                     ▼
                                            ┌────────┬────────┐
                                            │ Engine          │
                                            │ 单模型驻留/加锁 │
                                            └────────┬────────┘
                                                     ▼
                                          ┌──────────┬──────────┐
                                          │GPT-SoVITS 权重      │
                                          │v1/v2/v2Pro/v2ProPlus│
                                          │models_out\{模型}\   │
                                          └─────────────────────┘

 鸿蒙 APP（app/harmony）── 局域网 HTTP（同一套 API）──► 同一个 FastAPI
```

一句话链条：**Web UI (`app/web`) ←→ FastAPI (`app/backend`) ←→ Engine → GPT-SoVITS 权重**。

- **桌面 EXE** 只是启动器：拉起 pywebview 窗口（Windows 上用 Edge WebView2），并在**子进程**里隐藏运行 FastAPI，绑定 `127.0.0.1:<随机端口>`；窗口一关，服务一起退出。普通用户接触不到端口和浏览器。
- **HTTP API 依然存在**，它的两个消费者是：① EXE 里的窗口 UI；② 鸿蒙 APP（手机通过局域网访问，此时才需要手工启动服务并显式指定端口）。完整契约见 [docs/API.md](docs/API.md)。
- **GPU 策略**：12 GB 显存同一时刻只驻留一个模型，所有推理串行排队；换模型要重新加载（约 15 秒）。
- **运行依赖不在 EXE 里**：EXE 不含 Python、torch、GPT-SoVITS 仓库和模型权重，仍依赖本机的 `<VITS_ENV>` 环境（见[快速开始](#快速开始)的前提）。

---

## 目录结构

```text
<PROJECT_ROOT>\
├── app\                         本项目
│   ├── backend\                 后端（FastAPI）
│   │   ├── server.py            HTTP 路由：/api/health、/api/models、/api/tts …
│   │   ├── engine.py            推理引擎：串行加锁、单模型驻留、调 GPT-SoVITS
│   │   └── registry.py          模型发现：扫描模型目录，读 model.json
│   ├── web\                     前端页面（窗口里加载的就是它）
│   │   ├── index.html           页面结构
│   │   ├── style.css            样式
│   │   └── app.js               交互逻辑（模型列表、合成、历史、下载）
│   ├── harmony\                 鸿蒙 ArkTS 工程（同一套 HTTP API）
│   │   └── README.md            鸿蒙端说明、局域网联调步骤
│   ├── docs\
│   │   ├── API.md               REST API 规格（含 curl / fetch / ArkTS 示例）
│   │   └── ADD_MODEL.md         新增模型指南（含零样本）
│   ├── desktop.py               桌面启动器：pywebview 窗口 + 拉起后端
│   ├── build\                   PyInstaller 中间产物（含 VITS Studio.spec）
│   ├── dist\                    打包产物
│   │   └── VITS Studio\
│   │       ├── VITS Studio.exe  ← 双击这个
│   │       └── _internal\       运行时依赖（必须和 exe 放一起）
│   ├── logs\                    运行日志：server.log、desktop.log
│   └── CONTEXT.md               项目交接说明（内部）
└── models_out\                  模型目录（「目录即模型」）
    ├── HK416\                   sovits.pth + gpt.ckpt + ref.wav + ref.txt + model.json
    ├── xya\
    └── Tong\
```

> 根目录下的 `dataset_*`、`gsv_data`、`训练模型`、`vits-upgrade`、`inference-*` 等是**训练/实验的历史产物，运行本应用不需要**。真正跑起来只需要 `app\` + `models_out\` + `<VITS_ENV>`。

---

## 快速开始

### 前提（一次性）

| 依赖 | 路径 | 说明 |
|---|---|---|
| Python 环境 | `<VITS_ENV>\venv-modern\Scripts\python.exe` | 已装 torch(CUDA)、fastapi、uvicorn、soundfile、pywebview 等 |
| GPT-SoVITS 仓库 | `<VITS_ENV>\GPT-SoVITS` | v2Pro 代码 + `pretrained_models\` 底模 |
| 模型目录 | `<PROJECT_ROOT>\models_out` | 三个角色模型 |
| 显卡 | ≥ 8 GB 显存（本机 RTX 5070 Ti Laptop，12 GB） | CPU 也能跑，但很慢 |

### 场景 A：桌面用户（推荐，无需命令行）

1. 双击：

   ```text
   <PROJECT_ROOT>\app\dist\VITS Studio\VITS Studio.exe
   ```

2. 窗口出现（首次约 5–15 秒，后端在后台启动；启动器最多等 90 秒，超时会弹错误框；日志写在 exe 同级的 `logs\`）。
3. 左侧选角色 → 输入文本（≤500 字）→ 选语言（`ja` / `zh` / `en`）→「生成语音」→ 播放或下载 WAV。
4. **关闭窗口 = 退出应用**，后台服务会一并结束（会调 `/api/shutdown`）。

> **「下载 WAV」保存到哪？** `%USERPROFILE%\Downloads\VITS Studio\`（即 `%USERPROFILE%\Downloads\VITS Studio\`），文件名是 `模型id_时间戳.wav`（如 `Tong_20261126_223015.wav`）。点击后界面会显示完整路径，并出现「打开文件夹」按钮；**同一段音频再点一次不会重复写盘**，只提示「文件已存在」+ 原路径。保存失败（无权限/磁盘满）会弹红色错误，不会静默。

首次合成要加载模型（约 15 秒），单句推理本机实测 5.25 秒（12 字），**合计 20 秒上下**；之后每句 1–5 秒。详细说明见[常见问题](#常见问题)。

> 若机器上 pywebview/WebView2 不可用，启动器会自动退回「系统浏览器打开」——功能一样，只是有个浏览器外框。排查见 [常见问题](#常见问题)。

### 场景 B：开发者 / 调试（命令行 + 浏览器）

这是调试与兜底路径，**不是**给最终用户的路径。

```powershell
# 1) 启动后端（固定 7788）
<VITS_ENV>\venv-modern\Scripts\python.exe <PROJECT_ROOT>\app\backend\server.py --port 7788

# 等价写法（先切到项目根目录）
Set-Location <PROJECT_ROOT>
<VITS_ENV>\venv-modern\Scripts\python.exe app\backend\server.py --port 7788

# 2) 浏览器打开（调试 UI 用）
Start-Process http://127.0.0.1:7788

# 3) 不用了，停掉
curl.exe -s -X POST http://127.0.0.1:7788/api/shutdown
```

常用参数：

```powershell
--port 7788                      端口（默认 7788）
--host 127.0.0.1                 监听地址（手机联调要 0.0.0.0）
--models "D:\...\models_out"     模型目录
--device cuda|cpu                推理设备
--preload                        启动时就加载第一个模型
```

> ⚠️ 不要同时开 EXE 和手工后端：两个进程各占一份显存，12 GB 会 OOM。

### 场景 C：鸿蒙 APP / 手机联调

1. 电脑上用**固定端口**、绑定所有网卡启动后端：

   ```powershell
   <VITS_ENV>\venv-modern\Scripts\python.exe <PROJECT_ROOT>\app\backend\server.py --host 0.0.0.0 --port 7788
   ```

2. 查电脑局域网 IP：

   ```powershell
   Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' } | Select-Object InterfaceAlias, IPAddress
   ```

3. 手机与电脑连同一 Wi-Fi，APP 里填 `http://<电脑IP>:7788`，例如 `http://<LAN_IP>:7788`。
4. 首次会弹 Windows 防火墙，选「允许专用网络」；仍连不上就在防火墙里放行 7788。

鸿蒙工程的编译、页面与 API 封装见 [harmony/README.md](harmony/README.md)，接口字段见 [docs/API.md](docs/API.md)。

---

## 内置模型

> 模型由「专职训练 VITS 的 AI」产出，交付形态是 `models_out\`（训练产物 + `manifest.json`）
> 与 `VITS标准模型\`（可直接分发的标准包，含 `MODEL_CARD.md` / `metrics.json` / `SHA256SUMS.txt`）。
> 应用同时扫描这两个目录，按 `speaker` 去重、保留版本号最高的一份。

| id | 版本 | 默认语言 | 训练数据 | 权重 | CER（评测集） | 备注 |
|---|---|---|---|---|---|---|
| `HK416` | 1.2.0 | `ja` | 54 条 | s1+s2 微调 | 0.120 | 音色贴合最好（`timbre_self` 0.128） |
| `Tong` | 1.1.0 | `ja` / `zh` | 290 条 | s1+s2 微调 | 0.153（中文 0.178） | 数据最多，**唯一支持中文** |
| `xya` | 1.0.1 | `ja` | 177 条 | s1+s2 微调 | 0.109 | demucs 分离过 BGM |

- 每个模型权重约 277 MB（`sovits.pth`/`*_e*.pth` 128.7 + `gpt.ckpt`/`*-e*.ckpt` 148.1；`/api/models` 的 `size_mb` 按十进制 MB 显示为 290.3）。
- `/api/models` 会带出 `version` / `cer` / `timbre_self` / `rtf` / `output_sr` / `source`，界面卡片上直接显示 **v1.2.0**、**CER 0.12** 这类标签。
- 三个模型的**参考音都是日语**，参考音语言固定 `ja`（`model.json` 的 `ref_lang`）；要读中文就在请求/UI 里把语言切成 `zh`。
- 新增角色不用改代码：往任一模型根目录丢文件夹 → 界面「重新扫描模型」。见 [docs/ADD_MODEL.md](docs/ADD_MODEL.md)。

---

## 导出到手机（鸿蒙端模型包）

窗口里「导出到手机」（顶栏 / 左侧栏 / 模型卡右上角 `⇪`）→ 选经典 VITS 模型 → 选语音档位
（`T128/T256/T512/T1024`，可多选）→ 选输出目录 → 「开始导出」。完成后显示
**zip 路径 + 大小 + 耗时 + 自检结果**，可「打开所在文件夹」；检测到手机（`hdc`）时还能
「直接传到手机」（推到设备 `Download`，再到 App「导入模型」选它）。

- **能导出什么**：经典 VITS v1（22.05 kHz / hop 256），例如 `inference-xya`、`inference-HK416`
  以及 `vits-upgrade\registry.json` 里登记的模型。**GPT-SoVITS 模型（32 kHz，需要 BERT + 自回归
  GPT）端上跑不了**，卡片上的 `⇪` 会说明原因并给同名的经典 VITS 备选。
- **产物**：`<输出目录>\<id>.zip`，内含 `text2mel.ms` + `mel2wav.t*.ms` + `model.json`
  + `dur_ja/zh.json` + `README.txt`（格式见 `harmony-ondevice/docs/HANDOFF.md` §15）。
  四档约 197 MB，只带 `128,256` 约 110 MB。
- **不做命令行**：底层仍是 `harmony-ondevice\tools\make_ondevice_model.py`（**import 复用同一份代码**，
  不是另写一份）；桌面端只加了任务/进度/错误翻译/自检/hdc 推送。完整说明（含失败原因、
  环境变量、实测数据）见 [docs/ADD_MODEL.md §12](docs/ADD_MODEL.md#12-导出到鸿蒙端端上模型包)，
  接口见 [docs/API.md §4.8](docs/API.md#48-导出到手机鸿蒙端模型包apiondevice)。

实测（本机）：`xya` 四档 → `app\ondevice_out\xya.zip` 197 MB / 44.5 s / 9 个条目 / 自检通过；
与命令行产物逐文件对比，`text2mel.ms` 与四个 `mel2wav.t*.ms` 的 SHA256 完全相同。

---

## 打包 EXE（PyInstaller）

产物布局是 **onedir**（exe + `_internal`），因为 pywebview / pythonnet 用 onedir 最稳、启动最快。

```powershell
$py  = "<VITS_ENV>\venv-modern\Scripts\python.exe"
$app = "<PROJECT_ROOT>\app"

# 0) 先关掉正在运行的实例，否则 dist 目录被占用会打包失败
Get-Process "VITS Studio" -ErrorAction SilentlyContinue | Stop-Process -Force

# 1) 打包（入口 desktop.py，把 backend 和 web 一起塞进去）
& $py -m PyInstaller --noconfirm --clean --windowed `
  --name "VITS Studio" `
  --distpath "$app\dist" `
  --workpath "$app\build\work" `
  --specpath "$app\build" `
  --add-data "$app\backend;backend" `
  --add-data "$app\web;web" `
  --hidden-import webview.platforms.edgechromium `
  --hidden-import clr `
  --collect-submodules webview `
  "$app\desktop.py"

# 2) 检查产物
Get-ChildItem "$app\dist\VITS Studio" | Select-Object Length, Name
```

**产物**：

```text
<PROJECT_ROOT>\app\dist\VITS Studio\
├── VITS Studio.exe      ← 双击运行（约 10 MB）
└── _internal\           ← Python 运行时 + pywebview 依赖（约 76 MB）
```

- 合计约 **86 MB**（不含模型、torch、GPT-SoVITS 仓库——那些运行时从 `<VITS_ENV>` 读）。
- **分发时必须整个 `VITS Studio` 文件夹一起拷**，只拷 exe 会启动失败。
- 想复用参数可第二次直接打 spec：`& $py -m PyInstaller --noconfirm --distpath "$app\dist" --workpath "$app\build\work" "$app\build\VITS Studio.spec"`。
- 若确实要单文件：把 `--windowed` 换成 `--onefile --windowed`，产物为 `app\dist\VITS Studio.exe`；每次启动要解包，慢 2–5 秒，排错也更难，**不推荐**。

### 换机器 / 路径不同

exe 同级可以放一个 `config.json`（可选）：

```json
{
  "python": "<VITS_ENV>\\venv-modern\\Scripts\\python.exe",
  "models_dir": "<PROJECT_ROOT>\\models_out"
}
```

优先级：**环境变量 > `config.json` > 内置默认值**。

```powershell
$env:VITS_PYTHON  = "<VITS_ENV>\venv-modern\Scripts\python.exe"
$env:VITS_MODELS  = "<PROJECT_ROOT>\models_out"
& "<PROJECT_ROOT>\app\dist\VITS Studio\VITS Studio.exe"
```

---

## 常见问题

**1. 首次合成很慢（约 15 秒）？**
首次请求要把 GPT-SoVITS 模型加载进显存，约 15 秒，随后单句推理本机实测 5.25 秒（12 字），合计 20 秒上下；之后每句 1–5 秒，属正常。**切换角色后的第一句也会重新加载**，因为显存里只留一个模型。API 响应头 `X-Render-Seconds` 只统计推理，不含这 15 秒加载，别拿它判断「首次为什么慢」。

**2. 12 GB 显存只能驻留一个模型？**
是。引擎设计为「单模型驻留 + 串行推理」：换模型时先释放旧模型再加载新的。所以不要同时开两个实例、不要并行发多个合成请求，否则可能 OOM（报 500）。两个角色轮流用没问题，只是每次切换多等十几秒。

**3. 中文合成要选 `zh`？**
是。语言决定「要读的文本」用哪套前端处理：
- 读中文 → 选 `zh`（推荐用 `Tong`，中文语料最多）；
- 读日文 → 选 `ja`；
- 读英文 → 选 `en`。

不传语言时默认用模型的 `language`（三个都是 `ja`），所以**读中文一定要显式选 `zh`**，否则会按日文规则读中文，效果很差。

**4. 参考音是日语，为什么 `ref_lang` 固定 `ja`？**
参考音（`ref.wav`）与参考文本（`ref.txt`）都是日语，参考音语言必须是 `ja`，否则音色直接跑偏。后端把「参考音语言」固定取自 `model.json` 的 `language`（当前三个模型都是 `ja`），**API 请求体里没有 `ref_lang` 字段**。你只需要管 `lang`（读什么语言），参考音语言不用传。
→ 新增**中文参考音**的模型时，记得把它的 `model.json` 的 `language` 写成 `zh`，详见 [docs/ADD_MODEL.md](docs/ADD_MODEL.md)。

**5. 端口被占用怎么改？**
- **桌面 EXE**：不用管。启动器每次向系统申请随机空闲端口，只绑 `127.0.0.1`，不会和别的程序冲突。
- **手工启动**：换个端口即可，客户端/鸿蒙端同步改地址：

  ```powershell
  <VITS_ENV>\venv-modern\Scripts\python.exe <PROJECT_ROOT>\app\backend\server.py --port 7790
  ```

- 查谁占了 7788：

  ```powershell
  Get-NetTCPConnection -LocalPort 7788 -State Listen | Select-Object LocalAddress, OwningProcess
  Get-Process -Id <上一步的 OwningProcess>
  ```

**6. 双击 EXE 没反应 / 弹「启动失败」？**
看 `app\dist\VITS Studio\logs\desktop.log` 和 `server.log`。常见原因：`<VITS_ENV>\venv-modern` 被移走、`models_out` 不存在、pywebview/WebView2 缺失（会退回浏览器打开）、显存被别的程序占满。

**7. 模型列表里看不到我新加的模型？**
确认目录在 `models_out` 的**第一层**，点「重新扫描模型」或 `POST /api/models/reload`。`ready: false` 通常是缺 `sovits.pth`。见 [docs/ADD_MODEL.md](docs/ADD_MODEL.md)。

**8. 合成报 500？**
`detail` 会带原因，`logs\server.log` 有完整堆栈。常见：参考音 `ref.wav`/`ref.txt` 缺失、权重损坏或版本不匹配、显存不足。
社区模型可在 `model.json` 写 `engine_version`（`v1`/`v2`/`v2Pro`/`v2ProPlus`），详见 [docs/ADD_MODEL.md](docs/ADD_MODEL.md) §10。

**9. 手机上连不上电脑？**
后端必须 `--host 0.0.0.0` 手工启动（EXE 只绑 127.0.0.1，手机连不上），手机与电脑同一局域网，防火墙放行端口。

**10. 界面上的「退出应用」和关窗口有什么区别？**
没区别：都先调 `/api/shutdown` 关服务，再退出窗口。若用浏览器调试（场景 B），网页里的「退出」只会关掉后台服务。

**11. 点「下载 WAV」没反应 / 文件在哪？**
桌面版的下载由后端写盘，不走浏览器的「另存为」对话框（pywebview/WebView2 不接管 `blob:` 下载，旧版本点了会静默失效）。默认目录 **`%USERPROFILE%\Downloads\VITS Studio\`**，可用环境变量 `VITS_DOWNLOAD_DIR` 覆盖。点击成功后播放器下方会显示完整路径与字节数，并可「打开文件夹」；同一段音频重复点只提示已有路径，不重复写盘。若提示「保存失败（目录不可写或磁盘已满）」，按 `detail` 里的路径检查权限/磁盘。接口见 [docs/API.md §4.4.1](docs/API.md#441-post-apiaudiosave--把-wav-保存到磁盘)。

---

## 相关文档

| 文档 | 内容 |
|---|---|
| [docs/API.md](docs/API.md) | 所有 REST 端点、二进制 WAV 与响应头、错误码、curl/fetch/ArkTS 示例、真实返回样本 |
| [docs/ADD_MODEL.md](docs/ADD_MODEL.md) | 目录即模型、`model.json` 字段、从训练产物取权重、参考音要求、零样本模型 |
| [harmony/README.md](harmony/README.md) | 鸿蒙 APP 工程结构、DevEco 编译、局域网联调 |
| [CONTEXT.md](CONTEXT.md) | 内部交接说明（分工、历史决策），面向维护者 |

---

## 添加模型（界面）

左侧栏「＋ 添加模型」：

- **本地目录**：选文件夹 → 扫描 → 自动识别 SoVITS/GPT 权重与引擎版本（v1/v2/v3/v4/v2Pro/v2ProPlus）→ 选参考音与文本 → 安装（拷贝或只登记路径）。
- **HuggingFace**：填 `owner/name` 直接下载安装，带进度条。
- 顶栏「移除模型」把模型移到 `_removed\`（可恢复）。

详见 [docs/ADD_MODEL.md](docs/ADD_MODEL.md) §11。
