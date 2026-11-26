# 新增模型指南（目录即模型）

> 适用版本：VITS Studio 1.0+ / GPT-SoVITS **v1 / v2 / v2Pro / v2ProPlus**（社区模型直接可用；v3/v4 需额外底模，见 [§10](#10-社区开源模型直接可用)）
>
> 核心规则：**一个子目录 = 一个模型**。往模型目录里放一个符合结构的文件夹，重新扫描一次就多一个模型——不用改任何代码，Web UI、桌面 EXE、鸿蒙 APP 都会自动看到。

默认模型目录：

```text
<PROJECT_ROOT>\models_out\
```

可通过启动参数覆盖：`server.py --models "D:\其它目录"`。

---

## 1. 目录结构

```text
<PROJECT_ROOT>\models_out\
├── HK416\                  ← 目录名 = 模型 id（调用 /api/tts 时用这个值）
│   ├── model.json          ← 可选：显示名、语言、备注、权重路径
│   ├── sovits.pth          ← 必需：SoVITS (s2) 权重
│   ├── gpt.ckpt            ← 可选：GPT (s1) 权重，缺省回退官方底模 s1v3.ckpt
│   ├── ref.wav             ← 必需：参考音（3–10 秒）
│   ├── ref.txt             ← 必需：参考音的逐字文本
│   ├── sample\             ← 可选：试听样例，程序不读
│   └── README.md           ← 可选：随便写，程序不读
├── Tong\
└── xya\
```

**扫描规则**（`app/backend/registry.py`）：

- 只扫描模型目录的**第一层子目录**，不递归成更多模型。
- 每个子目录内部会向下最多 **3 层**探测权重/参考音，兼容社区包常见的
  `SoVITS_weights/`、`GPT_weights/`、`dataset/` 布局。
- 目录按名称**升序**排序后返回，可用的模型排在前面。
- 目录里多出来的文件（`infer.ps1`、`README.md`、`sample/`、日志……）一律忽略，不影响加载。
- `model.json` 解析失败时不再静默：会自动探测文件，并把「model.json 解析失败」写进
  `/api/models` 的 `warnings`，UI 卡片 tooltip 可见。
- 没有 `model.json` 时也能发现模型：只要探测到 `*.pth/*.pt/*.ckpt` 或参考音就会列出；
  缺什么由 `missing` 字段返回，UI 显示「未就绪 + 缺什么」。

### 文件清单

| 文件 | 必需性 | 说明 |
|---|---|---|
| `sovits.pth` | **必需** | SoVITS（s2）权重，决定音色。文件名可任意（`*.pth/*.pt`），缺失 → `ready: false`，卡片标「缺权重」 |
| `gpt.ckpt` | 可选 | GPT（s1）权重，决定韵律/语气。缺失时按 `engine_version` 自动选官方底模（v2Pro/v2ProPlus 用 `s1v3.ckpt`，v1/v2 用各自官方 s1；缺失时回退 `s1v3.ckpt`） |
| `ref.wav` | **必需** | 参考音，音色克隆的「样本」。缺失 → `ready: false`，卡片标「缺参考音」 |
| `ref.txt` | **必需** | 参考音的**逐字**文本，必须与 `ref.wav` 内容一致；缺失 → `ready: false` |
| `model.json` | 可选 | 元信息 + 引擎版本。不写则：`name` = 目录名、`language` = `ja`、版本自动识别 |
| `config.json` | 可选 | 社区训练配置；会自动读出 `sampling_rate` / `add_blank` / `symbols`（见 §10） |

> ⚠️ **引擎版本可配**：`model.json` 的 `engine_version` 支持 `v1` / `v2` / `v2Pro` /
> `v2ProPlus`。不写时先读权重前 2 字节自动识别，识别不出才回退 `v2Pro` 并在 `warnings` 里提示。
> 不要为了「让它能跑」乱写版本：GPT-SoVITS 最终以权重内嵌版本为准，写错会得到一条明确的
> 「声明版本 vs 实际版本」警告。

---

## 2. `model.json` 字段表

所有字段都可省略。路径字段支持**绝对路径**或**相对模型目录**的相对路径。

| 字段 | 类型 | 默认 | 作用 |
|---|---|---|---|
| `speaker` | string | 目录名 | 显示名（`/api/models` 的 `name`）。UI 卡片标题、顶部标题用它 |
| `language` | string | `"ja"` | **两字母**语言码，只保留前 2 位。它同时决定两件事：① 合成的默认文本语言；② **参考音语言**（见 [第 5 节](#5-参考音与-ref_lang-注意)） |
| `engine_version` | string | 自动识别 / `v2Pro` | **引擎版本**：`v1` / `v2` / `v2Pro` / `v2ProPlus`（也接受 `v2-pro`、`v2pro` 等写法）。社区包必填时优先信它；不写则读权重头 |
| `sovits_version` | string | 空 | 旧字段，等价于 `engine_version`；新包建议用 `engine_version` |
| `config_path` | string | `config.json` | 可选：训练配置路径（相对模型目录）。不写会自动找 `config.json` / `s2_config.json` / `configs/config.json` |
| `finetuned` | string | `""` | 微调范围备注，例如 `"s1+s2"`。仅展示 |
| `note` | string | `""` | 备注，例如数据来源、训练条数说明。仅展示 |
| `train_lines` | int | `0` | 训练语料条数。仅展示；写 `"75"` 这种字符串也能被正确转换 |
| `gpt_weights` | string | `gpt.ckpt` | GPT(s1) 权重路径。指向不存在的文件会**自动改探测同目录权重**并写 warning；都没有则用官方底模 |
| `sovits_weights` | string | `sovits.pth` | SoVITS(s2) 权重路径。指向不存在的文件会自动探测；仍找不到 → `ready: false` |
| `ref_audio` | string | `ref.wav` | 参考音路径，支持子目录（如 `dataset/xxx.wav`） |
| `ref_text` | string | `ref.txt` | 参考音文本文件路径（UTF-8 文本），支持子目录 |

其它字段（现有 `model.json` 里的 `engine`、`train_minutes`）会被忽略，留着只是给人看的。

> 路径字段支持**绝对路径**或**相对模型目录**的相对路径。`model.json` 里写错/文件被移动时，
> 会尽量回退到自动探测结果，并在 `/api/models` 的 `warnings` 里说明改用了哪个文件。

### 示例一：最简（推荐）

不写 `model.json`，目录名当显示名：

```text
models_out\MyVoice\sovits.pth
models_out\MyVoice\gpt.ckpt
models_out\MyVoice\ref.wav
models_out\MyVoice\ref.txt
```

显示为 `MyVoice`、语言 `ja`、`finetuned` 空、`train_lines` 0。

### 示例二：完整 `model.json`（相对路径）

```json
{
  "speaker": "MyVoice",
  "language": "zh",
  "engine_version": "v2Pro",
  "finetuned": "s1+s2",
  "note": "自建语料 320 条，demucs 去伴奏后切分",
  "train_lines": 320,
  "gpt_weights": "gpt.ckpt",
  "sovits_weights": "sovits.pth",
  "ref_audio": "ref.wav",
  "ref_text": "ref.txt"
}
```

### 示例三：社区包（权重/参考音都在子目录）

```text
community_models\SomeCharacter\
├── SoVITS_weights\some_e8_s200.pth
├── GPT_weights\some-e10.ckpt
├── dataset\line_001.wav
├── dataset\line_001.txt
└── config.json                 ← 可选；程序自动读 sampling_rate/add_blank/version
```

不写 `model.json` 也能被发现（权重在子目录、参考音在 `dataset/` 都支持）。想显式指定时：

```json
{
  "speaker": "SomeCharacter",
  "language": "ja",
  "engine_version": "v2Pro",
  "sovits_weights": "SoVITS_weights/some_e8_s200.pth",
  "gpt_weights": "GPT_weights/some-e10.ckpt",
  "ref_audio": "dataset/line_001.wav",
  "ref_text": "dataset/line_001.txt"
}
```

### 示例四：权重在训练输出目录（不拷贝，直接引用）

```json
{
  "speaker": "MyVoice",
  "language": "zh",
  "finetuned": "s1+s2",
  "note": "直接引用 GPT-SoVITS 训练输出目录",
  "train_lines": 320,
  "gpt_weights": "<VITS_ENV>\\GPT-SoVITS\\GPT_weights_v2Pro\\myvoice-e10.ckpt",
  "sovits_weights": "<VITS_ENV>\\GPT-SoVITS\\SoVITS_weights_v2Pro\\myvoice_e12_s300.pth",
  "ref_audio": "<PROJECT_ROOT>\\models_out\\MyVoice\\ref.wav",
  "ref_text": "<PROJECT_ROOT>\\models_out\\MyVoice\\ref.txt"
}
```

> JSON 里 Windows 路径的反斜杠必须写成 `\\`。写错（如 `\v`、`\G`）会让 `model.json` 解析失败，模型静默回退默认值。

真实样例可直接参考 `<PROJECT_ROOT>\models_out\HK416\model.json`。

---

## 3. 从训练产物拿到权重

GPT-SoVITS 训练结束后，权重输出在这两个目录：

| 权重 | 输出目录 | 文件示例 |
|---|---|---|
| SoVITS (s2)，决定音色 | `<VITS_ENV>\GPT-SoVITS\SoVITS_weights_v2Pro\` | `myvoice_e12_s300.pth` |
| GPT (s1)，决定韵律 | `<VITS_ENV>\GPT-SoVITS\GPT_weights_v2Pro\` | `myvoice-e10.ckpt` |

本机现有的都是 v2Pro 产物，例如：

```text
SoVITS_weights_v2Pro\hk416wiki_e20_s300.pth
SoVITS_weights_v2Pro\xya_e12_s300.pth
SoVITS_weights_v2Pro\tong_e12_s504.pth
GPT_weights_v2Pro\hk416wiki-e10.ckpt
GPT_weights_v2Pro\xya-e10.ckpt
GPT_weights_v2Pro\tong-e10.ckpt
```

命名含义：`e` = epoch，`s` = step。**s1 和 s2 要选同一次实验、同一阶段**（例如都取 `e10`/`s300` 那一轮），混搭会导致音色和韵律不匹配。

### 步骤（PowerShell）

```powershell
# 1) 建模型目录（目录名就是模型 id，建议用短英文/数字）
$modelDir = "<PROJECT_ROOT>\models_out\MyVoice"
New-Item -ItemType Directory -Force -Path $modelDir | Out-Null

# 2) 拷权重并改名成约定的 sovits.pth / gpt.ckpt
Copy-Item "<VITS_ENV>\GPT-SoVITS\SoVITS_weights_v2Pro\myvoice_e12_s300.pth" "$modelDir\sovits.pth"
Copy-Item "<VITS_ENV>\GPT-SoVITS\GPT_weights_v2Pro\myvoice-e10.ckpt"        "$modelDir\gpt.ckpt"

# 3) 放参考音与文本
Copy-Item "D:\某处\myvoice_ref.wav" "$modelDir\ref.wav"
[IO.File]::WriteAllText("$modelDir\ref.txt", "参考音里说的那句话。", (New-Object Text.UTF8Encoding $false))

# 4) 确认文件齐了
Get-ChildItem $modelDir | Select-Object Length, Name
```

也可以**不拷**权重，改用 `model.json` 指向训练输出目录（见示例四），省磁盘；但迁移/打包时容易漏掉，备份时留意。

---

## 4. 让新模型出现

### 桌面 / Web UI

点左下角「**重新扫描模型**」，或调用：

```powershell
curl.exe -s -X POST http://127.0.0.1:7788/api/models/reload
```

### 鸿蒙 APP

不需要任何操作：鸿蒙端每次都通过 `GET /api/models` 拉列表，**自动出现**。下拉刷新/重进页面即可。

### 验证

```powershell
# 列表里应能看到新模型，ready 为 true
curl.exe -s http://127.0.0.1:7788/api/models

# 试合成一句（首次会加载模型，约 15 秒）
[IO.File]::WriteAllText("$PWD\body.json", '{"model":"MyVoice","text":"你好，我是新模型。","lang":"zh"}', (New-Object Text.UTF8Encoding $false))
curl.exe -s -D headers.txt -o out.wav -X POST http://127.0.0.1:7788/api/tts -H "Content-Type: application/json" --data-binary "@body.json"
Get-Content headers.txt
```

接口细节见 [API.md](API.md)。

---

## 5. 参考音与 ref_lang 注意

### 参考音怎么选

| 要求 | 说明 |
|---|---|
| **3–10 秒** | 太短（<2 s）信息不足，音色飘；太长（>15 s）首次加载/推理变慢，收益很小 |
| **干净** | 无 BGM、无混响、无他人抢话、无电流声。人声分离后的干声最好 |
| **情绪平稳** | 用平稳陈述句，别用尖叫/大笑/唱歌，否则合成容易带情绪惯性 |
| **与 `ref.txt` 逐字一致** | 文本少了标点、多了语气词都会降低相似度；不要用「翻译版」文本 |
| **格式** | 单声道 WAV、16-bit；16 kHz 以上即可（引擎内部会重采样）。**不要**用 MP3 改名成 `.wav` |
| **不要削波** | 音量拉满导致爆音会显著影响音色 |

`ref.txt` 要求：UTF-8 编码、一行、只写参考音里真实说出的内容（程序会 `strip()`，多余空行会被忽略但没意义）。

### ref_lang 是什么、为什么固定 `ja`

- **请求体里没有 `ref_lang` 字段**（`/api/tts` 只接受 `model/text/lang/speed/temperature`）。
- 后端把「参考音语言」固定取**模型 `model.json` 的 `language`**：本机三个模型的参考音都是日语，所以参考音语言恒为 `ja`。
- 请求里的 `lang` 只控制「**要读的文本**」语言，两者互不影响。

所以：

| 场景 | `model.json` 的 `language` | 请求里的 `lang` | 结果 |
|---|---|---|---|
| 日语参考音 + 读中文（Tong 的常见用法） | `ja` | `zh` | ✅ 正常，中文读出来仍是该角色音色 |
| 日语参考音 + 读日语 | `ja` | `ja` | ✅ 正常 |
| 中文参考音 + 读中文（新增中文模型） | **必须写 `zh`** | `zh` | ✅ 正常 |
| 中文参考音但 `language` 留默认 `ja` | `ja`（错） | `zh` | ❌ 参考音语言判断错，音色/吐字明显变差 |

**结论**：新增模型时，`language` 必须填**参考音本身的语言**，而不是「你以后想读的语言」。想读别的语言，在请求里改 `lang` 就行。

---

## 6. 零样本模型（不训练也能加一个）

GPT-SoVITS 支持零样本克隆：只要有参考音，就能用官方底模合成，**不需要训练**。

### 极简做法（3 个文件）

```powershell
$dir = "<PROJECT_ROOT>\models_out\ZeroVoice"
New-Item -ItemType Directory -Force -Path $dir | Out-Null

# 1) 用官方底模 SoVITS 顶替（154.8 MiB，约 162 MB，零样本）
Copy-Item "<VITS_ENV>\GPT-SoVITS\GPT_SoVITS\pretrained_models\v2Pro\s2Gv2Pro.pth" "$dir\sovits.pth"

# 2) 参考音 + 文本（换成你自己的 3–10 秒干净人声）
Copy-Item "D:\某处\who_ref.wav" "$dir\ref.wav"
[IO.File]::WriteAllText("$dir\ref.txt", "参考音对应的文本", (New-Object Text.UTF8Encoding $false))

# 3) 可选：不写 gpt.ckpt，后端会用官方 s1v3.ckpt
```

目录最终长这样：

```text
models_out\ZeroVoice\
├── sovits.pth     ← 官方 s2Gv2Pro.pth 的拷贝
├── ref.wav
└── ref.txt
```

然后 `POST /api/models/reload`，列表里会出现 `ZeroVoice`，特征为：

- `has_gpt: false`（用官方 s1）
- `finetuned: ""`、`train_lines: 0`
- `size_mb` ≈ 162.3（`s2Gv2Pro.pth` 是 154.8 MiB，接口按十进制 MB 算）

### 效果与限制

- **音色相似度明显低于微调模型**：零样本只靠参考音模仿，长句容易漂、语气不稳。
- 参考音质量的影响被放大：**3–10 秒、干净、文本一致**这三点比微调模型更关键。
- 想提升：换更好的参考音 → 再考虑拿 100 条以上数据做 s1+s2 微调（见第 3 节）。
- 零样本适合「先听听像不像」「临时给新角色占个位」，不适合正式发布。

---

## 7. 排错

| 现象 | 原因 | 处理 |
|---|---|---|
| 列表里没有新模型 | 目录不是模型的**第一层**子目录；或没重新扫描 | 确认直接在模型根目录下；点「重新扫描模型」或 `POST /api/models/reload` |
| `ready: false` / 卡片「缺权重」 | `sovits.pth` 不存在，或 `model.json` 的 `sovits_weights` 路径写错 | 检查权重文件；相对路径是相对模型目录；看 `missing` 字段 |
| `ready: false` / 卡片「缺参考音」 | `ref.wav` 或 `ref.txt` 缺失（现在参考音缺失也会提前标记，不再等到合成才 500） | 补齐 `ref.wav` + `ref.txt`，或用「＋ 添加模型」补参考音 |
| 合成 400「未就绪：…」 | `ready: false` | 按 `missing` 列表补齐；`POST /api/models/reload` |
| 卡片显示「社区」但引擎版本不对 | `model.json` 写了 `engine_version`，与权重头不一致 | 看 `warnings`；GPT-SoVITS 以权重内嵌版本为准，实际加载版本会打印在 `logs\server.log` |
| 加载 500「引擎版本 v3/v4 暂不可用」 | 当前只装了 v1/v2/v2Pro/v2ProPlus 底模 | 补 `s2Gv3.pth`/`vocoder` 等（见 §10），否则换 v1/v2/v2Pro/v2ProPlus 权重 |
| 加载 500「不支持的 engine_version」 | `engine_version` 写成了 v9 之类 | 改成 `v1` / `v2` / `v2Pro` / `v2ProPlus`，或删掉让权重头自动识别 |
| 合成 500，信息含 `KeyError` / `state_dict` / `load` | 权重损坏、s1/s2 不配套，或声明的版本与权重结构不符 | 换成同一次实验的 `*_e*_s*.pth` + `*-e*.ckpt`；看错误里的 s2/s1 文件名 |
| 显示名变成目录名、语言变成 `ja` | `model.json` JSON 语法错误（反斜杠没转义、尾逗号等） | 看 `warnings` 的「model.json 解析失败」；用 `python -m json.tool "路径\model.json"` 校验 |
| 合成出来不是这个人 | 参考音不干净/文本不匹配；或 s1 与 s2 来自不同实验轮次 | 换参考音；核对权重文件名 |
| 换了模型第一句等很久 | 正常：12 GB 显存只驻留一个模型，切换要重新加载（约 10–15 秒） | 无需处理 |
| 新模型和旧模型同时用就爆显存 | 显存不足（加载后模型会占掉大部分显存） | 一次只用一个模型；不要并行调用 |
| 目录名带空格/中文，客户端不好传 | 目录名直接当 `id` | 建议目录名用短英文/数字（如 `MyVoice`） |

---

## 8. 相关文档

- [API.md](API.md) —— `/api/models`、`/api/models/reload`、`/api/tts` 的完整字段与示例
- [../README.md](../README.md) —— 启动、打包、常见问题
- [../harmony/README.md](../harmony/README.md) —— 鸿蒙端如何自动发现新模型

---

## 9. 标准模型包与多根目录（训练 AI 产出）

「专职训练 VITS 的 AI」除了 `models_out\`，还会产出可直接分发的**标准模型包**：

```text
VITS标准模型\<角色>_VITS模型\
  <角色>_e<epoch>_s<step>.pth   # s2 / SoVITS 权重
  <角色>-e<epoch>.ckpt          # s1 / GPT 权重
  ref.wav / ref.txt             # 参考音
  model.json                    # 带 version / ref_lang / output_sr
  metrics.json                  # CER / F0 / RTF / timbre_self
  MODEL_CARD.md / README.md / SHA256SUMS.txt / 试听.bat
```

应用默认同时扫描三个根目录：

```
<PROJECT_ROOT>\models_out
<PROJECT_ROOT>\VITS标准模型
<PROJECT_ROOT>\community_models
```

- 同一 `id` 出现在两个根目录时，**保留版本号最高**的一份（版本号取 `model.json.version`）。
- 若根目录有 `manifest.json`，其中的 `metrics` 会合并进 `/api/models`，界面上显示为 `v1.2.0`、`CER 0.12` 标签。
- 想加第三、第四个根目录：启动参数 `--models "A;B;C"`，或在 `VITS Studio.exe` 同目录的 `config.json` 里写
  `{"models_dir": "A;B;C"}`。

> 标准包里的 `model.json` 用的是**相对路径**（权重文件名与产物名一致），所以整个文件夹可以整体拷贝到别的机器，
> 只要目标机器有 GPT-SoVITS 运行时即可。

---

## 10. 社区开源模型（直接可用）

从 HuggingFace / 群友手里拿到的社区 GPT-SoVITS 模型，**不需要转换、不需要改代码**，
放进 `community_models\` 即可。应用默认按顺序扫描三个根目录：

```
models_out          （训练 AI 的产物）
VITS标准模型         （打包分发的标准包）
community_models    （社区下载的模型）
```

### 10.1 支持版本矩阵（本机实测）

| `engine_version` / 权重头 | 状态 | 实测 |
|---|---|---|
| `v1`（`00` 或老版 `PK` + 文件 < 82,978 KiB） | ✅ 加载 + 合成 | `Theresa_e100_s2700.pth`（84,931,549 B，PK 头）实际识别为 **v1**；32 kHz / 2.10 s / RMS 0.125 |
| `v2`（`01`，或老版 `PK` 约 84.9–700 MB） | ✅ 加载 + 合成（本地构造 v2 形状权重） | 本机没有真实 v2 公开权重；用 v1 权重按 v2 词表（322→732）与 `ref_enc` 704 维构造的 checkpoint 实测加载，实际 v2 / 32 kHz / 2.99 s / RMS 0.122；**非真实 v2 音质**，只证明 v2 网络分支可用 |
| `v2Pro`（`05`） | ✅ 加载 + 合成 | 无 `model.json` 的假社区包（权重/参考音在子目录）32 kHz / 1.51 s / RMS 0.098；`4nm1tsu/shikokumetan_GPT-SoVITS` 同样通过 |
| `v2ProPlus`（`06`） | ✅ 加载 + 合成 | `Richopera/Yangyang-gpt-sovits-v2proplus`，32 kHz；上采样/gin 参数与 v2Pro 不同，必须靠自动识别 |
| `v3`（`02`/`03`） | ❌ 明确报错 | 缺 `s2Gv3.pth` + bigvgan vocoder；引擎在 `torch.load` 前就报「引擎版本 v3 暂不可用」 |
| `v4`（`04`） | ❌ 明确报错 | 缺 `gsv-v4-pretrained/s2Gv4.pth` + `vocoder.pth` |
| `v5dev` / `v5turbo`（`07`/`08`） | ❌ 明确报错 | 缺 v5 底模；当前环境未安装 |
| 未知值（如 `v9`） | ⚠️ 自动纠正 | 若权重头能识别，按权重头加载并写 warning；识别不出才回退 `v2Pro` |

> **v2 为什么是「构造权重」？** 本机只有 v1 / v2Pro / v2ProPlus 真实权重；v2 与 v1 的结构差异是
> text embedding 词表 322→732，以及 `ref_enc` 频谱输入 1025→704。§10.9 里的 `SynthV2` 用真实 v1
> 权重按 v2 结构改造后实测加载/合成通过，证明 **v2 网络分支和版本参数都可用**；但它不是真实 v2
> 训练产物，音质不作数。拿到真实 v2 权重时走的是同一条路径。

### 10.2 必需文件与允许的组合

| 组合 | 是否可用 | 说明 |
|---|---|---|
| `sovits.pth` + `gpt.ckpt` + `ref.wav` + `ref.txt` | ✅ 最佳 | 最常见；文件名任意，放子目录也行 |
| `sovits.pth` + `ref.wav` + `ref.txt` | ✅ | 缺 GPT(s1) 时按版本用官方底模：v2Pro/v2ProPlus → `s1v3.ckpt`；v1/v2 → 各自官方 s1，缺失时回退 `s1v3.ckpt`（日志会提示） |
| 只有 `sovits.pth`（无参考音） | ⚠️ 列出但未就绪 | 卡片标「缺参考音」，点卡片提示补参考音，合成不会静默失败 |
| 权重/参考音在子目录 | ✅ | 最多向下 3 层探测；`dataset/xxx.wav` + `dataset/xxx.txt` 会自动配对 |
| 没有 `model.json` | ✅ | 自动探测权重/参考音/`config.json`；显示名 = 目录名 |
| 只有 `config.json` 没有 `model.json` | ✅ | 从 `config.json` 的 `model.version` 读版本，`sampling_rate` 读输出采样率 |

**文件名不要求固定**：程序优先认 `sovits.pth`/`s2*.pth`/`*sovits*.pth` 和
`gpt.ckpt`/`s1*.ckpt`/`*gpt*.ckpt`，都没有时按大小/后缀猜，并在卡片 tooltip 显示实际选了哪个。

### 10.3 `engine_version` 与示例

```json
{
  "speaker": "SomeCharacter",
  "language": "ja",
  "engine_version": "v2Pro",
  "sovits_weights": "SoVITS_weights/some_e8_s200.pth",
  "gpt_weights": "GPT_weights/some-e10.ckpt",
  "ref_audio": "dataset/line_001.wav",
  "ref_text": "dataset/line_001.txt"
}
```

- 合法值：`v1` / `v2` / `v2Pro` / `v2ProPlus`（也认 `v2pro`、`v2-pro`、`v2proplus` 等写法）。
- 不写：先读权重前 2 字节，再读 `config.json` 的 `model.version`，最后回退 `v2Pro`。
- 写了但识别不出（如 `v9`）：按权重头纠正并写 `warnings`；权重头也识别不出才回退 `v2Pro`。
- 写了合法值但与权重头不一致：**加载器以权重内嵌版本为准**，`warnings` 与
  `logs\server.log` 会写「声明 X，实际 Y」；UI 卡片 tooltip 能看到 warning。

### 10.4 `config.json` 里读什么

社区包里的 `config.json` 结构各不相同（有的是训练配置，有的是推理配置），程序会尽量兼容，
自动找 `config.json` / `s2_config.json` / `configs/config.json`，或 `model.json` 的 `config_path`：

| 字段 | 用途 |
|---|---|
| `data.sampling_rate` | 写进 `/api/models` 的 `sampling_rate` / `output_sr`（界面显示 kHz） |
| `data.add_blank` | 写进 `add_blank` 字段；**推理不读这个训练开关**（权重已内嵌），为 `false` 时只给 warning |
| `symbols`（如有） | 只统计数量并与引擎内置词表比对（v1=322、v2/v2Pro/v2ProPlus=732），不一致给 warning，避免「加载成功但吐字全错」 |
| `model.version` / `version` | 权重头识别不出时的版本兜底 |

> ⚠️ 真正的网络结构（`gin_channels`、`upsample_rates`、`n_speakers`、`filter_length`……）
> 仍然从 **SoVITS 权重自带的 `config`** 读取，不是从 `config.json` 重建。`config.json` 只用来
> 展示/校验；如果两者冲突，以权重内嵌配置为准。

### 10.5 版本识别优先级

1. `model.json` 的 `engine_version`（合法值时）；
2. SoVITS 权重前 2 字节（`00`~`08`；老版 `PK` 按文件大小）；
3. `config.json` 的 `model.version`；
4. 回退 `v2Pro` 并在 `warnings` 里明确提示。

真正构建网络时，GPT-SoVITS 的 `TTS` 还会再读一次权重头；所以**权重头是最终权威**。
引擎会把「声明版本 / 实际版本 / s1 底模」写进 `logs\server.log`，例如：

```text
[engine] 加载 TongDeclaredV2: engine_version=v2；s2=sovits.pth；s1=gpt.ckpt
[engine] 警告：TongDeclaredV2 声明 engine_version=v2，但权重内嵌版本是 v2Pro，已按 v2Pro 加载
```

### 10.6 常见坑

1. **社区仓库通常只放权重，不带参考音**：卡片会标「缺参考音」，点卡片会提示补参考音。
   补一个 3–10 秒、无 BGM、与 `ref.txt` 逐字一致的干净人声；脚本临时借用的参考音只能证明「能跑」，
   音色还是参考音那个人的。
2. **不要拿 `D_*.pth` / `G_*.pth` / `logs/` 里的训练中间产物当 SoVITS 权重**：程序会跳过
   `g_`/`d_`/`disc` 前缀与 `logs/`、`sample/`、`eval/` 目录；真正的推理权重通常是
   `*_e<epoch>_s<step>.pth`（带 `weight` + `config` 两个键）。
3. **s1/s2 要同一次实验**：混搭会出现音色和韵律不匹配，甚至 `state_dict` 报错。
4. **路径相对模型目录**：`model.json` 里写 `dataset/xxx.wav` 会解析成
   `community_models\<模型>\dataset\xxx.wav`，不要写相对当前工作目录的路径。
5. **v3/v4 不要硬塞**：需要额外 vocoder 和底模（约 1 GB），当前环境未装；强行加载会得到明确错误，
   不会静默失败。
6. **`model.json` JSON 语法**：Windows 路径反斜杠要写 `\\`；写错会得到「解析失败，已回退自动探测」
   warning，显示名/语言可能不对。
7. **显存**：12 GB 同时只驻留一个模型；切换模型第一句要重新加载（约 2–15 秒，取决于模型）。
   不要并行开多个实例。

### 10.7 授权提示

> **社区模型各有各的许可证**。权重、训练数据、角色音色/立绘的版权归各自权利方，
> 很多仓库只允许个人研究、不允许商用或二次分发。下载前先看仓库的 `LICENSE` / `README`，
> 商用、直播、对外分发前请自行确认授权；本应用只负责加载，不授予任何模型的使用权。

### 10.8 一键安装 / UI

```powershell
# 自动挑最大的 s2 / s1 下载并写 model.json（含 engine_version）
<VITS_ENV>\venv-modern\Scripts\python.exe app\tools\install_community_model.py `
    --repo 4nm1tsu/shikokumetan_GPT-SoVITS

# 指定文件、语言、参考音
... --repo Richopera/Yangyang-gpt-sovits-v2proplus --name yangyang --language zh
```

装完点「重新扫描模型」。卡片上会看到：

- **来源** tag：`社区` / `本地`；
- **引擎版本** tag：`v1` / `v2` / `v2Pro` / `v2ProPlus`；
- 缺参考音时：红色「缺参考音」tag，点卡片弹出补参考音提示；
- 悬停卡片：`warnings`（版本不一致、config.json symbols 不匹配、model.json 解析失败等）。

### 10.9 本机实测记录

用 Tong 权重复制了一个「假社区包」（改名、无 `model.json`、权重放
`SoVITS_weights/` + `GPT_weights/`、参考音放 `dataset/line_001.wav`），并构造了
v1 / v2ProPlus / 缺参考音 / v3 / 损坏权重等用例，经真实 GPU 加载与合成：

| 用例 | 结果 |
|---|---|
| 假社区包（无 model.json，子目录） | ✅ 发现 → v2Pro → 32 kHz / 1.51 s / RMS 0.098 |
| 真实 v1（Theresa，PK 头） | ✅ 加载 → 32 kHz / 2.10 s / RMS 0.125 |
| 只有 sovits + ref（v1/v2Pro） | ✅ 官方 s1 底模回退 → 32 kHz 合成正常（1.43 s / 1.18 s） |
| 声明 v2、权重 v2Pro | ✅ 加载 → 实际 v2Pro（warning 已记录） |
| 构造 v2 形状权重（`SynthV2`） | ✅ 加载 → 实际 v2 / 32 kHz / 2.99 s / RMS 0.122（非真实音质） |
| v2ProPlus（Yangyang） | ✅ 加载 → 32 kHz / 2.52 s / RMS 0.087 |
| 缺参考音 | ✅ 发现但 `ready:false`，`missing` 明确列出 |
| 伪造 v3 权重头 | ✅ 明确报「v3 暂不可用」 |
| 损坏 v1 权重 | ✅ 明确报「加载模型 X 失败（UnpicklingError…）」 |

合计 **9/9 通过**（`test_matrix.json` 的 `passed: 9 / total: 9`）。

测试脚本与结果：`_work\community_test\test_matrix.py`、`_work\community_test\test_matrix.json`
（临时目录，不属于发布物；需要复跑时用同一个 Python 环境执行）。v2 构造权重由
`_work\community_test\make_synth_v2.py` 生成，仅用于结构验证。

---

## 11. 「添加模型」界面

左侧栏「＋ 添加模型」打开对话框，三个标签页：

**① 本地目录**
- 填权重目录（`浏览…` 调系统文件夹选择器，仅 EXE 窗口内可用）
- 点「扫描」→ 列出 `.pth/.ckpt`（自动区分 SoVITS/GPT、读出引擎版本）、`.wav`（带时长）、`.txt`
- 自动预选 s2 / s1 / 参考音 / 参考文本，可手动改；填显示名与语言
- 安装方式：**拷贝到模型库**（自包含，推荐）或**只登记路径**（零拷贝，适合大模型）
- 「安装模型」→ 目录写入 `community_models\<名字>\`，列表立即刷新

**② HuggingFace**
- 填 `owner/name` → 「下载并安装」→ 后台任务，进度条实时显示（挑最大的 SoVITS + GPT，网关镜像）
- 完成自动刷新列表

**③ 说明**：格式与注意事项。

顶栏新增「移除模型」：把当前模型目录移到 `_removed\`（可恢复），不会真删。

### 对应接口

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/models/scan` | `{path}` → 目录扫描结果（权重/参考音/文本/建议） |
| POST | `/api/models/install` | `{source_dir,s2,s1,ref_wav,ref_text,name,language,mode}` → 安装 |
| POST | `/api/models/import_hf` | `{repo,name,speaker,language}` → 返回 job id |
| GET | `/api/models/import_hf/{job}` | 进度：`{status,progress,message,model}` |
| POST | `/api/models/load` | `{id}` → 只加载模型做诊断，返回声明/实际引擎版本、s1、耗时 |
| POST | `/api/models/remove` | `{id}` → 移到 `_removed` |

> 实现：`app/backend/installer.py`；命令行等价物 `app/tools/install_community_model.py`。

---

## 12. 导出到鸿蒙端（端上模型包）

> 目标：**不再敲命令行**。桌面界面里点几下，就能把「经典 VITS 模型」打包成鸿蒙 App
> 可导入的 `.zip`，也能直接推送到手机。
>
> 实现：`app/backend/ondevice.py` 通过 import 复用
> `harmony-ondevice/tools/make_ondevice_model.py` 的**同一份代码**（导出 ONNX → 转
> MindSpore Lite `.ms` → 写 `model.json` → 打 zip），桌面端只加了「任务/进度/错误翻译/自检/推送」。
> 详细包格式见 `harmony-ondevice/docs/HANDOFF.md` §15。

### 12.1 哪些模型能导出（重要）

| 模型类型 | 能导出？ | 原因 |
|---|---|---|
| **经典 VITS v1**（`config.json` + `G_*.pth`，**22.05 kHz** / hop 256，如 `inference-xya`、`inference-HK416`、`inference-Tong`） | ✅ | 端上运行时就是这套结构 |
| 经典 VITS 但 44.1/48 kHz 或改过 `inter_channels`/`upsample_rates` | ❌（会明确报错） | 端上 PCM 与 mel2wav 档位按 22050/hop256/192 通道写死 |
| **GPT-SoVITS v1/v2/v2Pro/v2ProPlus**（`models_out\`、`VITS标准模型\`、`community_models\`） | ❌ | 需要 BERT + 自回归 GPT + s1 语义 token + 32 kHz 声码器；端上没有这些算子/内存 |
| 已导出的 ONNX 目录（`text2mel.onnx` + `mel2wav.onnx`，text2mel 必须是「dur 作为输入」那版） | ✅ | 跳过 torch 导出，直接转换 + 打包 |

> 界面上的**模型卡都是 GPT-SoVITS 模型**（桌面端推理用）。点卡片右上角 `⇪` 时，
> 面板会说明「这个模型端上跑不了」，并自动选中**同名的经典 VITS 模型**（例如
> `models_out\HK416` → `registry:HK416`）。两个 HK416 不是同一份权重：桌面卡是微调的
> GPT-SoVITS（32 kHz），端上包来自 `inference-HK416\G_latest.pth`（经典 VITS 22.05 kHz）。

### 12.2 界面位置

| 入口 | 位置 | 说明 |
|---|---|---|
| 「导出到手机」 | 顶栏右侧（模型详情区） | 打开面板，默认选第一个可导出模型 |
| 「⇪ 导出到手机」 | 左侧栏「添加模型」下面 | 同上 |
| 卡片右上角 `⇪` | 悬停模型卡时出现 | 带着这个模型进面板；GPT-SoVITS 卡会给替代提示 |

面板里能做四件事：

1. **选模型**：只列可导出的经典 VITS 来源（`registry` 里的模型名 / `inference-*` 目录 / ONNX 目录）。
2. **选语音档位**：`T128` / `T256` / `T512` / `T1024` 多选，默认全选。
   句子越长帧数越多，运行时按帧数挑**最小可用档**；只带小档会让长句直接报错（不截断）。
   包体积参考：四档 ≈ 197 MB，只带 `128,256` ≈ 110 MB。
3. **选输出目录**：留空用默认（`app\ondevice_out\`；EXE 里是 exe 同级的 `ondevice_out\`），
   可用「浏览…」调系统文件夹选择器。
4. **开始导出**：进度条 + 阶段文案（导出 ONNX → 转换 text2mel → 转换 mel2wav → 打包 → 自检），
   下方实时滚动工具日志尾部。完成后显示 **zip 路径 + 大小 + 耗时 + 自检结果**，
   并提供「打开所在文件夹」「直接传到手机」。

面板底部还会列出**已生成的包**（大小 / 时间 / 档位），每行都能「打开位置」「传手机」。

### 12.3 产物结构

```text
<输出目录>\<id>.zip
  <id>/text2mel.ms           # 静态图：x[1,512] / x_lengths[1] / length_scale[1] / dur[1,512]
  <id>/mel2wav.t128.ms       # 按所选档位产出（默认四档全出）
  <id>/mel2wav.t256.ms
  <id>/mel2wav.t512.ms
  <id>/mel2wav.t1024.ms
  <id>/model.json            # format=vits-ondevice/1：id/speaker/language/output_sr/hop/tiers/
                             #   symbols/prompts（端上没有 G2P，念的就是 prompts 里的示范句）
  <id>/dur_ja.json           # 逐 token 时长（兜底）
  <id>/dur_zh.json
  <id>/README.txt
```

导入手机：把 zip 推到设备 `Download` → App「模型」区「导入模型」→ 选 zip → 点卡片切换。

### 12.4 失败原因（都会明确显示，不静默）

| 提示 | 原因 / 处理 |
|---|---|
| `缺转换器：…\converter_lite.exe` | `<BUILD_DIR>\tools\mslite\converter-2.5.0` 下要有 `converter\converter_lite.exe` 与 `lib\`；或用 `VITS_CONVERTER` 指向它 |
| `找不到打包工具：…\make_ondevice_model.py` | `harmony-ondevice\tools\` 不在原位；或用 `VITS_ONDEVICE_TOOLS` 指定 |
| `该模型采样率 44100 Hz，端上只支持 22050 Hz` | 换 22.05 kHz 的经典 VITS（或按 HANDOFF §15.5 改造端上运行时） |
| `缺 config：…` / `缺生成器权重：…` | 目录里要有 `config.json`/`finetune_speaker.json` + `G_*.pth`（`D_*.pth` 是判别器，不能用） |
| `text2mel 转换失败` / `mel2wav tN 转换失败` | 见日志尾部 converter 输出；常见是中间目录含中文 |
| `中间目录含非 ASCII 字符` | converter_lite 2.5.0 不支持中文路径；用 `VITS_ONDEVICE_WORK` 指到纯英文目录（默认 `%LOCALAPPDATA%\VITSStudio\ondevice-work`） |
| `内存/显存不足` | 关掉正在合成的模型/其它大程序，或只导出 `128,256` 两档再试 |
| `产物自检未通过：缺必需文件…` | zip 缺 `text2mel.ms` / `mel2wav.tN.ms`，日志里有转换失败原因 |
| `没有连接手机` | 开 USB 调试（或无线调试），`hdc list targets` 能看到设备 |
| `未检测到 hdc` | 装 DevEco Studio（自带 hdc）或用 `HDC` 环境变量指向 `hdc.exe` |

### 12.5 命令行等价物（同一份逻辑）

```powershell
$py = '<VITS_ENV>\venv-modern\Scripts\python.exe'

# 界面里等价于：选 registry:xya + 四档
& $py <PROJECT_ROOT>\harmony-ondevice\tools\make_ondevice_model.py build --model xya --id xya

# 任意「经典 VITS 目录」
& $py ...\make_ondevice_model.py build --dir "<PROJECT_ROOT>\inference-HK416" --id hk416 --tiers 128,256

# 自检
& $py ...\make_ondevice_model.py inspect <PROJECT_ROOT>\app\ondevice_out\xya.zip
```

界面与命令行的差别只有：默认输出目录（界面 = `ondevice_out\`）、中间目录（界面 = ASCII 安全路径）、
以及界面会额外做一次 `inspect` 自检并把结果写进任务结果。

### 12.6 可配置项（环境变量 / `config.json`）

| 变量 | 默认 | 作用 |
|---|---|---|
| `VITS_ONDEVICE_TOOLS` | `<工程>\harmony-ondevice\tools` | 打包工具目录 |
| `VITS_CONVERTER` | `<BUILD_DIR>\tools\mslite\converter-2.5.0` | converter_lite 目录 |
| `VITS_ONDEVICE_OUT` | `app\ondevice_out`（EXE：exe 同级 `ondevice_out`） | zip 默认落盘目录 |
| `VITS_ONDEVICE_WORK` | `%LOCALAPPDATA%\VITSStudio\ondevice-work` | 中间目录（**必须 ASCII**） |
| `VITS_ONDEVICE_DIRS` | 空 | 额外的经典 VITS 目录根（`;` 分隔），会自动发现其下 `inference*` 子目录 |
| `VITS_ROOT` / `VITS_REGISTRY` | `<工程>\vits-upgrade` | registry.json 位置 |
| `HDC` | 自动找 DevEco 的 hdc | 推送用的设备工具 |

### 12.7 实测（2026-11-26，本机）

> 截图都是**真正打包后的 EXE 窗口**（pywebview / WebView2，用 CDP 驱动点击，不是拼图）：
> [`evidence/ondevice_01_panel.png`](evidence/ondevice_01_panel.png)（面板）、
> [`evidence/ondevice_02_progress.png`](evidence/ondevice_02_progress.png)（进度）、
> [`evidence/ondevice_03_done_hk416.png`](evidence/ondevice_03_done_hk416.png)（EXE 里完成态）、
> [`evidence/ondevice_04_done_xya.png`](evidence/ondevice_04_done_xya.png)（xya 四档完成态）、
> [`evidence/ondevice_00_entry.png`](evidence/ondevice_00_entry.png)（入口与模型卡 `⇪`）。

| 项 | 数据 |
|---|---|
| 界面全流程（EXE） | 卡片 `⇪` → 面板 → `registry:HK416` + `128,256` → 6%→34%→44%→68%→93%→100% |
| 界面全流程（开发后端，xya 四档） | 3%→26%→44%→68%→80%→93%→100%，`window.__errs` 为空（无 JS 报错） |
| 产物（xya 四档） | `<PROJECT_ROOT>\app\ondevice_out\xya.zip`，196,838,255 B（197 MB），9 个条目，**44.5 s**；经 EXE 打包为 196,838,257 B / **41.7 s** |
| 产物（EXE 内 HK416 两档） | `…\app\dist\VITS Studio\ondevice_out\hk416.zip`，110,296,474 B（110.3 MB），7 个条目，**21.6 s** |
| 自检（界面 + CLI） | 面板「自检 ✓」/ `必需文件缺失：无`；`id=xya / sr=22050 / tiers=[128,256,512,1024] / symbols=52 / prompts=[ja,zh]` |
| 与 CLI 产物对比 | 9 个条目名/大小全等；`text2mel.ms` 与四个 `mel2wav.t*.ms` 的 SHA256 **逐一相同**；`model.json` 20 个字段全同，只有 `built_at` 不同 |
| 推手机（P40 模拟器） | `hdc file send` → `Download/xya.zip`，3.5 s；设备侧 md5 `892a5c31…` = 本机 md5 |
| 错误路径 | GPT-SoVITS 目录 → 400「缺 config…」；未知模型 → 400 带建议；档位 999 → 400；未知 job → 404；缺转换器 / 伪权重 → `status=error` + 中文原因 + hint |

复现 UI 取证：`node app\tools\ondevice_ui_smoke.mjs <URL> <截图目录> [modelId] [tiers]`
（需要 Node 22+；对 EXE 窗口时设 `CDP_URL` + `CDP_REUSE=1`，说明见脚本头）。

> 验证时 EXE 目录里产生的 zip 已清理（保持分发目录干净，重新导出即可复现）；
> `app\ondevice_out\` 保留了 `xya.zip`（四档）与 `hk416.zip`（两档）两个示例产物。
