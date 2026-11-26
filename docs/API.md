# VITS Studio REST API 规格

> 版本 1.1 · 本文档对应 2026-11-26 的 [`app/backend/server.py`](../backend/server.py)（**23 路由 + `/static` 挂载**） · 返回音频采用 **二进制 WAV**
>
> 本文件只描述**真实存在**的接口。改动服务端时请同步更新本文件。

---

## 0. 这套 API 谁在用

| 调用方 | 是否暴露给用户 | 说明 |
|---|---|---|
| **桌面 EXE（`VITS Studio.exe`）** | 否 | EXE 内部的原生窗口 UI 调用后端，后端在后台**隐藏运行**。用户不需要浏览器、不需要命令行。 |
| **鸿蒙 APP** | 否 | 手机通过局域网访问电脑上的服务，见 [harmony/README.md](../harmony/README.md)。 |
| **开发者 / 联调** | 是 | 手动启动 `server.py`，用 curl / 浏览器 / 脚本调试。 |

> 桌面端由 EXE 内部调用，**无需手动启动服务**；下文的手工启动方式只用于开发与鸿蒙联调。

---

## 1. 连接信息

| 项 | 值 |
|---|---|
| 传输 | HTTP/1.1，JSON（UTF-8），音频为二进制 |
| 默认地址（手工启动） | `http://127.0.0.1:7788` |
| 默认地址（鸿蒙联调） | `http://<电脑局域网 IP>:7788`，启动时需 `--host 0.0.0.0` |
| 端口（EXE 内自动） | `app/desktop.py` 每次启动向系统申请一个**随机空闲端口**，只绑定 `127.0.0.1`，仅供 EXE 内部窗口使用（所以 EXE 用户永远看不到、也不需要记端口） |
| CORS | 全开（`allow_origins=["*"]`），浏览器跨域可直接调 |
| 鉴权 | **无**。服务只应监听本机或可信局域网，切勿直接暴露到公网 |
| 前缀 | 无版本前缀，路径以 `/api/` 开头 |

### 手工启动（开发者）

```powershell
# 仅本机调试（默认 127.0.0.1:7788）
<VITS_ENV>\venv-modern\Scripts\python.exe <PROJECT_ROOT>\app\backend\server.py --port 7788

# 鸿蒙手机联调：必须绑定 0.0.0.0，手机才能连进来
<VITS_ENV>\venv-modern\Scripts\python.exe <PROJECT_ROOT>\app\backend\server.py --host 0.0.0.0 --port 7788 --models "<PROJECT_ROOT>\models_out"
```

首次 Windows 防火墙会弹窗，选择「允许专用网络」。

### 关于超时（重要）

- 首次合成要加载模型（约 15 秒），**首次请求建议把超时设到 120–180 秒**。
- 之后单句通常 1–5 秒（本机实测 12 字 5.25 秒）。
- 12 GB 显存只能驻留**一个**模型；切换模型后的第一句同样会重新加载（约 15 秒）。

---

## 2. 端点总览

| 方法 | 路径 | 用途 | 响应类型 |
|---|---|---|---|
| `GET` | `/api/health` | 健康检查、设备与模型目录信息（含当前加载的模型） | `application/json` |
| `GET` | `/api/models` | 列出所有模型 | `application/json` |
| `POST` | `/api/models/reload` | 重新扫描模型目录 | `application/json` |
| `POST` | `/api/models/load` | 只加载模型（不合成），用于诊断引擎版本/加载失败原因 | `application/json` |
| `POST` | `/api/models/scan` | 扫描一个本地目录，列出可用权重/参考音/文本 | `application/json` |
| `POST` | `/api/models/install` | 安装模型（拷贝进模型库，或只登记路径） | `application/json` |
| `POST` | `/api/models/import_hf` | 从 HuggingFace 下载并安装（异步任务） | `application/json` |
| `GET` | `/api/models/import_hf/{job_id}` | 下载任务进度；完成时附带新模型列表 | `application/json` |
| `POST` | `/api/models/remove` | 把模型目录移到 `_removed\`（不真删，可恢复） | `application/json` |
| `GET` | `/api/ondevice/status` | 「导出到手机」工具链/转换器/目录/hdc 状态 | `application/json` |
| `GET` | `/api/ondevice/sources` | 哪些模型能导出到鸿蒙端（经典 VITS）+ 不能的原因 | `application/json` |
| `GET` | `/api/ondevice/build` | 最近打包任务列表（默认 20 条） | `application/json` |
| `POST` | `/api/ondevice/build` | 创建打包任务（导出 ONNX→转 .ms→打包） | `application/json` |
| `GET` | `/api/ondevice/build/{id}` | 打包进度：阶段、日志尾部、产物 | `application/json` |
| `GET` | `/api/ondevice/packages` | 已生成的端上包（zip）列表 | `application/json` |
| `POST` | `/api/ondevice/inspect` | 结构化自检一个 zip（条目/必需文件/model.json 摘要） | `application/json` |
| `POST` | `/api/ondevice/reveal` | 在资源管理器里定位产物 | `application/json` |
| `GET` | `/api/ondevice/devices` | hdc 是否可用 + 已连接设备 | `application/json` |
| `POST` | `/api/ondevice/install` | 把 zip 推到手机 `Download`（hdc） | `application/json` |
| `POST` | `/api/tts` | 合成语音 | **`audio/wav` 二进制** |
| `GET` | `/api/tts/meta` | 上一次合成的统计信息 | `application/json` |
| `POST` | `/api/audio/save` | 把最近一次合成（或请求里的 base64 WAV）保存到 `下载\VITS Studio`，按内容去重 | `application/json` |
| `POST` | `/api/audio/reveal` | 在资源管理器里定位已保存的 WAV | `application/json` |
| `POST` | `/api/shutdown` | 结束后端进程（桌面端「退出应用」用） | `application/json` |
| `GET` | `/` | 返回网页 UI（`app/web/index.html`） | `text/html` |
| `GET` | `/static/*` | 网页静态资源（CSS/JS） | 按文件类型 |

> 上表 **26 行 = 25 个路由 + 1 个 `/static` 静态挂载**，与 `server.py` 一一对应。分组：核心（health / models / tts / audio / shutdown / 页面）→ 添加与管理模型（`/api/models/scan|install|import_hf|remove`）→ 导出到手机（`/api/ondevice/*`）。

**没有** `/api/quit`、`/api/config`、`/api/voices` 之类的端点——请不要照着别的项目臆测。

---

## 3. 错误响应约定

业务错误使用 HTTP 状态码 + FastAPI 标准结构，`detail` 为**中文可读字符串**（可以直接显示给用户）：

```json
{ "detail": "未知模型: NoSuchModel" }
```

| 状态码 | 触发条件 | `detail` 示例 |
|---|---|---|
| `400` | 文本为空 / 超过 500 字 / 模型缺权重或参考音；音频保存参数不合法（坏 base64、不是 WAV） | `文本为空`、`文本过长（>500 字），请分段`、`模型 HK416 缺权重或参考音`、`不是合法的 WAV 数据（缺少 RIFF/WAVE 文件头）` |
| `404` | `model` 不存在（既不是目录名也不是 speaker 名）；还没有可保存的音频 / 文件不存在 | `未知模型: NoSuchModel`、`服务端还没有可保存的音频，请先合成一句再下载` |
| `422` | 请求体字段校验失败（缺 `model`/`text`、类型不对）——FastAPI 自动产生，`detail` 是**数组** | `[{"type":"missing","loc":["body","model"],"msg":"Field required",...}]` |
| `500` | 合成过程异常（模型加载失败、显存不足、依赖缺失等）；音频写盘失败（权限/磁盘） | `合成失败: ModuleNotFoundError: ...`、`保存失败（目录不可写或磁盘已满）：C:\...：PermissionError: ...` |

实测样本（404 / 400 / 422 为本机捕获；500 给的是 `detail` 格式示意）：

```text
HTTP/1.1 404 Not Found                          HTTP/1.1 400 Bad Request
{"detail":"未知模型: NoSuchModel"}               {"detail":"文本为空"}

HTTP/1.1 400 Bad Request                        HTTP/1.1 500 Internal Server Error
{"detail":"文本过长（>500 字），请分段"}          {"detail":"合成失败: <异常类型>: <原因>"}
```

> ⚠️ `/api/tts` 正常返回 WAV，**出错时返回 JSON**。客户端必须先判断 `Content-Type` 或状态码，再决定按音频还是按文本解析，否则会把错误 JSON 当 WAV 播放（常见症状：拿到一个只有一百多字节、播放不了的「WAV」）。

---

## 4. 端点详情

### 4.1 `GET /api/health`

健康检查。桌面启动器用它判断后端是否就绪，鸿蒙端可用于「连接测试」。

**请求**：无参数、无请求体。

**响应 200**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `ok` | bool | 恒为 `true`（能返回就代表服务活着） |
| `uptime_s` | int | 服务已运行秒数 |
| `models` | int | **已发现的模型数量**（注意这里是数字，不是数组） |
| `model_dir` | str | 模型目录；多个根目录时用 `;` 连接 |
| `gpu` | str | 显卡名；无 CUDA 时为 `"cpu"`；torch 不可用时为 `"unknown"` |
| `loaded_model` | str \| null | **当前驻留在显存里的模型 `id`**；没加载过为 `null`（换模型/`reload` 后变 `null`） |
| `loaded_engine_version` | str \| null | 当前驻留模型**实际加载**的引擎版本（`v1`/`v2`/`v2Pro`/`v2ProPlus`）；未加载时为 `null` |

**curl（PowerShell，注意用 `curl.exe`）**

```powershell
curl.exe -s http://127.0.0.1:7788/api/health
```

**JavaScript fetch**

```js
const r = await fetch('http://127.0.0.1:7788/api/health');
const info = await r.json();
console.log(info.ok, info.gpu, info.models);
```

**ArkTS（HarmonyOS）**

```ts
import { http } from '@kit.NetworkKit';

async function health(baseUrl: string): Promise<string> {
  const req = http.createHttp();
  try {
    const res = await req.request(`${baseUrl}/api/health`, {
      method: http.RequestMethod.GET,
      expectDataType: http.HttpDataType.STRING,
      connectTimeout: 5000,
      readTimeout: 10000,
    });
    if (res.responseCode !== 200) throw new Error(`HTTP ${res.responseCode}`);
    return res.result as string;            // JSON 文本，可 JSON.parse
  } finally {
    req.destroy();
  }
}
```

**实测真实输出**（本机，模型目录就绪、GPU 为 RTX 5070 Ti Laptop；后加的 `loaded_*` 两个字段在
「刚启动 / 还没合成过」时都是 `null`，合成过之后会变成模型 id 与实际引擎版本，例如 `"HK416"` / `"v2Pro"`）：

```json
{
  "ok": true,
  "uptime_s": 93,
  "models": 3,
  "model_dir": "<PROJECT_ROOT>\\models_out",
  "gpu": "NVIDIA GeForce RTX 5070 Ti Laptop GPU",
  "loaded_model": null,
  "loaded_engine_version": null
}
```

> `model_dir` 是多根拼接的字符串（`;` 分隔），单个根目录时就是那个路径。
> 想知道「现在到底加载了谁」，看 `loaded_model` / `loaded_engine_version`，不用去翻日志。

> PowerShell 里 `curl` 是 `Invoke-WebRequest` 的别名，会丢掉响应头、也不方便拿原始字节，本文一律写 `curl.exe`。等价的 PS 写法：`(Invoke-RestMethod http://127.0.0.1:7788/api/health).gpu`。

---

### 4.2 `GET /api/models`

列出模型目录下所有模型（「目录即模型」，一个子目录 = 一个模型，按目录名升序）。

**请求**：无参数。

**响应 200**：`{"models": [ModelInfo, ...]}`，每个 `ModelInfo`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | str | 目录名，例如 `HK416`；调用 `/api/tts` 时直接用这个值 |
| `name` | str | 显示名，取 `model.json` 的 `speaker`，缺省同 `id` |
| `language` | str | 两字母语言码，取 `model.json` 的 `language`，缺省 `ja`（只保留前 2 位） |
| `ref_lang` | str | **参考音语言**（`model.json` 的 `ref_lang`，缺省同 `language`）；三模型均为 `ja`，合成时由引擎自动使用 |
| `finetuned` | str | 微调范围备注，例如 `s1+s2`；可为空串 |
| `note` | str | 备注文本；可为空串 |
| `train_lines` | int | 训练语料条数；可为 0 |
| `version` | str | 模型包版本号（训练 AI 产出标准包时写入，例如 `1.2.0`）；社区包可能是 `community-1` 或空串 |
| `engine_version` | str | **实际传给 GPT-SoVITS 的引擎版本**：`v1` / `v2` / `v2Pro` / `v2ProPlus`；缺省时已按权重头/config.json 识别，识别不出回退 `v2Pro` |
| `sovits_version` | str | 从 SoVITS 权重前 2 字节识别出的版本（权威值，可能是空串） |
| `version_source` | str | `model.json` / `weights` / `config.json` / `default`，说明 `engine_version` 的来源 |
| `engine` | str | 引擎名，例如 `GPT-SoVITS` |
| `output_sr` | int | 输出采样率（默认 32000；v3 为 24000；优先取 `model.json.output_sr` / `config.json.sampling_rate`） |
| `sampling_rate` | int | `config.json` 里的训练采样率（0 表示没读到） |
| `add_blank` | bool\|null | `config.json` 里的训练开关（仅展示；推理不读，权重已内嵌） |
| `symbols_count` | int | `config.json` 里的自定义 symbols 数量（0 表示没读到；与内置词表不一致会写 warning） |
| `config_file` | str | 实际读取的 `config.json` 相对路径（空串表示没读到） |
| `cer` | float\|null | 固定评测集的 CER（来自 `metrics.json` / `manifest.json`，越低越好）；无评测为 `null` |
| `timbre_self` | float\|null | 音色自匹配距离（输出与该模型参考音的一致性，越小越稳） |
| `rtf` | float\|null | 实时率（离线评测平均值） |
| `source` | str | 该模型来自哪个扫描根目录（`models_out` / `VITS标准模型` / `community_models`） |
| `source_kind` | str | `local`（本地训练/标准包）或 `community`（社区目录） |
| `source_label` | str | 中文来源标签：`本地` / `社区` |
| `ready` | bool | 是否可用：`sovits` 权重与 `ref.wav`/`ref.txt` 都存在，且引擎版本依赖齐全 |
| `status` | str | `ready` / `missing` / `unsupported` |
| `missing` | list[str] | 未就绪原因（中文），例如 `["参考音（ref.wav…）", "参考音文本（ref.txt…）"]` |
| `warnings` | list[str] | 非致命提示：版本声明与权重头不一致、`model.json` 解析失败、config symbols 不匹配等 |
| `has_gpt` | bool | 是否有 GPT 权重；`false` 表示运行时按版本回退官方 s1 底模（v2Pro/v2ProPlus 为 `s1v3.ckpt`，v1/v2 优先各自官方 s1） |
| `size_mb` | float | **权重文件**合计大小（s2 + s1，十进制 MB，不含参考音） |

> 扫描根目录可用 `--models "A;B"` 指定多个，应用会按 `id` 去重、保留**版本号最高**的一份；
> 若根目录有 `manifest.json`，其中的 `metrics` 会被合并进来（这就是上表 `cer` / `timbre_self` 的来源）。

**curl**

```powershell
curl.exe -s http://127.0.0.1:7788/api/models
```

**JavaScript fetch**

```js
const { models } = await (await fetch('http://127.0.0.1:7788/api/models')).json();
for (const m of models) console.log(m.id, m.name, m.language, m.ready, m.has_gpt);
```

**ArkTS**

```ts
interface ModelInfo {
  id: string; name: string; language: string;
  finetuned: string; note: string; train_lines: number;
  ready: boolean; has_gpt: boolean; size_mb: number;
}

async function listModels(baseUrl: string): Promise<ModelInfo[]> {
  const req = http.createHttp();
  try {
    const res = await req.request(`${baseUrl}/api/models`, {
      method: http.RequestMethod.GET,
      expectDataType: http.HttpDataType.STRING,
      readTimeout: 10000,
    });
    const body = JSON.parse(res.result as string) as Record<string, ModelInfo[]>;
    return body.models ?? [];
  } finally {
    req.destroy();
  }
}
```

**实测真实输出**（三个模型，已排序 `HK416 → Tong → xya`）：

> 注：下面的 JSON 是较早版本；v1.2.0 起每个模型还带 `version` / `engine` / `output_sr` /
> `cer` / `timbre_self` / `rtf` / `source` / `ref_lang` 字段（含义见上表）。
> 当前实测值：`HK416 v1.2.0 CER 0.12`、`Tong v1.1.0 CER 0.153`、`xya v1.0.1 CER 0.109`。

```json
{
  "models": [
    {
      "id": "HK416", "name": "HK416", "language": "ja", "finetuned": "s1+s2",
      "note": "s1+s2 全微调；文本来自少前wiki官方日文台词（75条）",
      "train_lines": 75, "ready": true, "has_gpt": true, "size_mb": 290.3
    },
    {
      "id": "Tong", "name": "Tong", "language": "ja", "finetuned": "s1+s2",
      "note": "剧情 121 条用官方中文文本（E盘主线剧情全文对齐），其余用 Vosk 转写；s1+s2 微调",
      "train_lines": 290, "ready": true, "has_gpt": true, "size_mb": 290.3
    },
    {
      "id": "xya", "name": "xya", "language": "ja", "finetuned": "s1+s2",
      "note": "s1+s2 全微调；数据经 demucs 人声分离",
      "train_lines": 177, "ready": true, "has_gpt": true, "size_mb": 290.3
    }
  ]
}
```

> `ready: false` 的模型仍会出现在列表里（方便用户看到「缺文件」的模型），但 `/api/tts` 会返回 400。详见 [ADD_MODEL.md](ADD_MODEL.md)。

---

### 4.3 `POST /api/models/reload`

重新扫描模型目录，并**重建推理引擎**（会释放当前已加载的模型）。UI 的「重新扫描模型」按钮、鸿蒙端的「刷新」都调它。

**请求**：无请求体、无参数（`Content-Length: 0` 即可）。

**响应 200**：与 `GET /api/models` 完全相同的结构（`{"models": [...]}`），可直接用来刷新本地列表。

**副作用**：若之前已加载模型，内存/显存中的模型被丢弃，下一次合成会重新加载（约 15 秒）。

**curl**

```powershell
curl.exe -s -X POST http://127.0.0.1:7788/api/models/reload
```

**JavaScript fetch**

```js
const r = await fetch('http://127.0.0.1:7788/api/models/reload', { method: 'POST' });
if (!r.ok) throw new Error((await r.json()).detail);
const { models } = await r.json();   // 与 GET /api/models 同构
```

**ArkTS**

```ts
async function reloadModels(baseUrl: string): Promise<string> {
  const req = http.createHttp();
  try {
    const res = await req.request(`${baseUrl}/api/models/reload`, {
      method: http.RequestMethod.POST,
      expectDataType: http.HttpDataType.STRING,
      readTimeout: 30000,               // 扫描 3 个目录很快，留点余量
    });
    if (res.responseCode !== 200) throw new Error(`HTTP ${res.responseCode}`);
    return res.result as string;
  } finally {
    req.destroy();
  }
}
```

---

### 4.3.1 `POST /api/models/load`（诊断用）

只把模型加载进显存、不合成，用来快速判断「版本/权重到底能不能加载」，失败时返回明确错误。
桌面 UI 不调用它；测试脚本、鸿蒙端排错或 `curl` 排错时有用。

**请求**：`{"id": "FakeTong"}`

**响应 200**：

```json
{
  "ok": true,
  "id": "FakeTong",
  "engine_version": "v2Pro",
  "sovits_version": "v2Pro",
  "loaded_engine_version": "v2Pro",
  "s1": "fake_tong_s1.ckpt",
  "load_seconds": 2.8
}
```

- `engine_version`：`model.json` / 扫描结果声明的版本；
- `loaded_engine_version`：GPT-SoVITS 按权重内嵌版本**实际加载**的版本；
- `s1`：实际使用的 GPT(s1) 文件名（可能是模型自带，也可能是官方底模）；
- 未就绪 → 400 + `detail` 含 `missing` 原因；加载异常 → 500 + `detail` 含 engine_version / s2 / s1 / 原始异常类型。

```powershell
curl.exe -s -X POST http://127.0.0.1:7788/api/models/load `
  -H "Content-Type: application/json" -d "{\"id\":\"FakeTong\"}"
```

---

### 4.4 `POST /api/tts` —— 返回**二进制 WAV**

最重要的端点。**成功时响应体是纯 WAV 字节流，不是 JSON**。

> 桌面 UI 的「下载 WAV」**不再**走浏览器下载通道（pywebview/WebView2 不接管 `blob:` + `<a download>`，点了会完全静默），而是合成后调 [4.4.1](#441-post-apiaudiosave--把-wav-保存到磁盘) 由后端落盘。`/api/tts` 本身不写文件。

#### 请求体（JSON）

| 字段 | 类型 | 必需 | 默认 | 说明 |
|---|---|---|---|---|
| `model` | str | ✅ | — | 模型 `id` 或 `name`，两者都能匹配 |
| `text` | str | ✅ | — | 待合成文本。`strip()` 后不能为空；长度上限 **500**（按 Python 字符数计，中日文一个字算 1） |
| `lang` | str | ❌ | 模型的 `language` | 文本语言，**两字母码**：`zh` 中文 / `ja` 日文 / `en` 英文等。后端只取前 2 位 |
| `speed` | float | ❌ | `1.0` | 语速倍率，UI 范围为 0.6–1.6（服务端不校验范围） |
| `temperature` | float | ❌ | `1.0` | 采样温度，UI 范围 0.5–1.5，越高越有变化、越容易跑调 |
| `top_k` | int | ❌ | `15` | 采样 top-k，**请求体里不暴露**，始终用后端默认值 15 |

几个容易踩的点：

- **参考音是日语，参考音语言固定为 `ja`**，这一项由后端根据模型配置写入，**请求体里没有 `ref_lang` 字段**，传了也会被忽略。`lang` 只影响「要读的文本」。
- `lang` 不要传 `"auto"`：后端会截成 `"au"`，不是合法语言码，合成会失败。`"zh-CN"` 会被截成 `"zh"`，能用但不建议依赖这种写法。
- 未知字段会被忽略，不会报错。
- 建议带 `Accept: audio/wav`（Web UI 与鸿蒙端都这么发）；后端不强制。
- 混排（中文+日文+英文）由 GPT-SoVITS 的切分策略处理，建议仍按主要语言传 `lang`。

#### 响应 200（成功）

| 项 | 值 |
|---|---|
| `Content-Type` | `audio/wav`（PCM 16-bit WAV） |
| 音频格式 | 单声道、16-bit PCM；采样率见响应头（GPT-SoVITS v2Pro 通常为 32000 Hz） |
| `Content-Disposition` | `inline; filename="HK416.wav"` |

响应头含义：

| 响应头 | 实测示例 | 含义 |
|---|---|---|
| `X-Audio-Seconds` | `2.24` | **音频时长**（秒，两位小数）= 音频样本数 ÷ 采样率。用于界面显示「生成了 2.24 秒语音」 |
| `X-Render-Seconds` | `5.25` | **后端渲染耗时**（秒），只统计本次模型推理。**不含模型加载**——首次/换模型后的整体等待会多出十几秒，但这个头看不出来。用于性能对比，也不含网络传输 |
| `X-Sample-Rate` | `32000` | **采样率**（Hz，整数）。播放/混音前以此为准，不要写死 |
| `X-Engine-Version` | `v2Pro` | 本次**实际加载**的引擎版本（`v1`/`v2`/`v2Pro`/`v2ProPlus`；权重内嵌版本优先） |
| `X-Model-Id` | `FakeTong` | 实际合成的模型 id（便于日志/多模型客户端核对） |

> 上表数值来自本机一次真实合成（12 字日文）。实际值随文本长度与机器性能变化。
>
> ⚠️ 服务端发出的头名在网络上可能全是**小写**（如 `x-audio-seconds`），HTTP 头名本身不区分大小写：`fetch` 的 `headers.get('X-Audio-Seconds')` 照常可用；ArkTS 里要自己忽略大小写匹配（见下方示例的 `headerOf`）。

**实测响应头原文**（`curl.exe -D headers.txt` 原样保存）：

```http
HTTP/1.1 200 OK
date: Wed, 25 Nov 2026 15:18:32 GMT
server: uvicorn
x-audio-seconds: 2.24
x-render-seconds: 5.25
x-sample-rate: 32000
x-engine-version: v2Pro
x-model-id: HK416
content-disposition: inline; filename="HK416.wav"
content-length: 143404
content-type: audio/wav
vary: Origin
```

#### 响应 400 / 404 / 422 / 500

见 [第 3 节](#3-错误响应约定)。`/api/tts` 的错误响应是 `application/json`，`detail` 字段可直接展示。

#### 示例一：curl（PowerShell）

⚠️ **Windows PowerShell 5.1 会把内联 JSON 里的双引号吃掉**，`--data-binary '{"model":"HK416"}'` 会让 curl 收到坏 JSON（实测返回 422 `JSON decode error`）。因此**一律把请求体写成 UTF-8 文件再引用**：

```powershell
# 1) 写请求体（无 BOM 的 UTF-8；中文/日文都安全）
[IO.File]::WriteAllText("$PWD\body.json",
  '{"model":"HK416","text":"こんにちは、テストです。","lang":"ja","speed":1.0}',
  (New-Object Text.UTF8Encoding $false))

# 2) 合成并保存 WAV，同时把响应头另存
curl.exe -s -D headers.txt -o out.wav `
  -X POST http://127.0.0.1:7788/api/tts `
  -H "Content-Type: application/json" `
  --data-binary "@body.json"

# 3) 看响应头 + 试听
Get-Content headers.txt
Start-Process out.wav
```

> `body.json` 必须存在且内容合法；`@` 前缀表示「从文件读」。PowerShell 7（`pwsh`）的内联引号处理正常，但为了脚本通用，仍然推荐文件方式。

#### 示例二：JavaScript fetch（Web UI 用的就是这套）

```js
async function tts(model, text, lang = 'ja', speed = 1.0, temperature = 1.0) {
  const res = await fetch('http://127.0.0.1:7788/api/tts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'audio/wav' },
    body: JSON.stringify({ model, text, lang, speed, temperature }),
  });

  if (!res.ok) {
    // 错误时是 JSON，不是音频
    const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
    throw new Error(typeof err.detail === 'string' ? err.detail : `HTTP ${res.status}`);
  }

  const audioSec  = Number(res.headers.get('X-Audio-Seconds'));
  const renderSec = Number(res.headers.get('X-Render-Seconds'));
  const sampleRate = Number(res.headers.get('X-Sample-Rate'));

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);   // 交给 <audio src=...>
  return { url, blob, audioSec, renderSec, sampleRate };
}
```

#### 示例三：ArkTS（HarmonyOS）

```ts
import { http } from '@kit.NetworkKit';
import { fileIo as fs } from '@kit.CoreFileKit';
import { common } from '@kit.AbilityKit';

interface TtsResult { path: string; audioSeconds: number; renderSeconds: number; sampleRate: number; }

function headerOf(h: Object, name: string): string {
  const map = h as Record<string, string>;
  for (const k of Object.keys(map)) {
    if (k.toLowerCase() === name.toLowerCase()) return map[k];
  }
  return '';
}

export async function synthesize(ctx: common.UIAbilityContext, baseUrl: string,
                                 model: string, text: string, lang: string): Promise<TtsResult> {
  const req = http.createHttp();
  try {
    const res = await req.request(`${baseUrl}/api/tts`, {
      method: http.RequestMethod.POST,
      header: { 'Content-Type': 'application/json', 'Accept': 'audio/wav' },
      extraData: JSON.stringify({ model, text, lang, speed: 1.0, temperature: 1.0 }),
      expectDataType: http.HttpDataType.ARRAY_BUFFER,   // 关键：拿二进制
      connectTimeout: 10000,
      readTimeout: 180000,                              // 首次合成要加载模型，给足 3 分钟
    });

    if (res.responseCode !== 200) {
      // 错误时 result 是 JSON 文本
      const msg = typeof res.result === 'string' ? res.result : `HTTP ${res.responseCode}`;
      throw new Error(msg);
    }

    const buf = res.result as ArrayBuffer;              // WAV 字节
    const path = `${ctx.filesDir}/tts_${Date.now()}.wav`;
    const file = fs.openSync(path, fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE);
    fs.writeSync(file.fd, buf);
    fs.closeSync(file);

    return {
      path,
      audioSeconds: Number(headerOf(res.header, 'X-Audio-Seconds')),
      renderSeconds: Number(headerOf(res.header, 'X-Render-Seconds')),
      sampleRate: Number(headerOf(res.header, 'X-Sample-Rate')),
    };
  } finally {
    req.destroy();
  }
}
```

> 手机端保存到 `filesDir` 后用 `media.AVPlayer` 播放或直接分享；播放细节见 [harmony/README.md](../harmony/README.md)。

---

### 4.4.1 `POST /api/audio/save` —— 把 WAV 保存到磁盘

桌面端「下载 WAV」用的就是这个接口：**由后端写文件**，前端只负责显示返回的绝对路径与错误。这样在 pywebview/WebView2 里也能用，浏览器里行为一致。

保存目录固定为 **`%USERPROFILE%\Downloads\VITS Studio\`**（即 `%USERPROFILE%\Downloads\VITS Studio\`），不存在会自动创建；可用环境变量 `VITS_DOWNLOAD_DIR` 覆盖（EXE 的子进程会继承这个环境变量）。

#### 请求体（JSON）

| 字段 | 类型 | 必需 | 默认 | 说明 |
|---|---|---|---|---|
| `filename` | str | ❌ | `<model>_<YYYYMMDD_HHMMSS>.wav` | 期望文件名。只取文件名部分（目录会被丢掉），Windows 非法字符换成 `_`，没有 `.wav` 会自动补上 |
| `data` | str | ❌ | 服务端最近一次合成 | base64 编码的 WAV。**桌面 UI 不传**，直接保存 `/api/tts` 刚生成的那一段 |
| `model` | str | ❌ | 服务端记录的模型 id | 生成默认文件名时用 |

#### 响应 200

```json
{
  "ok": true,
  "path": "<USER>\\Downloads\\VITS Studio\\Tong_20261126_223015.wav",
  "filename": "Tong_20261126_223015.wav",
  "dir": "<USER>\\Downloads\\VITS Studio",
  "bytes": 107510,
  "sha256": "a84eb81f29eb23057d7feac3509466febab5e6db78bad58f2035f855220f9745",
  "duplicate": false
}
```

行为约定：

- **同一段 WAV（SHA256 相同）重复保存** → 不新写文件，返回第一次的 `path`，`duplicate: true`。前端据此提示「文件已存在」。
- 后端重启后内存缓存丢了、但磁盘上已有同名同内容文件 → 仍按重复处理；同名不同内容 → 自动加 `_1`、`_2` 后缀，**绝不覆盖**。
- 先写临时文件再 `os.replace` 原子替换，断电/异常不会留下半截 WAV。

#### 响应 400 / 404 / 500

| 状态码 | `detail` 示例 | 触发 |
|---|---|---|
| `400` | `data 不是合法的 base64` / `不是合法的 WAV 数据（缺少 RIFF/WAVE 文件头）` | 显式传了坏 `data` |
| `404` | `服务端还没有可保存的音频，请先合成一句再下载` | 启动后还没成功合成、也没传 `data` |
| `500` | `保存失败（目录不可写或磁盘已满）：C:\...：PermissionError: ...` | 目录无权限 / 磁盘满；**前端必须把 `detail` 原样提示，不允许静默** |

#### 示例（PowerShell + curl）

```powershell
# 1) 先按 4.4 合成一次（WAV 会留在服务端内存里）
# 2) 请求体写成无 BOM 的 UTF-8 文件（PowerShell 5.1 内联引号会吃坏 JSON）
[IO.File]::WriteAllText("$PWD\save.json",
  '{"filename":"Tong_20261126_223015.wav","model":"Tong"}',
  (New-Object Text.UTF8Encoding $false))
curl.exe -s -X POST http://127.0.0.1:7788/api/audio/save `
  -H "Content-Type: application/json" --data-binary "@save.json"
```

`data` 字段只给「把离线 WAV 交给这台机器落盘」的客户端用（例如手机把 /api/tts 拿到的字节回传），正常流程不用传。

#### 示例（JavaScript fetch，Web UI 用的就是这套）

```js
async function saveWav(filename, model) {
  const res = await fetch('/api/audio/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename, model }),
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error((data && data.detail) || `HTTP ${res.status}`);
  return data;   // { ok, path, filename, dir, bytes, sha256, duplicate }
}
```

---

### 4.4.2 `POST /api/audio/reveal` —— 打开文件所在文件夹

「下载 WAV」成功后的「打开文件夹」按钮使用。只允许打开保存目录内的文件（防越界）。

#### 请求体（JSON）

| 字段 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `path` | str | ❌ | 要定位的 WAV 绝对路径；省略则用服务端最近一次成功保存的文件 |

#### 响应 200

```json
{
  "ok": true,
  "revealed": "<USER>\\Downloads\\VITS Studio\\Tong_20261126_223015.wav",
  "dir": "<USER>\\Downloads\\VITS Studio"
}
```

Windows 上等价于 `explorer /select,<文件>`（打开文件夹并选中文件）；macOS 用 `open -R`，Linux 用 `xdg-open` 打开所在目录。

错误：`404` 没有可定位的文件 / 文件不存在；`400` 路径不在保存目录内；`500` 系统不支持或调用失败。

---

### 4.5 `GET /api/tts/meta`

返回**最近一次成功合成**的统计（调试/界面展示用）。服务启动后、尚未成功合成过时返回空对象 `{}`。

**请求**：无参数。

**响应 200**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `model` | str | 本次使用的模型 `id` |
| `chars` | int | 文本字符数 |
| `seconds` | float | 推理耗时（秒），即 `X-Render-Seconds` 的来源 |
| `audio_s` | float | 音频时长（秒），即 `X-Audio-Seconds` 的来源 |
| `sr` | int | 采样率 |
| `at` | float | Unix 时间戳（秒），合成完成时刻 |
| `engine_version` | str \| null | 本次**实际加载**的引擎版本（与 `/api/health` 的 `loaded_engine_version` 同源） |

**curl**

```powershell
curl.exe -s http://127.0.0.1:7788/api/tts/meta
```

**JavaScript fetch**

```js
const meta = await (await fetch('http://127.0.0.1:7788/api/tts/meta')).json();
// 未合成过：{}
// 合成过（本机实测；engine_version 为后加字段，示例值 v2Pro）：
// { model:'HK416', chars:12, seconds:5.25, audio_s:2.24, sr:32000,
//   at:1795619927.6002045, engine_version:'v2Pro' }
```

**ArkTS**

```ts
const req = http.createHttp();
const res = await req.request(`${baseUrl}/api/tts/meta`, {
  method: http.RequestMethod.GET,
  expectDataType: http.HttpDataType.STRING,
  readTimeout: 10000,
});
const meta = JSON.parse(res.result as string) as Record<string, number | string>;
req.destroy();
```

> 该状态只存在内存里，服务重启或 `reload` 后重置，**不要**把它当持久化历史记录。UI 的历史记录存在浏览器 `localStorage`。

---

### 4.6 `GET /` 与 `GET /static/*`

- `GET /`：返回网页 UI（`app/web/index.html`）。桌面端窗口和浏览器都加载它。
- `GET /static/*`：静态资源（`/static/style.css`、`/static/app.js` 等），映射到 `app/web/` 目录。

这两个是给 Web 界面用的，客户端（鸿蒙 APP）**不需要**调用。

```powershell
Start-Process http://127.0.0.1:7788/    # 开发时用浏览器看界面（桌面用户用 EXE，不需要）
```

---

### 4.7 `POST /api/shutdown`

**结束服务进程**。桌面 EXE 在关闭窗口后调用它，保证后台不留僵尸进程；也可以手动用它停掉调试用的后端。

**请求**：无请求体、无参数。

**响应 200**：立刻返回，约 0.3 秒后进程退出（`os._exit(0)`，uvicorn 不会走优雅关闭）。

```json
{ "ok": true, "message": "server shutting down" }
```

**副作用**：服务直接消失，所有后续请求失败（连接被拒）。桌面端的加载中的合成会中断。

**curl**

```powershell
curl.exe -s -X POST http://127.0.0.1:7788/api/shutdown
```

**JavaScript fetch**

```js
// 「退出应用」按钮：先关服务，再关窗口
await fetch('http://127.0.0.1:7788/api/shutdown', { method: 'POST' }).catch(() => {});
window.close();
```

**ArkTS**

```ts
// 鸿蒙端一般不需要；真要用（例如调试用的「重启服务」）：
const req = http.createHttp();
try {
  await req.request(`${baseUrl}/api/shutdown`, {
    method: http.RequestMethod.POST,
    readTimeout: 5000,
  });
} finally {
  req.destroy();
}
```

> ⚠️ 无鉴权。手工以 `--host 0.0.0.0` 启动联调时，同一局域网内任何人都能关掉你的服务。联调结束后请改回 `127.0.0.1`。

---

### 4.8 导出到手机（鸿蒙端模型包）：`/api/ondevice/*`

桌面端「导出到手机」面板用的接口。**只有经典 VITS（22.05 kHz / hop 256）模型能导出**；
GPT-SoVITS（`models_out\`、`VITS标准模型\`、`community_models\` 里的 32 kHz 模型）不行——详见
[ADD_MODEL.md §12](ADD_MODEL.md#12-导出到鸿蒙端端上模型包)。

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/ondevice/status` | 工具链 / 转换器 / 中间目录 / 产物目录 / hdc 是否就绪 |
| `GET` | `/api/ondevice/sources` | 可导出模型列表 + 不可导出模型及原因 |
| `POST` | `/api/ondevice/build` | 创建后台打包任务，**立即返回** job（含 id） |
| `GET` | `/api/ondevice/build/{id}` | 进度：阶段 / 百分比 / 日志尾部 / 产物 |
| `GET` | `/api/ondevice/build` | 最近任务列表（默认 20 条） |
| `GET` | `/api/ondevice/packages` | 已生成的 zip（大小/时间/档位） |
| `POST` | `/api/ondevice/inspect` | 结构化自检一个 zip |
| `POST` | `/api/ondevice/reveal` | 在资源管理器里定位产物 |
| `GET` | `/api/ondevice/devices` | hdc 是否可用 + 已连接设备 |
| `POST` | `/api/ondevice/install` | `hdc file send` 到设备 `Download` |

#### 4.8.1 `GET /api/ondevice/status`

```json
{
  "ok": true,
  "tool": {
    "cli": "<PROJECT_ROOT>\\harmony-ondevice\\tools\\make_ondevice_model.py",
    "cli_ok": true,
    "converter": "<BUILD_DIR>\\tools\\mslite\\converter-2.5.0\\converter\\converter_lite.exe",
    "converter_ok": true
  },
  "work_dir": "%LOCALAPPDATA%\\VITSStudio\\ondevice-work",
  "work_ascii": true,
  "output_dir": "<PROJECT_ROOT>\\app\\ondevice_out",
  "output_ok": true,
  "tiers": [128, 256, 512, 1024],
  "sample_rate": 22050,
  "format": "vits-ondevice/1",
  "device_download": "/storage/media/100/local/files/Docs/Download/",
  "hdc": { "available": true, "path": "C:\\Program Files\\Huawei\\DevEco Studio\\...\\hdc.exe",
           "devices": ["127.0.0.1:5555"], "message": "已连接：127.0.0.1:5555" },
  "building": false
}
```

`ok:false` 时看 `tool.cli_ok` / `tool.converter_ok` / `work_ascii` / `output_error`，界面会显示缺哪一项。

#### 4.8.2 `GET /api/ondevice/sources`

```json
{
  "sources": [
    { "id": "registry:xya", "name": "xya", "kind": "registry", "kind_label": "registry",
      "config": "<PROJECT_ROOT>\\inference - xya\\finetune_speaker-xya.json",
      "checkpoint": "<PROJECT_ROOT>\\inference - xya\\G_latest-xya.pth",
      "sample_rate": 22050, "symbols_count": 52, "size_mb": 158.9,
      "exportable": true, "reason": "" }
  ],
  "exportable": [ /* 同上，只含可导出的 */ ],
  "skipped": [ /* config/权重缺失或采样率不是 22050 的，附 reason */ ],
  "unsupported": [
    { "id": "HK416", "name": "HK416", "engine": "GPT-SoVITS", "output_sr": 32000,
      "similar": ["registry:HK416"],
      "reason": "GPT-SoVITS 需要 BERT + 自回归 GPT + s1 语义 token + 32 kHz 声码器，端上跑不了（该模型 32000 Hz）；端上只支持经典 VITS 22.05 kHz 模型。同名经典 VITS 模型：registry:HK416，可在导出面板里选它" }
  ],
  "tiers": [128, 256, 512, 1024],
  "sample_rate": 22050
}
```

来源 `id` 有两种：`registry:<名字>`（来自 `vits-upgrade\registry.json`）与
`dir:<目录名>`（工程根目录下 `inference-*` 或 `VITS_ONDEVICE_DIRS` 指定的目录）。

#### 4.8.3 `POST /api/ondevice/build`

请求体：

| 字段 | 类型 | 必需 | 默认 | 说明 |
|---|---|---|---|---|
| `model_id` | str | ✅ | — | `sources` 里的 `id`、registry 模型名，或经典 VITS 目录路径（也可 `onnx:<目录>`） |
| `tiers` | int[] | ❌ | `[128,256,512,1024]` | 语音档位，只能取这四个值；越多越慢、包越大 |
| `output_dir` | str | ❌ | 服务端默认 | zip 落盘目录（不存在会自动创建，不可写会 400） |
| `display` | str | ❌ | 模型默认 | 手机卡片显示名，如 `xya · 星野爱` |
| `speaker` | str | ❌ | — | 传给工具的 speaker 名（多数情况留空） |
| `language` | str | ❌ | 自动 | `ja` / `zh` / `en`；留空按示范句自动选 |
| `keep_work` | bool | ❌ | `false` | 保留中间目录（ONNX/.ms，约 200 MB），排查用 |
| `no_fp16` | bool | ❌ | `false` | mel2wav 不压 fp16（包更大） |

响应 = job 快照（`status: queued`），随后用 `GET /api/ondevice/build/{id}` 轮询：

```json
{
  "id": "1795683469418-hk416",
  "status": "queued", "stage": "queued", "stage_label": "排队中",
  "progress": 0.0,
  "message": "等待执行（打包是单任务串行）",
  "model_id": "hk416",
  "tiers": [128, 256],
  "output_dir": "<PROJECT_ROOT>\\app\\ondevice_out",
  "zip_path": "",
  "result": null, "error": null
}
```

> 打包是**单任务串行**（CPU/内存重）：同时提交多个会排队，`stage` 停在 `queued`。

#### 4.8.4 `GET /api/ondevice/build/{id}`

| 字段 | 说明 |
|---|---|
| `status` | `queued` / `running` / `done` / `error` |
| `stage` | `queued` → `precheck` → `export_onnx` → `convert_text2mel` → `convert_mel2wav` → `pack` → `inspect` → `done`（失败为 `error`） |
| `stage_label` | 上面阶段的中文：排队中 / 检查环境 / **导出 ONNX** / **转换 text2mel** / **转换 mel2wav** / **打包** / 自检产物 / 完成 |
| `progress` | 0.0–1.0（只增不减） |
| `message` | 当前动作，如 `正在转换 mel2wav T512（第 3/4 档）` |
| `logs_tail` | 工具日志尾部（最多 120 行） |
| `duration_s` | 完成后的总耗时（秒） |
| `work_dir` | 中间目录（成功后会在收尾时删除） |
| `result` | 完成时的产物（下） |
| `error` / `error_hint` | 失败原因 + 建议（中文，可直接显示） |

完成时 `result`（本机实测 xya 四档，44.5 s）：

```json
{
  "zip": "<PROJECT_ROOT>\\app\\ondevice_out\\xya.zip",
  "zip_name": "xya.zip",
  "size_bytes": 196838255,
  "size_mb": 196.8,
  "output_dir": "<PROJECT_ROOT>\\app\\ondevice_out",
  "model_id": "xya",
  "tiers": [128, 256, 512, 1024],
  "inspect": { "ok": true, "missing": [], "problems": [], "...": "见 4.8.7" },
  "built_at": "2026-11-26T17:01:25"
}
```

失败时（HTTP 仍是 200，`status=error`）：

```json
{ "status": "error", "stage": "error",
  "error": "缺转换器：<BUILD_DIR>\\tools\\mslite\\converter-2.5.0 下找不到 converter_lite.exe",
  "error_hint": "把 converter-2.5.0（含 converter\\ 与 lib\\）放到该目录，或用环境变量 VITS_CONVERTER 指向它" }
```

#### 4.8.5 `GET /api/ondevice/packages`

```json
{
  "output_dir": "<PROJECT_ROOT>\\app\\ondevice_out",
  "count": 2,
  "packages": [
    { "name": "xya.zip", "path": "D:\\...\\ondevice_out\\xya.zip",
      "size_bytes": 196838255, "size_mb": 196.8,
      "mtime": 1795683685.0, "mtime_text": "2026-11-26 17:01",
      "model_id": "xya", "display": "xya · 星野爱",
      "tiers": [128, 256, 512, 1024], "built_at": "2026-11-26T17:01:25" }
  ]
}
```

可带 `?output_dir=D:\xxx` 查别的目录。

#### 4.8.6 `POST /api/ondevice/inspect`

请求 `{"path": "D:\\...\\xya.zip"}`，返回结构化自检（与 CLI `inspect` 同一套判定）：

```json
{ "ok": true, "name": "xya.zip", "size_mb": 196.8,
  "entries": [{"name":"xya/text2mel.ms","size":30013752,"size_mb":30.01}, "..."],
  "missing": [], "problems": [],
  "meta_summary": { "id": "xya", "speaker": "xya", "display": "xya · 星野爱",
                    "language": "ja", "languages": ["ja","zh"], "output_sr": 22050,
                    "tiers": [128,256,512,1024], "symbols": 52, "prompts": ["ja","zh"] },
  "dur_files": ["xya/dur_ja.json", "xya/dur_zh.json"], "has_readme": true }
```

`ok:false` 时看 `missing`（缺必需文件）与 `problems`（中文原因）。

#### 4.8.7 `POST /api/ondevice/reveal` · `GET /api/ondevice/devices` · `POST /api/ondevice/install`

```powershell
# 资源管理器定位 zip（桌面端按钮用）
curl.exe -s -X POST http://127.0.0.1:7788/api/ondevice/reveal `
  -H "Content-Type: application/json" -d "{\"path\":\"<PROJECT_ROOT>\\app\\ondevice_out\\xya.zip\"}"

# 检测手机
curl.exe -s http://127.0.0.1:7788/api/ondevice/devices
# {"available":true,"path":"...hdc.exe","devices":["127.0.0.1:5555"],"message":"已连接：127.0.0.1:5555"}

# 推到设备 Download（App 里「导入模型」再选它）
curl.exe -s -X POST http://127.0.0.1:7788/api/ondevice/install `
  -H "Content-Type: application/json" -d "{\"zip_path\":\"D:\\...\\xya.zip\"}"
# {"ok":true,"device":"127.0.0.1:5555",
#  "target":"/storage/media/100/local/files/Docs/Download/xya.zip","seconds":3.5,
#  "output":"FileTransfer finish, Size:196838255, File count = 1, time:3443ms rate:57170.56kB/s"}
```

`install` 的请求体也可只给 `{"name":"xya.zip"}`（在默认产物目录里找），可带 `device` 指定目标。

#### 4.8.8 错误码

| 状态码 | 场景 | `detail` 示例 |
|---|---|---|
| `400` | 模型不可导出 / 档位非法 / 目录不可写 / 工具链缺失 | `模型 HK416 不能导出：…`、`档位只能是 [128, 256, 512, 1024]，收到 999` |
| `404` | `job_id` 不存在 | `未知任务: 123`（后端重启后旧任务会丢，界面提示「任务丢失」） |
| `200 + status:error` | **打包过程**失败 | 见 4.8.4；`error_hint` 给处理建议（缺转换器 / 非 22.05 kHz / 内存不足 …） |

> 打包过程失败用 `200 + status=error` 而不是 500：任务在后台线程里跑，HTTP 只是查询状态；
> 这样界面能拿到完整日志尾部。创建任务时的参数错误才走 4xx。

---

### 4.9 添加 / 管理模型：`/api/models/*`

桌面端「＋ 添加模型」「移除模型」按钮用的接口。模型是「目录即模型」：一个目录 = 一个模型
（`sovits` 权重 + `gpt` 权重 + `ref.wav` + `ref.txt` + `model.json`），结构与字段见
[ADD_MODEL.md](ADD_MODEL.md)。

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/api/models/scan` | 扫描一个本地目录，列出权重/参考音/文本并给推荐选择 |
| `POST` | `/api/models/install` | 安装：拷贝进模型库，或只登记原路径 |
| `POST` | `/api/models/import_hf` | 起一个 HuggingFace 下载任务（立即返回 job id） |
| `GET` | `/api/models/import_hf/{job_id}` | 查下载进度；完成时顺手重扫并附带新模型列表 |
| `POST` | `/api/models/remove` | 把模型目录移到 `_removed\`（可恢复） |

#### 4.9.1 `POST /api/models/scan`

递归扫描一个目录（含所有子目录），列出 `*.pth/*.pt/*.ckpt` 权重、`*.wav` 参考音、`*.txt/*.lab/*.list` 文本，
并给出一组推荐选择。**只读，不写任何文件**。

请求体：

| 字段 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `path` | str | ✅ | 要扫描的目录（绝对路径或 `~` 开头）；不存在/不是目录 → 400 |

响应 200：

```json
{
  "root": "D:\\下载\\某个模型",
  "weights": [
    { "path": "D:\\...\\x_s2.pth",   "name": "x_s2.pth",   "mb": 158.9, "kind": "sovits", "version": "v2Pro" },
    { "path": "D:\\...\\y_e10.ckpt", "name": "y_e10.ckpt", "mb": 148.1, "kind": "gpt",    "version": "" }
  ],
  "wavs":  [ { "path": "D:\\...\\ref.wav", "name": "ref.wav", "sec": 5.2 } ],
  "texts": [ { "path": "D:\\...\\ref.txt", "name": "ref.txt",
               "preview": "こんにちは…", "text": "こんにちは…" } ],
  "suggest": { "s2": "D:\\...\\x_s2.pth", "s1": "D:\\...\\y_e10.ckpt",
               "ref_wav": "D:\\...\\ref.wav", "ref_txt": "D:\\...\\ref.txt" }
}
```

- `weights[].kind`：`sovits` / `gpt`；只有 SoVITS 才有 `version`（读权重前 2 字节，识别不出为空串）。
- 小于 1 KB 的权重/音频、小于 2 字节的文本会被跳过；`preview` 取前 80 字、`text` 取前 500 字。
- `suggest` 的挑法：SoVITS 取「能识别版本的里面最大的」，识别不出就取最大的权重；GPT 取最大的 GPT 权重
  （没有 GPT 时退而取任意非 SoVITS 权重）；参考音/文本取排在最前的一个。界面会把这些值填进表单，用户可改。

失败码：`400`（`目录不存在：…`、`这个目录里没有 .pth/.ckpt 权重`）。

#### 4.9.2 `POST /api/models/install`

把已有的权重安装进模型库（安装完自动重扫，响应里直接带回新列表）。

请求体：

| 字段 | 类型 | 必需 | 默认 | 说明 |
|---|---|---|---|---|
| `s2` | str | ✅ | — | SoVITS 权重路径（`.pth/.pt`） |
| `s1` | str | ✅ | — | GPT 权重路径（`.ckpt/.pth/.pt`） |
| `ref_wav` | str | ✅ | — | 参考音，3–10 秒干净单人语音 |
| `ref_txt` | str | ❌ | `""` | 参考音文本**文件**（与 `ref_text` 二选一） |
| `ref_text` | str | ❌ | `""` | 直接给文本，**优先于** `ref_txt` |
| `source_dir` | str | ❌ | `""` | 只写进 `model.json` 的 `note`（"本地安装，来源 …"），不参与拷贝 |
| `name` | str | ❌ | 同 `speaker` | 目录名 / 模型 `id`；非法字符替换成 `_`；同名目录已含 `model.json` 会 400 |
| `speaker` | str | ❌ | 同 `name` | 显示名 |
| `language` | str | ❌ | `ja` | 只取前 2 位；**同时写入 `language` 和 `ref_lang`** |
| `mode` | str | ❌ | `copy` | `copy` 拷进模型库（自包含）/ `register` 只写 `model.json` 指向原路径（零拷贝） |

响应 200：

```json
{
  "id": "xya",
  "folder": "<PROJECT_ROOT>\\community_models\\xya",
  "sovits_version": "v2Pro",
  "engine_version": "v2Pro",
  "mode": "copy",
  "models": [ /* 重扫后的完整列表，结构同 GET /api/models */ ]
}
```

- 落点**固定**是 `<PROJECT_ROOT>\community_models\<name>`（`installer.LIBRARY`），不是 `models_out`；
  只有它在 `--models` 的扫描根里才会出现在模型列表里（服务默认三个根已包含它）。
- `engine_version` 由 s2 权重前 2 字节识别，识别不出回退 `v2Pro`；`version` 固定写 `local-1`。
- ⚠️ **`ref_lang` 跟 `language` 走**（本地安装）。参考音不是日语时务必传对应的 `language`，否则音色会跑偏；
  HF 导入则相反——固定写 `ja`。
- 不论 `copy` 还是 `register`，`ref.wav` 与 `ref.txt` 都会写进模型目录。

失败码：`400`（`找不到 s2 权重：…`、`找不到 s1 权重：…`、`必须提供参考音 ref.wav`、
`必须提供参考音对应的文本（ref.txt 内容）`、`模型已存在：xya（改个名字或先移除）`）；`422` 缺 `s2`/`s1`。

#### 4.9.3 `POST /api/models/import_hf` 与 `GET /api/models/import_hf/{job_id}`

从 HuggingFace（走 `hf-mirror.com`）下载权重并安装。**异步**：POST 立即返回 job id，进度靠 GET 轮询。

POST 请求体：

| 字段 | 类型 | 必需 | 默认 | 说明 |
|---|---|---|---|---|
| `repo` | str | ✅ | — | `owner/name`，例如 `4nm1tsu/shikokumetan_GPT-SoVITS`；不含 `/` → 400 |
| `name` | str | ❌ | 仓库名 | 安装目录名（会去掉 `GPT-SoVITS-` 前缀并转小写） |
| `speaker` | str | ❌ | 同 `name` | 显示名 |
| `language` | str | ❌ | `ja` | 文本语言（参考音语言固定 `ja`） |
| `s2` | str | ❌ | 自动挑 | 仓库内相对路径；不传则挑最大的 SoVITS 权重 |
| `s1` | str | ❌ | 自动挑 | 仓库内相对路径；不传则挑最大的 GPT 权重 |
| `ref_text` | str | ❌ | 空 | 参考音文本（配合默认借用的参考音） |

POST 响应 200：

```json
{ "job": "1795683469418" }
```

GET 轮询（`status`：`running` / `done` / `error`）：

```json
{
  "id": "1795683469418",
  "repo": "4nm1tsu/shikokumetan_GPT-SoVITS",
  "status": "running",
  "progress": 0.42,
  "message": "下载 s2G.pth",
  "model": null,
  "error": null
}
```

完成时 `model` 有值，并附带重扫后的模型列表：

```json
{
  "status": "done", "progress": 1.0, "message": "完成",
  "model": { "id": "shikokumetan", "sovits_version": "v2Pro",
             "engine_version": "v2Pro",
             "folder": "<PROJECT_ROOT>\\community_models\\shikokumetan" },
  "models": [ /* 同 GET /api/models */ ]
}
```

失败时 `status` 为 `"error"`、`error` 为 `类型: 原因`（**仍是 HTTP 200**）。
未知 `job_id` 返回 `200 {"id":"…","status":"unknown"}`，**不是 404**（服务重启后旧任务会丢）。

- 自动挑选优先认 SoVITS/GPT 形态的文件，排除 `G_` / `D_`（GAN 生成器/判别器）和 `logs/` 下的训练中间权重；
  两类都找不到时才回退到「最大的权重」。
- 不传参考音时**借用** `models_out\Tong\ref.wav` 及其文本（社区包多数不带参考音）→ 音色会像 Tong，
  要贴近角色需自己换 `ref.wav` + `ref.txt`。
- 装完 `version` 固定 `community-1`，`ref_lang` 固定 `ja`。

#### 4.9.4 `POST /api/models/remove`

把模型移出模型库（**移动，不删除**）。

请求体：`{ "id": "xya" }`（模型 `id`，即目录名）。

响应 200：

```json
{
  "ok": true,
  "moved_to": "<PROJECT_ROOT>\\_removed\\xya_1795683986",
  "models": [ /* 重扫后的列表 */ ]
}
```

- 目标是**第一个扫描根目录旁的 `_removed\`**（默认 `<PROJECT_ROOT>\_removed`），目录名加 `_<时间戳>` 后缀；
  移回原位置 + `/api/models/reload` 即可恢复。
- 只认目录名 `id`（不认 `speaker` 显示名）；不存在 → `404 未知模型: xxx`。
- 正在合成的模型被移除不会中断当前推理，但下一次请求会 404。

#### 4.9.5 错误码

| 状态码 | 场景 | `detail` 示例 |
|---|---|---|
| `400` | 扫描/安装/导入的参数或环境问题 | `目录不存在：…`、`这个目录里没有 .pth/.ckpt 权重`、`必须提供参考音 ref.wav`、`模型已存在：xya（改个名字或先移除）`、`repo 需要形如 owner/name` |
| `404` | `remove` 的模型不存在 | `未知模型: xya` |
| `422` | 缺必填字段（`path` / `s2` / `s1` / `repo` / `id`） | FastAPI 自动产生，`detail` 是数组 |

> 下载任务**不阻塞 HTTP**：`import_hf` 立即返回 id，进度靠轮询；下载失败是 `status:"error"` 而不是 500，
> 只有创建任务时的参数错误才走 4xx。这与 4.8 的打包任务是同一套约定。

---

## 5. 端到端最小示例

```powershell
# 1) 后端活着吗？
curl.exe -s http://127.0.0.1:7788/api/health

# 2) 有哪些模型？
curl.exe -s http://127.0.0.1:7788/api/models

# 3) 合成一句并保存
[IO.File]::WriteAllText("$PWD\body.json", '{"model":"HK416","text":"今日はいい天気ですね。","lang":"ja"}', (New-Object Text.UTF8Encoding $false))
curl.exe -s -D headers.txt -o out.wav -X POST http://127.0.0.1:7788/api/tts -H "Content-Type: application/json" --data-binary "@body.json"

# 4) 校验产物：应为 44 字节 WAV 头 + PCM 数据，Content-Type: audio/wav
Get-Content headers.txt
Get-Item out.wav | Select-Object Length

# 5) 让服务端把刚才那段音频落到「下载\VITS Studio」（桌面 UI「下载 WAV」用这个）
[IO.File]::WriteAllText("$PWD\save.json", '{"filename":"HK416_demo.wav"}', (New-Object Text.UTF8Encoding $false))
curl.exe -s -X POST http://127.0.0.1:7788/api/audio/save -H "Content-Type: application/json" --data-binary "@save.json"
```

---

## 6. 常见坑清单

1. **把 `/api/tts` 的响应当 JSON 解析** → 成功时是 WAV 二进制；只有出错才是 JSON。
2. **首次请求超时太短** → 首次合成要先加载模型（约 15 秒），再加单句推理（本机实测 12 字 5.25 秒）。客户端超时至少 60 秒，建议 180 秒。
3. **切换模型后第一句慢** → 12 GB 显存只驻留一个模型，换模型必然重新加载。
4. **`lang` 传 `auto`** → 被截断成 `au`，合成失败；请传 `zh` / `ja` / `en`。
5. **文本超过 500 字** → 400；请客户端先分段（按标点切）。
6. **鸿蒙连不上** → 手工启动时必须 `--host 0.0.0.0`，手机与电脑同一局域网，防火墙放行 7788。
7. **想连 EXE 里的服务** → 端口是每次随机的，且只绑 `127.0.0.1`，局域网连不上。鸿蒙联调请用 `--host 0.0.0.0 --port 7788` **手工启动**后端。
8. **`size_mb` 不是磁盘占用** → 只统计两个权重文件，不含 `ref.wav`、`sample/` 等。
9. **`/api/shutdown` 会用 `os._exit` 退出** → 没有优雅关闭、不刷日志，属正常现象。
10. **装了模型却在列表里看不到** → 安装/导入都落在 `community_models\`；手工启动时若用 `--models` 覆盖了扫描根，必须把它带上（EXE 默认扫 `models_out;VITS标准模型;community_models`），装完调一次 `/api/models/reload`。
11. **`/api/models/remove` 不是删除** → 目录被移到第一个扫描根旁的 `_removed\`，磁盘空间不会回收；要彻底清理请手动删该目录。
12. **参考音语言写错导致音色跑偏** → `install` 的 `ref_lang` 取请求里的 `language`（本地安装），HF 导入则固定 `ja`；参考音不是日语时务必显式传 `language`。
13. **桌面端用 `blob:` + `<a download>` 下载 WAV** → pywebview/WebView2 不接管下载事件，点击会**完全静默**（无文件、无对话框、无报错）。桌面/浏览器统一改调 `POST /api/audio/save`，见 4.4.1。

---

## 7. 相关文档

- [README.md](../README.md) —— 项目总览、启动与打包
- [ADD_MODEL.md](ADD_MODEL.md) —— 新增模型 / 零样本模型
- [harmony/README.md](../harmony/README.md) —— 鸿蒙 APP 与局域网联调
