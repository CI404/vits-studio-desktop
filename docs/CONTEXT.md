# VITS Studio · 开发者交接上下文（CONTEXT）

> 本文是开发者交接文档。用户向说明见 [README.md](README.md)。

最后更新：2026-11-26 · 读者：接手本项目的开发者 / 子智能体
读法：§1 看现状 → §2 看架构 → 要改哪块再看对应章节。文中路径均为绝对路径或相对 `<PROJECT_ROOT>` 的路径，可直接用。

## 0. 这是什么

一个**本地离线 TTS 桌面应用**：双击 EXE → 原生窗口 → 输入文字 → 生成 WAV。不用命令行、不用浏览器、不联网。
内置三个自训练的 **GPT-SoVITS v2Pro** 角色模型（HK416 / Tong / xya），也能加载社区的 v1 / v2 / v2Pro / v2ProPlus 模型。

一句话链条：**Web UI (`app/web`) ←→ FastAPI (`app/backend`) ←→ Engine → GPT-SoVITS 权重 (`models_out`)**

三条硬约束（违反就炸）：

1. **显存 12 GB** → 同一时刻只驻留**一个**模型，所有推理**串行**；换模型必须重建（约 15 秒）。
2. **参考音全是日语** → `prompt_lang` 固定 `ja`，只有 `text_lang` 跟随请求的 `lang`。
3. **运行依赖不在 EXE 里** → Python、torch、GPT-SoVITS 仓库、权重都在本机 `<VITS_ENV>` 与 `models_out`（见 §6）。

## 1. 现状与交付物

全部已完成、已验收，**没有「待做」项**。

| 交付物 | 位置 | 状态 / 证据 |
|---|---|---|
| 桌面 EXE（PyInstaller onedir） | `<PROJECT_ROOT>\app\dist\VITS Studio\VITS Studio.exe` | exe 约 10 MB（10,287,945 字节）+ `_internal`（约 76 MB）；整目录 **86.2 MB / 156 文件** |
| 原生窗口 | `app\desktop.py` | pywebview（Windows 走 Edge WebView2）；后端是**子进程**里隐藏运行的 uvicorn，随机端口、只绑 `127.0.0.1`，**用户永远看不到 http 地址** |
| 后端 | `app\backend\` | FastAPI + uvicorn，**25 个路由**（见 §3） |
| Web UI | `app\web\` | `index.html` + `style.css` + `app.js`（约 78 KB），原生 JS、**无构建、无框架** |
| 鸿蒙早期骨架 | `app\harmony\` | ArkTS Stage 工程，调同一套 HTTP API；README 自述**未在 DevEco 里编译验证**，仅作参考 |
| 鸿蒙端正式项目 | `<PROJECT_ROOT>\harmony-ondevice\` | NAPI + MindSpore Lite，端上离线推理，见该目录 `docs\HANDOFF.md` |
| 文档 | `app\README.md`、`app\docs\{API,ADD_MODEL}.md` | 与代码一致；**改接口必须同步 `docs\API.md`** |

开发模式：`python <PROJECT_ROOT>\app\desktop.py`，或双击 `app\启动开发模式.bat`（bat 里预设 `VITS_PYTHON` 后调 `desktop.py`）。

补充事实：

- 运行时会生成 `logs\{desktop,server}.log`；「导出到手机」的 zip 落在 `ondevice_out\`（开发模式在 `app\ondevice_out\`，已产出 `hk416.zip` 110 MB / `xya.zip` 197 MB；打包后落在 exe 同级）。
- `app\config.example.json` 是 exe 同级可选 `config.json` 的样例；优先级 **环境变量 > config.json > 内置默认值**。

## 2. 架构与目录

```text
desktop.py（EXE 启动器）
 ├─ pywebview 原生窗口 ──HTTP──► 后端子进程 uvicorn（server.py，127.0.0.1:随机端口）
 │   加载 app/web/index.html
 └─ 关窗口 → POST /api/shutdown → terminate 子进程

server.py（全局 STATE：models / engine / last）
 └─ Engine（串行锁 + 单模型驻留）──► GPT-SoVITS  TTS.run()
      ▲
      └─ registry.discover() 扫 models_out / VITS标准模型 / community_models，按 id 去重保留最高版本
```

| 文件 | 职责 | 改动时注意 |
|---|---|---|
| `app\desktop.py` | 启动器：选空闲端口、拉子进程、轮询 `/api/health`（最多 90 s）、开窗口、关窗口时 shutdown | 打包入口就是它；`sys.frozen` 分支区分 `_MEIPASS` 与 exe 同级目录 |
| `app\backend\server.py` | 全部 HTTP 路由 + 静态资源 + 全局 `STATE` | 改模型根目录走 `_refresh()`（会重建 Engine） |
| `app\backend\engine.py` | 推理：加载/卸载、串行锁、归一化、静音裁剪、重试 | **坑最集中，见 §4** |
| `app\backend\registry.py` | 模型发现（「目录即模型」）、版本识别、指标合并 | `HEAD2VERSION` 是版本权威表；不要改模型目录 |
| `app\backend\installer.py` | 添加模型：scan / install（拷贝或登记）/ HF 下载任务 / 移除 | 拷贝模式落 `<PROJECT_ROOT>\community_models` |
| `app\backend\ondevice.py` | 导出到手机：复用 `harmony-ondevice\tools\make_ondevice_model.py`，管任务/进度/zip/hdc 推送 | 只能导出**经典 VITS 22.05 kHz**；GPT-SoVITS 模型导不了 |
| `app\backend\_stubs\` | `jieba_fast` / `gradio` / `opencc` / `cached_path` 垫片 | 打包后 GPT-SoVITS 找不到这些包会崩；路径见 `engine.STUBS` |
| `app\web\` | 页面结构 / 样式 / 交互 | 无构建，改完刷新即生效；`window.pywebview.api.pick_dir()` 只在窗口内可用 |
| `app\tools\install_community_model.py` | 命令行装社区模型（HF repo → 模型目录） | |

前端已实现的功能（**不要照着旧清单重写**）：模型卡片（版本 / CER / 来源标签 / 缺文件提示）、语言 chips（ja / zh / en）、语速 0.6–1.6、温度 0.5–1.5、500 字计数上限、Ctrl+Enter 生成、历史（`localStorage` 键 `vits_history`，最多 8 条、可重放）、播放与「下载 WAV」（**后端落盘**到 `Downloads\VITS Studio`，文件名带模型 id + 时间戳，同一段音频重复点不重复写盘；失败弹明确错误）、添加模型弹窗（本地目录 / HuggingFace / 说明）、导出到手机弹窗、移除模型、退出应用。

## 3. 后端接口

全部在 `app\backend\server.py`，无鉴权、CORS 全开、无版本前缀。完整字段与调用示例见 [docs/API.md](docs/API.md)。

**核心 7 个 `/api/*` + `/` + `/static` 挂载**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | `{ok, uptime_s, models(数量), model_dir, gpu, loaded_model, loaded_engine_version}`；后两个字段 API.md 尚未写 |
| GET | `/api/models` | `{models:[...]}`，含 `id/name/language/ref_lang/version/engine_version/output_sr/cer/timbre_self/rtf/source/ready/status/missing/warnings/size_mb` |
| POST | `/api/models/reload` | 重扫目录 + **重建 Engine**（丢弃显存里的模型）；返回结构同 `/api/models` |
| POST | `/api/models/load` | 只加载不合成（诊断用）：`{id}` → `engine_version/sovits_version/loaded_engine_version/s1/load_seconds` |
| POST | `/api/tts` | `{model,text,lang?,speed?,temperature?}` → **`audio/wav` 二进制**；响应头 `X-Audio-Seconds`、`X-Render-Seconds`、`X-Sample-Rate`、`X-Engine-Version`、`X-Model-Id`；**出错返回 JSON** |
| GET | `/api/tts/meta` | 上次成功合成统计（`seconds` / `audio_s` / `sr` / `at` / `engine_version`），内存态，reload 或重启即清空 |
| POST | `/api/audio/save` | 把最近一次合成（或请求里的 base64 WAV）保存到 `%USERPROFILE%\Downloads\VITS Studio\`；同一 SHA256 不重复写盘，返回 `{ok,path,filename,dir,bytes,sha256,duplicate}` |
| POST | `/api/audio/reveal` | 在资源管理器里定位已保存的 WAV（只允许下载目录内） |
| POST | `/api/shutdown` | 0.3 秒后 `os._exit(0)`（无优雅关闭、不刷日志） |
| GET | `/` | 返回 `app\web\index.html` |
| GET | `/static/*` | 挂载 `app\web\` 目录 |

**添加模型 5 个**：`/api/models/scan`（扫描目录）、`/api/models/install`（拷贝或登记安装）、`/api/models/import_hf`（起 HuggingFace 下载任务）、`/api/models/import_hf/{job_id}`（轮询进度，完成时附带新模型列表）、`/api/models/remove`（把目录移到模型根目录旁的 `_removed\`，可恢复）。

**导出到手机 10 个**（`/api/ondevice/*`）：`status`（工具链 / hdc / 目录就绪）、`sources`（哪些模型能导出及原因）、`build`（POST 建任务 / GET 列任务）、`build/{job_id}`（阶段与进度、日志尾部）、`packages`（已生成 zip）、`inspect`（zip 自检）、`reveal`（资源管理器定位）、`devices`（hdc 与已连设备）、`install`（`hdc file send` 推到手机 Download）。

**错误约定**：`400` 文本空 / 超 500 字 / 模型未就绪；`404` 未知模型；`422` 请求体校验失败（`detail` 是数组）；`500` 合成或加载异常（`detail` = `合成失败: <异常类型>: <原因>`）。`detail` 是可直接给用户看的中文串。

写代码时记住：`/api/tts` 成功是 WAV、失败是 JSON，**先看状态码或 `Content-Type` 再解析**，否则会把错误 JSON 当音频播。

## 4. 引擎行为与已知坑

### 4.1 一次合成的流水线（`engine.Engine.synthesize`）

1. 校验 `ref.wav` / `ref.txt` → `_load(info)`（同一模型目录已加载则直接复用）。
2. `text_lang = (lang or 模型 language)[:2]`；`prompt_lang = ref_lang or 模型 language` → 三个模型都是 `ja`。
3. `text_split_method = cut5 if len(text) > 40 else cut0`：**≤40 字不分句，>40 字按标点切**。
4. 固定 `top_k=15`、`top_p=1.0`、`batch_size=1`；`temperature` 与 `speed_factor` 透传。
5. 输出 → `normalize_audio()` → `trim_silence()` → 质量闸门（时长 ≥ 0.4 s 且 RMS ≥ 0.004）；不达标**自动重试，最多 2 次**（共 3 次尝试），仍不达标返回最后一次（不抛错）。
6. 返回 `(float32 音频, sr, 推理耗时)`；`server.py` 用 soundfile 写 `PCM_16` WAV，采样率随引擎回传（三个内置模型都是 32 kHz）。

### 4.2 坑清单（都是踩过的，别重犯）

| 现象 | 根因 | 对策 / 位置 |
|---|---|---|
| 三个模型全部爆音、削波严重 | GPT-SoVITS `TTS.run()` 返回的是 **int16 量级**数值（远超 ±1.0），直接按 float 写 PCM16 | `engine.normalize_audio()`：峰值 >1.5 先 `/32768`，再归一到 **±0.95** |
| 开头/结尾一大段空白 | 模型输出首尾静音 | `engine.trim_silence()`：阈值 0.006、首尾各保留 **30 ms** |
| 偶发近乎无声或只有 0.1 s | GPT 自回归「秒 EOS」 | 质量闸门 + **重试 ≤2**（`max_retries`） |
| 中文只出 0.3 s / 音色跑偏 | 把 `prompt_lang` 跟 `lang` 一起改成了 `zh`，而参考音是日语 | `prompt_lang` 永远取模型的 `ref_lang`/`language`（`ja`），只有 `text_lang` 变 |
| `unpickling stack underflow` | 裸 `torch.load` 读 s2 权重：v2Pro 的 s2 是 GPT-SoVITS 自定义容器，**文件头 `05` 不是 `PK`** | 必须用官方 `load_sovits_new`（`GPT_SoVITS\process_ckpt.py`，它会补 `PK` 头再 `torch.load`）；版本判定见 `registry.HEAD2VERSION` |
| 切换模型后 OOM / 越来越慢 | 12 GB 只能驻留一个模型 | `Engine._load` 按目录判重、换目录先释放，`torch.cuda.empty_cache()`；`_refresh()` 重建前先 `old.unload()` |
| 代码里相对路径突然失效 | `engine._prepare_sys()` 会 `os.chdir(<VITS_ENV>\GPT-SoVITS)`（GPT-SoVITS 依赖相对路径） | 服务进程内一律用**绝对路径**（`installer` / `ondevice` 都是这样写的） |
| 打包后 `ModuleNotFoundError: jieba_fast` | 依赖未随包 / 垫片路径失效 | `app\backend\_stubs\` 随包，`engine.STUBS` 兼容开发与打包两种路径 |
| 版本对不上、加载失败 | `model.json` 声明版本与权重内嵌版本不一致 | 优先级：`model.json.engine_version` > 权重前 2 字节 > 兜底 `v2Pro`；**GPT-SoVITS 以权重内嵌版本为准**，引擎会打警告日志 |
| 模型没有 `gpt.ckpt` | 只带 s2 的模型 | 自动用官方 s1 底模：v2Pro / v2ProPlus → `pretrained_models\s1v3.ckpt` |
| 两个进程抢显存 | 同时开了 EXE 和手工后端 | 只留一个；切换前先 `/api/shutdown` |

## 5. 模型清单

| id | 版本 | 默认语言 | 训练条数 | CER | timbre_self | rtf | 备注 |
|---|---|---|---|---|---|---|---|
| `HK416` | **1.2.0** | `ja` | 54（干净清单） | 0.120 | 0.128 | 0.69 | 音色贴合最好；参考音 4.25 s |
| `Tong` | **1.1.0** | `ja` / `zh` | 290 | 0.153（中文 **0.178**） | 0.118 | 0.54 | 数据最多，**唯一能读中文**；参考音 4.47 s |
| `xya` | **1.0.1** | `ja` | 177 | 0.109 | 0.184 | 0.36 | 数据经 demucs 人声分离；参考音 5.68 s |

- 输出统一 **32 kHz / PCM16**；三个模型 `finetuned` 均为 `s1+s2`，音色自匹配（`timbre_ok = yes`）全部通过，削波 0%。
- **版本号在各自 `model.json` 的 `version` 字段**；`models_out\manifest.json` 里没有逐模型的 `version`，只有 `files` / `reference` / `metrics`（CER、timbre、rtf、peak），`registry` 会把两边合并进 `/api/models`。
- 权重约 277 MB / 模型（s2 134.9 + s1 155.3 MB）；`size_mb` 按十进制 MB 显示为 290.3，**不是磁盘占用**。
- 新增角色**不用改代码**：往任一模型根目录丢一个符合结构的文件夹 → `/api/models/reload`。见 [docs/ADD_MODEL.md](docs/ADD_MODEL.md)。

## 6. 环境与启动

| 用途 | 路径 / 版本 |
|---|---|
| 训练 / 推理 Python | `<VITS_ENV>\venv-modern\Scripts\python.exe`（Python 3.12.10，torch **2.8.0+cu129**，CUDA 可用） |
| GPT-SoVITS 仓库 | `<VITS_ENV>\GPT-SoVITS`（v2Pro 代码 + `GPT_SoVITS\pretrained_models\`） |
| ffmpeg | `<VITS_ENV>\bin`（**需在 PATH**） |
| 数据 / 导出脚本环境 | `<PROJECT_ROOT>\vits-upgrade\.venv\Scripts\python.exe` |
| 模型根目录（默认三个） | `models_out`（训练产物）、`VITS标准模型`（标准分发包）、`community_models`（社区包）；`--models "A;B"` 可覆盖，按 id 去重保留最高版本 |
| 端上工具链 | `<BUILD_DIR>\tools\mslite\converter-2.5.0` |

三种启动方式：

```powershell
# A. 桌面用户：双击
<PROJECT_ROOT>\app\dist\VITS Studio\VITS Studio.exe

# B. 开发模式（原生窗口 + 热改前端）：用模型环境的 python，或直接双击 app\启动开发模式.bat
<VITS_ENV>\venv-modern\Scripts\python.exe <PROJECT_ROOT>\app\desktop.py

# C. 只起后端（调试 / 手机联调）
$py = "<VITS_ENV>\venv-modern\Scripts\python.exe"
& $py <PROJECT_ROOT>\app\backend\server.py --port 7788                 # 浏览器调试
& $py <PROJECT_ROOT>\app\backend\server.py --host 0.0.0.0 --port 7788   # 手机同局域网
curl.exe -s -X POST http://127.0.0.1:7788/api/shutdown                   # 停
```

`server.py` 参数：`--port`（默认 7788）、`--host`（默认 127.0.0.1）、`--models`（多根用 `;`）、`--device cuda|cpu`、`--preload`（启动即加载第一个模型）。

打包重新出 EXE：见 [README.md](README.md) 「打包 EXE（PyInstaller）」一节（onedir，入口 `desktop.py`，`--add-data` 带上 `backend` 与 `web`）。打包前先杀掉正在运行的 `VITS Studio` 进程，否则 `dist` 被占用。

## 7. 相关文档

| 文档 | 内容 |
|---|---|
| [README.md](README.md) | 用户向：安装、启动、打包、FAQ |
| [docs/API.md](docs/API.md) | REST 契约：全部端点、响应头、错误码、curl/fetch/ArkTS 示例、真实返回样本 |
| [docs/ADD_MODEL.md](docs/ADD_MODEL.md) | 目录即模型、`model.json` 字段、社区包、零样本模型、「添加模型」界面（§11） |
| [../models_out/manifest.json](../models_out/manifest.json) | 模型总清单 + 指标（消费契约，`schema: 1`） |
| [../trainer/docs/HANDOFF.md](../trainer/docs/HANDOFF.md) | 训练端交给消费方的契约与边界 |
| [../harmony-ondevice/docs/HANDOFF.md](../harmony-ondevice/docs/HANDOFF.md) | 鸿蒙端上全貌（NAPI、静态 text2mel、mel2wav 档位、G2P、模型导入） |
| [../docs/PROJECT_MAP.md](../docs/PROJECT_MAP.md) | 工作区地图、归档策略、环境速查 |
| [../docs/KNOWLEDGE.md](../docs/KNOWLEDGE.md) | 跨项目可复用技术结论（版本识别、训练、端侧部署） |

## 8. 变更记录（v1 / v2 关键修复）

v1 = 早期骨架交付（能跑通，但音频与音色有硬伤）；v2 = 当前状态。

| # | v1 的问题 | v2 的修复 | 落点 |
|---|---|---|---|
| 1 | 三个模型全部爆音、大面积采样削波 | 输出归一化到 ±0.95（先判 int16 量级） | `engine.normalize_audio` |
| 2 | 三个模型听起来像同一个人 | HK416 用 54 条干净数据重训 80 epoch 并换参考音（→ 1.2.0） | `models_out\HK416` |
| 3 | 句子首尾大段空白 | 首尾静音裁剪（30 ms 余量） | `engine.trim_silence` |
| 4 | 偶发近乎无声的短输出 | 质量闸门 + 重试 ≤2 次 | `engine.synthesize` |
| 5 | 中文合成只出 0.3 s、音色跑偏 | `prompt_lang` 固定取模型 `ref_lang`（`ja`） | `engine.synthesize` |
| 6 | `reload` 后显存叠加、易 OOM | `_refresh()` 先 `unload()` 再重建 + `empty_cache()` | `server._refresh` / `engine.unload` |
| 7 | 社区模型加载报 `unpickling stack underflow` | 走官方 `load_sovits_new` 读 s2 自定义容器（头 `05`/`06` 不是 `PK`），版本按权重头识别 | `registry.HEAD2VERSION` + GPT-SoVITS `process_ckpt.py` |
| 8 | `backend\models.py` 与 GPT-SoVITS 的 `models` 包重名，导入串到 GPT-SoVITS 的同名包 | 改名 `registry.py` | `app\backend\registry.py` |
| 9 | 打包后 `jieba_fast` / `gradio` 缺失 | `backend\_stubs` 随包 + 兼容开发/打包两套路径 | `engine.STUBS` |
| 10 | `/api/health` 看不出当前加载了谁 | 增加 `loaded_model` / `loaded_engine_version`；`/api/tts/meta` 增加 `engine_version` | `server.py` |
| 11 | 桌面版「下载 WAV」点击完全静默（`blob:` + `<a download>`，WebView2 不接管下载事件） | 改为合成后调 `POST /api/audio/save` 由后端写盘；UI 显示绝对路径/字节数/错误 + 「打开文件夹」；同一 SHA256 去重；`/api/audio/reveal` 用 `explorer /select,"<path>"`（整参加引号会被 Explorer 解析失败） | `server.py` / `web/app.js` |

模型侧版本变化（来自各自 `model.json` 的 `changelog`）：HK416 1.0.0 → 1.1.0（重训 80 epoch、换参考音、修爆音）→ **1.2.0**（数据修复后重训）；Tong 1.0.0 → **1.1.0**（补官方剧情文本 121 条）；xya 1.0.0 → **1.0.1**（修爆音、补 s1 微调）。

## 9. 常见改动指引（改哪儿）

| 想做的事 | 改哪里 | 注意 |
|---|---|---|
| 加一个角色 | 只加目录，不改代码；界面「重新扫描模型」或 `/api/models/reload` | 见 `docs\ADD_MODEL.md`；缺文件会 `ready:false` |
| 调切句 / 重试 / 归一化 / 静音裁剪 | `app\backend\engine.py`（单点） | 阈值改动要在 `docs\API.md` 或本文件记录 |
| 加一个 HTTP 端点 | `server.py` + `docs\API.md` + 需要时 `app.js` | 先加字段不删字段 |
| 改界面 / 交互 | `app\web\{index.html,style.css,app.js}` | 无构建，刷新即生效；不要引入打包步骤 |
| 改端口 / 路径 / 环境 | `desktop.py`（`VITS_PYTHON`、`VITS_MODELS`、`config.json`） | 优先级：环境变量 > config.json > 内置默认值 |
| 模型版本 / 指标不对 | `models_out\<id>\model.json` 的 `version`，或重跑 `trainer/model_card.py` 生成 `manifest.json` | 不要手改权重与 `_removed\` |
| 重新出 EXE | PyInstaller onedir（见 README） | 先关掉运行中的实例 |
| 端上（手机）相关 | `<PROJECT_ROOT>\harmony-ondevice\`，先读其 `docs\HANDOFF.md` | `app\harmony\` 只是早期 HTTP 客户端骨架 |

## 10. 导出到手机（端上模型打包）集成（2026-11-26 17:0x）

用户目标：**端上打包不再需要命令行**——EXE 里点「导出到手机」出 zip，能推到手机。

### 10.1 代码

| 文件 | 改动 |
|---|---|
| `app/backend/ondevice.py`（新增） | 任务管理 + 阶段/进度 + 日志 tee + 错误翻译（中文原因 + 建议）+ 包自检 + hdc 推送 |
| `app/backend/server.py` | 新增 10 个 `/api/ondevice/*` 路由（status / sources / build / build{id} / packages / inspect / reveal / devices / install） |
| `app/web/{index.html,style.css,app.js}` | 顶栏「导出到手机」+ 侧栏按钮 + 模型卡右上角 `⇪`；导出面板：档位多选、输出目录、进度条+阶段+日志尾部、完成态（zip 路径/大小/耗时/自检）、打开所在文件夹、传手机、已生成包列表 |
| `app/desktop.py` | 给后端子进程注入 `VITS_ONDEVICE_OUT=<exe 同级>\ondevice_out` |
| `app/docs/{ADD_MODEL.md,API.md}`、`app/README.md` | 新增「导出到鸿蒙端」§12 / `/api/ondevice/*` §4.8 / README 章节 |

**设计：复用而非重写。** `ondevice.py` 用 import 直接调 `harmony-ondevice/tools/make_ondevice_model.py` 的
`cmd_build()`（同一份导出/转换/打包代码），外面只套：① 临时替换 CLI 的 `run_converter` 做「按档位推进」的进度钩子；
② tee stdout/stderr 采集阶段与日志；③ 把 `die()` 的 `SystemExit`/异常翻译成中文原因 + 建议；④ 结束跑一次结构化 `inspect_zip` 自检。

**两个坑（别踩）**：

- `converter_lite 2.5.0` 不认非 ASCII 路径，而工程目录 `<PROJECT_ROOT>` 就是中文 → 中间目录默认
  `%LOCALAPPDATA%\VITSStudio\ondevice-work`（纯 ASCII）；显式把 `VITS_ONDEVICE_WORK` 设成中文目录会**直接报错**，不静默换目录。
- CLI 用 `die()`（`SystemExit`）报错，HTTP 线程里必须捕获，否则 500 且没有可读原因。

### 10.2 真实验收（2026-11-26 17:01–17:04）

| 项 | 数据 |
|---|---|
| UI 全流程（**真实 EXE 窗口**，WebView2 + CDP 驱动） | 卡片 `⇪` → 面板 → `registry:HK416` + `128,256` → 6%→34%→44%→68%→93%→100%，无 JS 报错 |
| UI 全流程（开发后端 headless Chrome，xya 四档） | 3%→26%→44%→68%→80%→93%→100%，`window.__errs` 为空 |
| 产物（xya 四档） | `app\ondevice_out\xya.zip` 196,838,255 B / 9 条目 / **44.5 s**；经 EXE 打包 196,838,257 B / **41.7 s** |
| 产物（EXE，HK416 两档） | `dist\VITS Studio\ondevice_out\hk416.zip` 110,296,474 B / 7 条目 / **21.6 s** |
| 自检 | 面板「自检 ✓」；CLI `inspect` → `必需文件缺失：无`、`tiers=[128,256,512,1024]`、`symbols=52`、`prompts=[ja,zh]` |
| 与 CLI 产物对比 | 9 条目名/大小全等；`text2mel.ms` + 四个 `mel2wav.t*.ms` 的 **SHA256 逐一相同**；`model.json` 20 字段全同（仅 `built_at` 不同） |
| 推手机 | `hdc file send` → 设备 `Download/xya.zip` 3.5 s；设备侧 md5 `892a5c31…` = 本机 md5（HarmonyOS 模拟器 `127.0.0.1:5555`） |
| 错误路径 | GPT-SoVITS 目录 → 400「缺 config…」；未知模型 → 400 带建议；档位 999 → 400；未知 job → 404；缺转换器 / 伪权重 → 任务 `status=error` + 中文原因 + hint |

截图（`app\docs\evidence\`）：`ondevice_00_entry.png`（入口/卡片 ⇪）、`ondevice_01_panel.png`、
`ondevice_02_progress.png`、`ondevice_03_done_hk416.png`（EXE 完成态）、`ondevice_04_done_xya.png`。
复现脚本：`app\tools\ondevice_ui_smoke.mjs`（Node 22+，支持 `CDP_REUSE=1` 直连 EXE 的 WebView2 窗口）。
验证时 EXE 目录里产生的 zip 已清理（保持分发目录干净）；`app\ondevice_out\` 保留了 xya/hk416 两个示例包。

### 10.3 剩余限制

1. **能导出的只有经典 VITS 22.05 kHz**（`inference-*` / registry）；桌面端主力的 GPT-SoVITS（32 kHz，需 BERT + 自回归 GPT）端上仍跑不了——端上运行时能力边界（HANDOFF §15.5），不是 UI 限制。
2. EXE 不内嵌工具链：仍需 `harmony-ondevice\tools` + `converter-2.5.0`（+ `vits-upgrade`）在磁盘上，路径可用环境变量改。EXE 省掉的是「敲命令」，不是这些依赖。
3. 打包**单任务串行**（CPU 重），第二个任务排队；导出期间别同时合成（都吃内存）。
4. 端上无通用 G2P：包内 `prompts` 只有日/中各一条示范句。
5. 真机导入仍需人在 App 里点「导入模型」（系统文件选择器），接口只负责把 zip 放到 `Download`。
