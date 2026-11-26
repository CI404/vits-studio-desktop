/*
 * VITS Studio —— 前端交互逻辑
 * 原生 JavaScript，无框架、无构建步骤，由 /static/app.js 直接加载。
 *
 * 约定：
 *  - 所有 DOM 查询都在 DOMContentLoaded 之后进行（见文件末尾 boot）；
 *  - 不使用 ES module（页面用普通 <script src> 引入）；
 *  - 所有异步分支都有 try/catch，底部还有 unhandledrejection 兜底；
 *  - 只依赖 index.html 中已有的 id 和 style.css 中已有的类名。
 */
(function () {
  'use strict';

  // ===========================================================================
  // 常量
  // ===========================================================================
  var MAX_CHARS = 500;                  // 文本上限，与后端校验保持一致
  var HISTORY_KEY = 'vits_history';     // 历史记录 localStorage 键名
  var HISTORY_LIMIT = 8;                // 历史最多 8 条
  var TOAST_MS = 2500;                  // toast 展示时长（毫秒）

  var LANGS = [
    { code: 'ja', label: 'ja', name: '日语' },
    { code: 'zh', label: 'zh', name: '中文' },
    { code: 'en', label: 'en', name: '英语' }
  ];
  var LANG_NAMES = { ja: '日语', zh: '中文', en: '英语' };

  // ===========================================================================
  // 运行时状态
  // ===========================================================================
  var state = {
    models: [],          // 当前模型列表（后端 to_dict 结构）
    activeModel: null,   // 当前选中的模型
    lang: 'ja',          // 当前语言 chip
    busy: false,         // 是否正在合成
    reloading: false,    // 是否正在重新扫描
    history: [],         // 历史记录（最多 8 条）
    audioURL: null,      // 当前音频的 objectURL，用于释放
    saveFilename: '',    // 本次合成建议的下载文件名（模型id_时间戳.wav）
    savedPath: '',       // 最近一次成功保存的绝对路径（「打开文件夹」用）
    saving: false        // 是否正在保存到磁盘
  };

  // DOM 引用统一在 boot() 里填充，避免脚本在 DOM 就绪前查询节点
  var els = {};
  var toastTimer = null;

  // ===========================================================================
  // 通用小工具
  // ===========================================================================

  /** 按 id 取节点 */
  function byId(id) {
    return document.getElementById(id);
  }

  /** 兼容旧浏览器的 closest 封装 */
  function closestElement(node, selector) {
    if (!node || typeof node.closest !== 'function') return null;
    return node.closest(selector);
  }

  /** 两位补零 */
  function pad2(n) {
    return String(n).padStart(2, '0');
  }

  /** 数字限制在 [min, max] 内 */
  function clampNumber(value, min, max) {
    var n = Number(value);
    if (!Number.isFinite(n)) return min;
    return Math.min(max, Math.max(min, n));
  }

  /** 保留最多两位小数并去掉多余的 0：1.80 -> "1.8"，32.00 -> "32" */
  function trimNum(value) {
    var n = Number(value);
    if (!Number.isFinite(n)) return '—';
    return String(Math.round(n * 100) / 100);
  }

  /** 按 Unicode 码点计字数（中文/emoji 更准确） */
  function charLength(text) {
    return Array.from(String(text == null ? '' : text)).length;
  }

  /** 文件大小标签 */
  function formatSize(mb) {
    var n = Number(mb);
    if (!Number.isFinite(n) || n <= 0) return '';
    if (n >= 100) return Math.round(n) + ' MB';
    return trimNum(n) + ' MB';
  }

  /** 字节数 -> "107,510 字节（105.0 KB）"（下载成功提示用） */
  function formatBytes(bytes) {
    var n = Number(bytes);
    if (!Number.isFinite(n) || n < 0) return '';
    var exact = Math.round(n).toLocaleString('en-US') + ' 字节';
    if (n < 1024) return exact;
    var kb = n / 1024;
    if (kb < 1024) return exact + '（' + kb.toFixed(1) + ' KB）';
    return exact + '（' + (kb / 1024).toFixed(1) + ' MB）';
  }

  /** 历史时间显示：当天只显示 HH:mm，跨天显示 MM-DD HH:mm */
  function formatTime(timestamp) {
    var d = new Date(Number(timestamp));
    if (Number.isNaN(d.getTime())) return '';
    var now = new Date();
    var hm = pad2(d.getHours()) + ':' + pad2(d.getMinutes());
    var sameDay = d.getFullYear() === now.getFullYear() &&
      d.getMonth() === now.getMonth() &&
      d.getDate() === now.getDate();
    return sameDay ? hm : (pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()) + ' ' + hm);
  }

  /** 下载文件名时间戳：20261125_231500 */
  function fileStamp(date) {
    var d = date || new Date();
    return String(d.getFullYear()) +
      pad2(d.getMonth() + 1) +
      pad2(d.getDate()) + '_' +
      pad2(d.getHours()) +
      pad2(d.getMinutes()) +
      pad2(d.getSeconds());
  }

  /** 清理文件名中不能出现在 Windows 文件名里的字符 */
  function safeFilename(name) {
    var s = String(name == null ? '' : name).replace(/[\\/:*?"<>|\s]+/g, '_');
    s = s.replace(/^_+|_+$/g, '');
    return s || 'model';
  }

  /** FNV-1a 字符串哈希（用于生成稳定的头像配色） */
  function hashString(str) {
    var s = String(str == null ? '' : str);
    var h = 2166136261;
    for (var i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return h >>> 0;
  }

  /** 由模型名生成两种颜色的 linear-gradient */
  function avatarGradient(name) {
    var base = hashString(name);
    var salt = hashString(String(name) + '#vits');
    var hue1 = base % 360;
    var hue2 = (hue1 + 35 + (salt % 80)) % 360;
    return 'linear-gradient(135deg, hsl(' + hue1 + ' 72% 56%), hsl(' + hue2 + ' 70% 40%))';
  }

  /** 头像首字母 */
  function avatarInitial(name) {
    var chars = Array.from(String(name == null ? '' : name).trim());
    return chars.length ? chars[0].toUpperCase() : 'M';
  }

  /** 语言代码归一化：只允许 ja / zh / en，其他一律回落到 ja */
  function normalizeLang(code) {
    var c = String(code == null ? '' : code).toLowerCase().slice(0, 2);
    return Object.prototype.hasOwnProperty.call(LANG_NAMES, c) ? c : 'ja';
  }

  /** 语言代码 -> 中文名 */
  function langName(code) {
    var c = normalizeLang(code);
    return LANG_NAMES[c] || c;
  }

  /** 是否为网络层错误（后端未启动、连接被拒绝等） */
  function isNetworkError(err) {
    if (!err) return false;
    if (typeof TypeError !== 'undefined' && err instanceof TypeError) return true;
    var msg = String(err && err.message ? err.message : err);
    return /Failed to fetch|NetworkError|Load failed|fetch failed|network/i.test(msg);
  }

  /** 把异常转成给用户看的短句 */
  function humanizeError(err) {
    if (!err) return '未知错误';
    if (isNetworkError(err)) return '无法连接后端，请确认服务已启动';
    var msg = err && err.message ? String(err.message) : String(err);
    msg = msg.trim();
    return msg || '未知错误';
  }

  // ===========================================================================
  // toast / 顶部状态
  // ===========================================================================

  /** 显示 toast；isError 为 true 时使用 .err 样式。2.5 秒后自动隐藏。 */
  function showToast(message, isError) {
    if (!els.toast) return;
    var text = message == null ? '' : String(message);
    if (text.length > 300) text = text.slice(0, 300) + '…';
    els.toast.textContent = text;
    els.toast.classList.remove('hidden');
    els.toast.classList.toggle('err', !!isError);

    if (toastTimer) {
      window.clearTimeout(toastTimer);
      toastTimer = null;
    }
    toastTimer = window.setTimeout(function () {
      toastTimer = null;
      if (els.toast) els.toast.classList.add('hidden');
    }, TOAST_MS);
  }

  /**
   * 设置右上角状态。
   * mode: 'ok' | 'busy' | 'error' | ''（空闲）
   */
  function setStatus(mode, text) {
    if (!els.statusDot || !els.statusText) return;

    els.statusDot.className = 'dot';
    els.statusDot.style.background = '';
    els.statusDot.style.boxShadow = '';
    els.statusText.style.color = '';
    els.statusText.style.fontWeight = '';

    if (mode === 'ok') {
      els.statusDot.classList.add('ok');
    } else if (mode === 'busy') {
      els.statusDot.classList.add('busy');
    } else if (mode === 'error') {
      // .dot 没有 error 类，用内联样式标红（不新增 CSS 文件）
      els.statusDot.style.background = 'var(--danger)';
      els.statusDot.style.boxShadow = '0 0 10px rgba(255,107,122,.7)';
      els.statusText.style.color = 'var(--danger)';
      els.statusText.style.fontWeight = '600';
    }
    els.statusText.textContent = text || '';
  }

  /** 更新字数、超限标红并同步生成按钮禁用状态 */
  function updateCharCount() {
    if (!els.text || !els.charCount || !els.btnGen) return;
    var len = charLength(els.text.value);
    var over = len > MAX_CHARS;

    els.charCount.textContent = String(len);
    els.charCount.style.color = over ? 'var(--danger)' : '';
    els.charCount.style.fontWeight = over ? '700' : '';

    els.btnGen.disabled = state.busy || over;
    if (over) {
      els.btnGen.title = '文本超过 ' + MAX_CHARS + ' 字上限，请删减后再生成';
    } else if (state.busy) {
      els.btnGen.title = '正在合成，请稍候';
    } else {
      els.btnGen.title = '';
    }
  }

  /** 切换合成忙碌状态（按钮文案 / 禁用） */
  function setBusy(busy) {
    state.busy = !!busy;
    if (els.btnGen) {
      els.btnGen.textContent = state.busy ? '生成中…' : '生成语音';
      updateCharCount();
    }
  }

  // ===========================================================================
  // 语言 chips
  // ===========================================================================

  /** 创建 ja / zh / en 三个 .chip（只创建一次，后续只切换 .active） */
  function renderLangChips() {
    if (!els.langChips) return;
    els.langChips.textContent = '';
    for (var i = 0; i < LANGS.length; i++) {
      var item = LANGS[i];
      var chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'chip';
      chip.dataset.lang = item.code;
      chip.textContent = item.label;
      chip.title = item.name;
      chip.setAttribute('aria-pressed', 'false');
      els.langChips.appendChild(chip);
    }
  }

  /** 选中某个语言 chip，并记录到 state.lang */
  function setLang(code) {
    state.lang = normalizeLang(code);
    if (!els.langChips) return;
    var chips = els.langChips.querySelectorAll('.chip');
    for (var i = 0; i < chips.length; i++) {
      var on = chips[i].dataset.lang === state.lang;
      chips[i].classList.toggle('active', on);
      chips[i].setAttribute('aria-pressed', on ? 'true' : 'false');
    }
  }

  // ===========================================================================
  // 模型列表
  // ===========================================================================

  /** 按 id 查找模型 */
  function findModel(id) {
    if (id == null) return null;
    for (var i = 0; i < state.models.length; i++) {
      if (String(state.models[i].id) === String(id)) return state.models[i];
    }
    return null;
  }

  /** 模型卡「来源」：后端 source_label（本地/社区） */
  function modelSourceLabel(model) {
    if (!model) return '';
    if (model.source_label) return String(model.source_label);
    return String(model.source_kind) === 'community' ? '社区' : '本地';
  }

  /** 引擎版本（v1 / v2 / v2Pro / v2ProPlus），旧字段 sovits_version 也认 */
  function engineVersionOf(model) {
    if (!model) return '';
    return String(model.engine_version || model.sovits_version || '').trim();
  }

  /** 未就绪原因（中文列表） */
  function missingText(model) {
    if (!model) return '';
    if (Array.isArray(model.missing) && model.missing.length) return model.missing.join('、');
    return '';
  }

  /** 缺参考音时的统一提示文案 */
  function missingHint(model) {
    var missing = missingText(model);
    if (missing) return '缺少：' + missing;
    if (model && model.ready === false) return '模型未就绪：请检查权重与参考音';
    return '';
  }

  /** 根据模型生成 #active-meta 文案：语言 / 来源 / 引擎版本 / 权重 / 微调 / 训练条数 / CER */
  function buildActiveMeta(model) {
    var lang = normalizeLang(model.language);
    var parts = [langName(lang) + '（' + lang + '）'];
    var src = modelSourceLabel(model);
    if (src) parts.push('来源 ' + src);
    var engineVersion = engineVersionOf(model);
    if (engineVersion) parts.push('引擎 ' + engineVersion);
    if (model.version && !/^community/i.test(String(model.version))) {
      var vv2 = String(model.version);
      parts.push(/^v?[0-9]/.test(vv2) ? (/^v/.test(vv2) ? vv2 : 'v' + vv2) : vv2);
    }
    parts.push(model.finetuned ? '微调' : (model.has_gpt ? '含 GPT' : '零样本'));

    var size = formatSize(model.size_mb);
    if (size) parts.push('权重 ' + size);

    var lines = Number(model.train_lines);
    if (Number.isFinite(lines) && lines > 0) parts.push('训练 ' + lines + ' 条');

    if (typeof model.cer === 'number' && isFinite(model.cer)) {
      parts.push('CER ' + Number(model.cer).toFixed(3));
    }
    if (typeof model.timbre_self === 'number' && isFinite(model.timbre_self)) {
      parts.push('音色贴合 ' + Number(model.timbre_self).toFixed(3));
    }
    if (model.output_sr) parts.push(Number(model.output_sr) / 1000 + ' kHz');
    if (model.ready === false) {
      var why = missingText(model);
      parts.push(why ? '缺 ' + why : '未就绪');
    }

    return parts.join(' · ');
  }

  /** 选中模型：更新卡片高亮、标题、active-meta、语言 chips */
  function selectModel(model, options) {
    if (!model) return;
    var opts = options || {};
    var same = !!(state.activeModel && String(state.activeModel.id) === String(model.id));
    var keepLang = opts.keepLang !== undefined ? !!opts.keepLang : same;

    state.activeModel = model;

    // 更新侧栏卡片 .active
    var cards = els.modelList.querySelectorAll('.model-card');
    for (var i = 0; i < cards.length; i++) {
      var on = String(cards[i].dataset.modelId) === String(model.id);
      cards[i].classList.toggle('active', on);
    }

    var displayName = model.name || model.id || '未命名模型';
    els.activeName.textContent = displayName;
    els.activeMeta.textContent = buildActiveMeta(model);
    var metaTips = [];
    if (model.note) metaTips.push(String(model.note));
    if (Array.isArray(model.warnings) && model.warnings.length) {
      metaTips.push('提示：' + model.warnings.join('；'));
    }
    if (model.ready === false) metaTips.push(missingHint(model));
    els.activeMeta.title = metaTips.filter(Boolean).join('\n');

    // 同一个模型保持用户当前选择的语言；切换模型则回到该模型的默认语言
    if (keepLang && state.lang) {
      setLang(state.lang);
    } else {
      setLang(normalizeLang(model.language));
    }

    updateCharCount();
  }

  /** 渲染侧栏模型卡片 */
  function renderModelList() {
    if (!els.modelList) return;
    els.modelList.textContent = '';

    if (!state.models.length) {
      var empty = document.createElement('div');
      empty.className = 'empty';
      empty.textContent = '未找到模型，请把模型文件夹放到模型目录后点「重新扫描模型」';
      els.modelList.appendChild(empty);
      return;
    }

    var frag = document.createDocumentFragment();

    for (var i = 0; i < state.models.length; i++) {
      var model = state.models[i];
      var ready = model.ready !== false;
      var displayName = String(model.name || model.id || '未命名模型');
      var modelId = String(model.id);

      var card = document.createElement('div');
      card.className = 'model-card';
      card.dataset.modelId = modelId;
      card.setAttribute('role', 'button');
      card.tabIndex = 0;

      if (!ready) {
        // 未就绪模型置灰；点击时由 handleModelClick 弹 toast 报错
        card.classList.add('disabled');
        card.setAttribute('aria-disabled', 'true');
        card.style.opacity = '0.5';
        card.style.filter = 'grayscale(0.65)';
        card.style.cursor = 'not-allowed';
        card.title = '模型未就绪 · ' + (missingHint(model) || '缺少权重或参考音');
      } else {
        card.title = displayName + (model.note ? ' · ' + model.note : '');
        if (Array.isArray(model.warnings) && model.warnings.length) {
          card.title += '\n提示：' + model.warnings.join('；');
        }
      }

      if (state.activeModel && String(state.activeModel.id) === modelId) {
        card.classList.add('active');
      }

      // 头像：首字母 + 名字哈希生成的双色渐变
      var avatar = document.createElement('div');
      avatar.className = 'avatar';
      avatar.textContent = avatarInitial(displayName);
      avatar.style.background = avatarGradient(displayName);
      avatar.setAttribute('aria-hidden', 'true');

      // 名称 + 标签容器（不新增 CSS 类，只用内联布局保证省略号生效）
      var info = document.createElement('div');
      info.style.minWidth = '0';
      info.style.flex = '1';

      var nameEl = document.createElement('div');
      nameEl.className = 'model-name';
      nameEl.textContent = displayName;
      nameEl.style.whiteSpace = 'nowrap';
      nameEl.style.overflow = 'hidden';
      nameEl.style.textOverflow = 'ellipsis';

      var tags = document.createElement('div');
      tags.className = 'model-tags';

      // 语言 tag
      var langCode = normalizeLang(model.language);
      var langTag = document.createElement('span');
      langTag.className = 'tag';
      langTag.textContent = langCode;
      langTag.title = langName(langCode);
      tags.appendChild(langTag);

      // 微调 / 零样本 tag
      var trainTag = document.createElement('span');
      trainTag.className = model.finetuned ? 'tag ok' : 'tag';
      trainTag.textContent = model.finetuned ? '微调' : (model.has_gpt ? '含 GPT' : '零样本');
      if (model.finetuned) trainTag.title = '微调权重：' + String(model.finetuned);
      else if (model.has_gpt) trainTag.title = '自带 GPT(s1) 权重，但 model.json 未声明 finetuned';
      tags.appendChild(trainTag);

      // 来源 tag：本地 / 社区
      var sourceTag = document.createElement('span');
      var sourceLabel = modelSourceLabel(model);
      sourceTag.className = 'tag' + (sourceLabel === '社区' ? ' ok' : '');
      sourceTag.textContent = '来源 ' + sourceLabel;
      sourceTag.title = sourceLabel === '社区'
        ? '来源：社区模型目录（' + String(model.source || 'community_models') + '）；授权请以模型作者声明为准'
        : '来源：本地训练产物 / 标准模型包';
      tags.appendChild(sourceTag);

      // 引擎版本 tag（v1 / v2 / v2Pro / v2ProPlus）
      var engineVersion = engineVersionOf(model);
      if (engineVersion) {
        var engineTag = document.createElement('span');
        engineTag.className = 'tag mono';
        engineTag.textContent = '引擎 ' + engineVersion;
        engineTag.title = 'GPT-SoVITS 引擎版本（model.json 的 engine_version / 权重头识别）';
        tags.appendChild(engineTag);
      }

      // 模型包版本 tag（训练 AI 产出的标准包会带 version；社区包不显示 community-1）
      if (model.version && !/^community/i.test(String(model.version))) {
        var verTag = document.createElement('span');
        verTag.className = 'tag';
        var vv = String(model.version);
        verTag.textContent = /^v?[0-9]/.test(vv) ? (/^v/.test(vv) ? vv : 'v' + vv) : String(vv);
        verTag.title = '模型包版本';
        tags.appendChild(verTag);
      }

      // 客观质量 tag（CER，越低越好）
      if (typeof model.cer === 'number' && isFinite(model.cer)) {
        var cerTag = document.createElement('span');
        cerTag.className = model.cer <= 0.15 ? 'tag ok' : 'tag';
        cerTag.textContent = 'CER ' + Number(model.cer).toFixed(2);
        cerTag.title = '固定评测集的字符错误率（Vosk 转写，越低越好）';
        tags.appendChild(cerTag);
      }

      if (!ready) {
        var badText = '未就绪';
        if (Array.isArray(model.missing) && model.missing.length) {
          if (model.missing.join('').indexOf('SoVITS') >= 0) badText = '缺权重';
          else if (model.missing.join('').indexOf('参考音') >= 0) badText = '缺参考音';
        }
        var badTag = document.createElement('span');
        badTag.className = 'tag';
        badTag.textContent = badText;
        badTag.title = missingHint(model);
        badTag.style.color = 'var(--danger)';
        badTag.style.borderColor = 'rgba(255,107,122,.35)';
        tags.appendChild(badTag);
      }

      info.appendChild(nameEl);
      info.appendChild(tags);
      card.appendChild(avatar);
      card.appendChild(info);

      // 卡片右上角「导出到手机」：悬停出现，点击不触发选中（事件委托里拦截）
      var exportBtn = document.createElement('button');
      exportBtn.type = 'button';
      exportBtn.className = 'card-export';
      exportBtn.dataset.exportModel = modelId;
      exportBtn.textContent = '⇪';
      exportBtn.title = '导出到手机（' + displayName + '）';
      exportBtn.setAttribute('aria-label', '导出 ' + displayName + ' 到手机');
      card.appendChild(exportBtn);

      frag.appendChild(card);
    }

    els.modelList.appendChild(frag);
  }

  /** 点击模型卡片 */
  function handleModelClick(model) {
    if (!model) return;
    if (model.ready === false) {
      var why = missingHint(model);
      var tip = '模型 ' + (model.name || model.id) + ' 未就绪：' + (why || '缺少权重或参考音');
      if (why.indexOf('参考音') >= 0) {
        tip += '。请把角色本人 3–10 秒干净语音放到 ref.wav、逐字文本放到 ref.txt，再点「重新扫描模型」';
      }
      showToast(tip, true);
      return;
    }
    selectModel(model);
  }

  /**
   * 应用一份模型列表。
   * preferredId 用于重扫描后尽量保留之前选中的模型。
   */
  function applyModels(models, preferredId) {
    var list = Array.isArray(models) ? models : [];
    var clean = [];
    for (var i = 0; i < list.length; i++) {
      var m = list[i];
      if (m && typeof m === 'object' && m.id != null) clean.push(m);
    }

    var previous = state.activeModel;
    var keepId = preferredId !== undefined ? preferredId : (previous ? previous.id : null);

    state.models = clean;
    state.activeModel = null;
    els.modelCount.textContent = String(clean.length);

    if (!clean.length) {
      renderModelList();
      els.activeName.textContent = '暂无可用模型';
      els.activeMeta.textContent = '把模型文件夹放进模型目录后，点「重新扫描模型」';
      setLang('ja');
      updateCharCount();
      return;
    }

    // 优先保留旧选择；否则选第一个就绪模型（没有就绪模型时至少选第一个）
    var target = null;
    if (keepId != null) {
      for (var j = 0; j < clean.length; j++) {
        if (String(clean[j].id) === String(keepId)) {
          target = clean[j];
          break;
        }
      }
    }
    if (!target) {
      for (var k = 0; k < clean.length; k++) {
        if (clean[k].ready !== false) {
          target = clean[k];
          break;
        }
      }
      if (!target) target = clean[0];
    }

    var keepLang = !!(previous && target &&
      String(previous.id) === String(target.id) && state.lang);
    renderModelList();
    selectModel(target, { keepLang: keepLang });
  }

  /** 初次加载失败时渲染离线提示（保证不白屏） */
  function renderOffline(network) {
    state.models = [];
    state.activeModel = null;
    els.modelCount.textContent = '0';
    els.modelList.textContent = '';

    var empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = network ? '后端未启动，请检查服务' : '模型列表加载失败，请检查后端日志';
    els.modelList.appendChild(empty);

    els.activeName.textContent = '无法连接后端';
    els.activeMeta.textContent = '请确认 VITS Studio 后端已启动（默认 7788 端口）';
    els.gpuInfo.textContent = '设备：不可用';
    els.gpuInfo.title = '';
    els.metaRender.textContent = '';
    updateCharCount();
  }

  // ===========================================================================
  // 网络请求辅助
  // ===========================================================================

  /** 读取非 2xx 响应里的错误：优先 FastAPI 的 {"detail": ...}，否则按文本 */
  async function readError(res) {
    var raw = '';
    try {
      raw = (await res.text()).trim();
    } catch (err) {
      raw = '';
    }
    if (!raw) return 'HTTP ' + res.status;

    try {
      var data = JSON.parse(raw);
      if (data && typeof data === 'object' && data.detail !== undefined) {
        return typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
      }
      if (data && typeof data === 'object' && data.message) {
        return String(data.message);
      }
    } catch (err) {
      // 不是 JSON，按纯文本显示
    }

    return raw.length > 300 ? raw.slice(0, 300) + '…' : raw;
  }

  /** 从响应头读数字，读不到返回 null */
  function headerNumber(res, name) {
    var raw = null;
    try {
      raw = res.headers.get(name);
    } catch (err) {
      raw = null;
    }
    if (raw == null || String(raw).trim() === '') return null;
    var n = Number(raw);
    return Number.isFinite(n) ? n : null;
  }

  /** 初次加载：GET /api/models */
  async function loadModels() {
    setStatus('busy', '连接后端');
    try {
      var res = await fetch('/api/models', { headers: { Accept: 'application/json' } });
      if (!res.ok) throw new Error(await readError(res));
      var data = await res.json();
      if (!data || !Array.isArray(data.models)) {
        throw new Error('后端返回的模型列表格式不正确');
      }

      applyModels(data.models);
      setStatus('ok', '就绪');
    } catch (err) {
      console.error('[VITS] 加载模型失败：', err);
      var network = isNetworkError(err);
      renderOffline(network);
      setStatus('error', network ? '后端未启动' : '加载失败');
      if (network) {
        showToast('后端未启动，请检查服务', true);
      } else {
        showToast('加载模型失败：' + humanizeError(err), true);
      }
      return;
    }

    // 设备信息属于增强信息，失败不影响主流程
    loadGpuInfo().catch(function (err) {
      console.warn('[VITS] 读取设备信息失败：', err);
    });
  }

  /** GET /api/health -> 侧栏设备信息 */
  async function loadGpuInfo() {
    try {
      var res = await fetch('/api/health', { headers: { Accept: 'application/json' } });
      if (!res.ok) throw new Error('health HTTP ' + res.status);
      var data = await res.json();
      var gpu = data && typeof data.gpu === 'string' && data.gpu.trim() ? data.gpu.trim() : '未知';
      els.gpuInfo.textContent = gpu.toLowerCase() === 'cpu' ? '设备：CPU 模式' : '设备：' + gpu;
      els.gpuInfo.title = data && data.model_dir ? '模型目录：' + data.model_dir : '';
    } catch (err) {
      console.warn('[VITS] 读取设备信息失败：', err);
      els.gpuInfo.textContent = '设备：不可用';
      els.gpuInfo.title = '';
    }
  }

  /** POST /api/models/reload 后重新渲染 */
  async function reloadModels() {
    if (state.reloading) return;
    if (state.busy) {
      showToast('正在合成，请稍后再重新扫描', true);
      return;
    }

    state.reloading = true;
    els.btnReload.disabled = true;
    setStatus('busy', '扫描模型');

    try {
      var res = await fetch('/api/models/reload', {
        method: 'POST',
        headers: { Accept: 'application/json' }
      });
      if (!res.ok) throw new Error(await readError(res));

      var data = await res.json();
      if (!data || !Array.isArray(data.models)) {
        throw new Error('后端返回的模型列表格式不正确');
      }

      var previousId = state.activeModel ? state.activeModel.id : null;
      applyModels(data.models, previousId);
      setStatus('ok', '就绪');
      showToast('扫描完成：共 ' + state.models.length + ' 个模型');
      loadGpuInfo().catch(function (err) {
        console.warn('[VITS] 读取设备信息失败：', err);
      });
    } catch (err) {
      console.error('[VITS] 重新扫描模型失败：', err);
      showToast('重新扫描失败：' + humanizeError(err), true);
      setStatus('error', '扫描失败');
    } finally {
      state.reloading = false;
      els.btnReload.disabled = false;
    }
  }

  // ===========================================================================
  // 生成
  // ===========================================================================

  /** 更新播放器下方的统计文案 */
  function setRenderMeta(renderSec, audioSec, sampleRate) {
    var parts = [];
    if (renderSec !== null) parts.push('渲染 ' + trimNum(renderSec) + 's');
    if (audioSec !== null) parts.push('音频 ' + trimNum(audioSec) + 's');
    if (sampleRate !== null && sampleRate > 0) {
      parts.push(trimNum(sampleRate / 1000) + ' kHz');
    }
    els.metaRender.textContent = parts.length ? parts.join(' · ') : '合成完成';
  }

  /** 核心流程：校验 -> POST /api/tts -> blob/objectURL -> 播放 + 下载 + 历史 */
  async function generate() {
    if (state.busy) return;

    var model = state.activeModel;
    if (!model) {
      showToast('请先选择一个模型', true);
      return;
    }
    if (model.ready === false) {
      showToast('模型 ' + (model.name || model.id) + ' 未就绪：' + (missingHint(model) || '缺少权重或参考音'), true);
      return;
    }

    var rawText = els.text.value || '';
    var text = rawText.trim();
    if (!text) {
      showToast('请输入要合成的文本', true);
      els.text.focus();
      return;
    }
    if (charLength(rawText) > MAX_CHARS) {
      showToast('文本过长（最多 ' + MAX_CHARS + ' 字），请分段生成', true);
      return;
    }

    var modelId = String(model.id);
    var modelName = model.name || model.id;
    var lang = normalizeLang(state.lang);
    var speed = Math.round(clampNumber(els.speed.value, 0.6, 1.6) * 100) / 100;
    var temperature = Math.round(clampNumber(els.temp.value, 0.5, 1.5) * 100) / 100;

    setBusy(true);
    setStatus('busy', '合成中');

    try {
      // 注意：这里刻意不设 AbortController/短超时。
      // 首次合成要加载模型，可能超过 15 秒，超时会误杀正常请求。
      var res = await fetch('/api/tts', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'audio/wav'
        },
        body: JSON.stringify({
          model: modelId,
          text: text,
          lang: lang,
          speed: speed,
          temperature: temperature
        })
      });

      if (!res.ok) throw new Error(await readError(res));

      var blob = await res.blob();
      if (!blob || blob.size === 0) throw new Error('后端返回了空音频');

      // 先从响应头取统计信息，再创建 objectURL
      var audioSec = headerNumber(res, 'X-Audio-Seconds');
      var renderSec = headerNumber(res, 'X-Render-Seconds');
      var sampleRate = headerNumber(res, 'X-Sample-Rate');

      var url = URL.createObjectURL(blob);
      var oldURL = state.audioURL;
      state.audioURL = url;

      // 播放器
      els.audio.src = url;
      try {
        els.audio.load();
      } catch (err) {
        console.warn('[VITS] audio.load 失败：', err);
      }
      if (typeof els.audio.play === 'function') {
        try {
          var playPromise = els.audio.play();
          // 浏览器可能因自动播放策略拒绝；只提示失败，不影响合成结果
          if (playPromise && typeof playPromise.catch === 'function') {
            playPromise.catch(function (err) {
              console.warn('[VITS] 自动播放被拒绝：', err);
            });
          }
        } catch (err) {
          console.warn('[VITS] 自动播放失败：', err);
        }
      }

      // 下载按钮：桌面版是 pywebview/WebView2，不接管 blob: 锚点下载（点了完全静默），
      // 所以这里只准备文件名，真正落盘由「下载 WAV」按钮调 POST /api/audio/save 完成。
      state.saveFilename = safeFilename(modelId) + '_' + fileStamp(new Date()) + '.wav';
      resetSaveUI();
      els.download.disabled = false;
      els.download.title = '保存到「下载\\VITS Studio」：' + state.saveFilename;

      // 统计文案：渲染 1.8s · 音频 3.2s · 32 kHz
      setRenderMeta(renderSec, audioSec, sampleRate);

      // 历史：成功合成后写入 localStorage（最多 8 条，文本只存前 60 字）
      pushHistory({
        model: modelId,
        text: Array.from(text).slice(0, 60).join(''),
        lang: lang,
        speed: speed,
        time: Date.now(),
        audioSeconds: audioSec
      });

      setStatus('ok', '就绪');
      showToast('合成完成：' + modelName);

      // 新音频已经接管播放器，延迟释放旧的 objectURL
      if (oldURL && oldURL !== url) {
        window.setTimeout(function () {
          try {
            URL.revokeObjectURL(oldURL);
          } catch (err) {
            console.warn('[VITS] 释放旧 objectURL 失败：', err);
          }
        }, 1000);
      }
    } catch (err) {
      console.error('[VITS] 合成失败：', err);
      var network = isNetworkError(err);
      var detail = humanizeError(err);
      showToast('合成失败：' + detail, true);
      setStatus('error', network ? '后端未启动' : '生成失败');
    } finally {
      setBusy(false);
    }
  }

  // ===========================================================================
  // 下载 WAV（由后端落盘）
  // ===========================================================================

  /** 从已解析/未解析的 JSON 错误体里取可读的 detail（FastAPI 校验错误是数组） */
  function detailOf(data, status) {
    if (data && data.detail !== undefined) {
      return typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
    }
    return 'HTTP ' + status;
  }

  /** 生成新音频后清空上一次的「已保存」提示 */
  function resetSaveUI() {
    state.savedPath = '';
    if (els.savePath) {
      els.savePath.textContent = '';
      els.savePath.classList.add('hidden');
    }
    if (els.reveal) {
      els.reveal.classList.add('hidden');
      els.reveal.dataset.path = '';
    }
  }

  /** 下载 WAV：调后端写盘（WebView2 不会弹保存框，不能依赖 blob: 下载） */
  async function saveWav() {
    if (state.saving) return;

    var model = state.activeModel;
    if (!state.saveFilename || !model) {
      showToast('请先生成语音，再下载 WAV', true);
      return;
    }

    state.saving = true;
    var label = els.download.textContent || '下载 WAV';
    els.download.disabled = true;
    els.download.textContent = '保存中…';
    try {
      var res = await fetch('/api/audio/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filename: state.saveFilename,
          model: String(model.id)
        })
      });

      var data = null;
      try {
        data = await res.json();
      } catch (err) {
        data = null;
      }
      if (!res.ok) throw new Error(detailOf(data, res.status));
      if (!data || !data.path) throw new Error('后端没有返回保存路径');

      state.savedPath = String(data.path);
      var sizeText = formatBytes(data.bytes);
      els.savePath.textContent = (data.duplicate ? '已存在：' : '已保存：') +
        state.savedPath + (sizeText ? '（' + sizeText + '）' : '');
      els.savePath.classList.remove('hidden');
      els.reveal.classList.remove('hidden');
      els.reveal.dataset.path = state.savedPath;
      showToast((data.duplicate ? '文件已存在：' : '已保存：') + state.savedPath);
    } catch (err) {
      console.error('[VITS] 保存 WAV 失败：', err);
      showToast('保存失败：' + humanizeError(err), true);
    } finally {
      state.saving = false;
      els.download.disabled = false;
      els.download.textContent = label;
    }
  }

  /** 在资源管理器里定位刚保存的 WAV */
  async function revealSaved() {
    var path = state.savedPath || (els.reveal && els.reveal.dataset.path) || '';
    if (!path) {
      showToast('还没有保存过文件', true);
      return;
    }
    try {
      var res = await fetch('/api/audio/reveal', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: path })
      });
      var data = null;
      try {
        data = await res.json();
      } catch (err) {
        data = null;
      }
      if (!res.ok) throw new Error(detailOf(data, res.status));
    } catch (err) {
      console.error('[VITS] 打开文件夹失败：', err);
      showToast('打开文件夹失败：' + humanizeError(err), true);
    }
  }

  // ===========================================================================
  // 历史记录
  // ===========================================================================

  /** 从 localStorage 读取历史（容错：损坏数据直接忽略） */
  function loadHistory() {
    try {
      var raw = window.localStorage.getItem(HISTORY_KEY);
      if (!raw) return [];
      var parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return [];

      var list = [];
      for (var i = 0; i < parsed.length && list.length < HISTORY_LIMIT; i++) {
        var item = parsed[i];
        if (!item || typeof item !== 'object') continue;
        if (typeof item.text !== 'string') continue;
        list.push(item);
      }
      return list;
    } catch (err) {
      console.warn('[VITS] 读取历史记录失败：', err);
      return [];
    }
  }

  /** 保存历史到 localStorage（失败只记录日志，不影响合成结果） */
  function saveHistory(list) {
    try {
      window.localStorage.setItem(HISTORY_KEY, JSON.stringify(list));
      return true;
    } catch (err) {
      console.warn('[VITS] 写入历史记录失败：', err);
      return false;
    }
  }

  /** 新增一条历史并重新渲染 */
  function pushHistory(entry) {
    state.history.unshift(entry);
    if (state.history.length > HISTORY_LIMIT) {
      state.history = state.history.slice(0, HISTORY_LIMIT);
    }
    saveHistory(state.history);
    renderHistory();
  }

  /** 渲染历史列表：左侧 .txt，右侧 .meta；空时显示 .empty */
  function renderHistory() {
    if (!els.historyList) return;
    els.historyList.textContent = '';

    if (!state.history.length) {
      var empty = document.createElement('div');
      empty.className = 'empty small';
      empty.textContent = '暂无';
      els.historyList.appendChild(empty);
      return;
    }

    var frag = document.createDocumentFragment();
    for (var i = 0; i < state.history.length; i++) {
      var entry = state.history[i];

      var item = document.createElement('div');
      item.className = 'history-item';
      item.dataset.index = String(i);
      item.tabIndex = 0;
      item.setAttribute('role', 'button');
      item.title = (entry.model || '未知模型') +
        ' · ' + langName(entry.lang) +
        ' · 语速 ' + (Number.isFinite(Number(entry.speed)) ? Number(entry.speed).toFixed(2) : '1.00') +
        (formatTime(entry.time) ? ' · ' + formatTime(entry.time) : '');

      var txt = document.createElement('div');
      txt.className = 'txt';
      txt.textContent = entry.text || '（空文本）';

      var meta = document.createElement('div');
      meta.className = 'meta';
      var bits = [];
      if (entry.model) bits.push(String(entry.model));
      var audioSec = Number(entry.audioSeconds);
      if (Number.isFinite(audioSec)) bits.push(trimNum(audioSec) + 's');
      var timeText = formatTime(entry.time);
      if (timeText) bits.push(timeText);
      meta.textContent = bits.length ? bits.join(' · ') : '—';

      item.appendChild(txt);
      item.appendChild(meta);
      frag.appendChild(item);
    }
    els.historyList.appendChild(frag);
  }

  /** 点击历史项：回填输入框/语言/语速，不重放音频 */
  function fillFromHistory(index) {
    if (!Number.isInteger(index) || index < 0 || index >= state.history.length) return;
    var entry = state.history[index];
    if (!entry) return;

    els.text.value = typeof entry.text === 'string' ? entry.text : '';
    updateCharCount();

    var speed = Number(entry.speed);
    if (Number.isFinite(speed)) {
      els.speed.value = String(clampNumber(speed, 0.6, 1.6));
      updateSliderLabels();
    }

    // 如果该模型还在列表里，顺便切过去；不在也不影响回填文本
    if (entry.model) {
      var model = findModel(entry.model);
      if (!model) {
        for (var i = 0; i < state.models.length; i++) {
          if (state.models[i].name === entry.model) {
            model = state.models[i];
            break;
          }
        }
      }
      if (model) {
        selectModel(model);
      } else {
        showToast('历史中的模型 ' + entry.model + ' 已不在列表中', true);
      }
    }
    if (entry.lang) setLang(entry.lang);

    // 不调用 audio.play()，历史点击只回填参数
    try {
      els.text.focus();
    } catch (err) {
      // 忽略聚焦失败
    }
  }

  // ===========================================================================
  // 弹窗 / 侧栏按钮
  // ===========================================================================

  function openModal() {
    if (!els.modal) return;
    els.modal.classList.remove('hidden');
    els.modal.setAttribute('aria-hidden', 'false');
    if (els.modalClose) {
      try {
        els.modalClose.focus();
      } catch (err) {
        // 忽略聚焦失败
      }
    }
  }

  function closeModal() {
    if (!els.modal) return;
    els.modal.classList.add('hidden');
    els.modal.setAttribute('aria-hidden', 'true');
  }

  /** 退出应用：先 confirm，再 POST /api/shutdown（404/网络错误都忽略） */
  async function quitApp() {
    var agreed = false;
    try {
      agreed = window.confirm('确定要退出 VITS Studio 吗？');
    } catch (err) {
      agreed = false;
    }
    if (!agreed) return;

    els.btnQuit.disabled = true;
    try {
      try {
        await fetch('/api/shutdown', { method: 'POST' });
      } catch (err) {
        // 后端可能没有实现 /api/shutdown（返回 404），也可能已经直接断开
        console.warn('[VITS] /api/shutdown 不可用，忽略：', err);
      }

      // 先显示提示再关窗：非脚本打开的窗口通常不允许 close，提示仍然可见
      showToast('已退出');
      try {
        window.close();
      } catch (err) {
        console.warn('[VITS] window.close 被拒绝：', err);
      }
    } finally {
      els.btnQuit.disabled = false;
    }
  }

  // ===========================================================================
  // 滑块 / 文本事件
  // ===========================================================================

  function updateSliderLabels() {
    if (!els.speed || !els.speedVal || !els.temp || !els.tempVal) return;
    els.speedVal.textContent = Number(els.speed.value).toFixed(2);
    els.tempVal.textContent = Number(els.temp.value).toFixed(2);
  }

  // ===========================================================================
  // 事件绑定
  // ===========================================================================


  // ================= 添加模型对话框 =================
  function setupAddModel() {
    var modal = els.modal;
    if (!modal) return;

    // 标签页切换
    var tabs = modal.querySelectorAll('.tab');
    var panels = modal.querySelectorAll('.tab-panel');
    for (var i = 0; i < tabs.length; i++) {
      tabs[i].addEventListener('click', function () {
        var name = this.getAttribute('data-tab');
        for (var j = 0; j < tabs.length; j++) tabs[j].classList.toggle('active', tabs[j] === this);
        for (var k = 0; k < panels.length; k++) {
          panels[k].classList.toggle('hidden', panels[k].getAttribute('data-panel') !== name);
        }
      });
    }

    function $(id) { return document.getElementById(id); }
    var pathInput = $('add-path'), form = $('add-form'), hint = $('add-scan-hint');
    var selS2 = $('add-s2'), selS1 = $('add-s1'), selWav = $('add-ref-wav'), refText = $('add-ref-text');
    var nameInput = $('add-name'), langSel = $('add-lang'), badge = $('add-version-badge');
    var scanData = null;

    // 原生文件夹选择器（仅 pywebview 窗口里有）
    $('add-browse').addEventListener('click', function () {
      var api = window.pywebview && window.pywebview.api;
      if (api && api.pick_dir) {
        api.pick_dir().then(function (dir) {
          if (dir) pathInput.value = dir;
        }).catch(function (e) { showToast('打开选择器失败: ' + e, true); });
      } else {
        showToast('当前窗口不支持文件夹选择器，请直接粘贴路径', true);
      }
    });

    function fillSelect(sel, items, chosen, labelOf) {
      sel.innerHTML = '';
      for (var i = 0; i < items.length; i++) {
        var it = items[i];
        var opt = document.createElement('option');
        opt.value = it.path;
        opt.textContent = labelOf(it);
        if (chosen && it.path === chosen) opt.selected = true;
        sel.appendChild(opt);
      }
    }

    function updateBadge() {
      var v = '';
      if (scanData) {
        for (var i = 0; i < scanData.weights.length; i++) {
          if (scanData.weights[i].path === selS2.value) { v = scanData.weights[i].version || '未知'; break; }
        }
      }
      badge.textContent = v || '?';
    }

    $('add-scan').addEventListener('click', function () {
      var p = (pathInput.value || '').trim();
      if (!p) { showToast('先填写模型目录', true); return; }
      hint.textContent = '扫描中…';
      fetch('/api/models/scan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: p })
      }).then(function (res) {
        if (!res.ok) return readError(res).then(function (msg) { throw new Error(msg); });
        return res.json();
      }).then(function (data) {
        scanData = data;
        fillSelect(selS2, data.weights, data.suggest.s2, function (w) {
          return w.name + '  ·  ' + w.mb + ' MB' + (w.version ? '  ·  ' + w.version : '');
        });
        var others = data.weights.filter(function (w) { return w.path !== selS2.value; });
        fillSelect(selS1, others, data.suggest.s1, function (w) { return w.name + '  ·  ' + w.mb + ' MB'; });
        fillSelect(selWav, data.wavs, data.suggest.ref_wav, function (w) {
          return w.name + '  ·  ' + w.sec + ' 秒';
        });
        if (data.suggest.ref_txt) {
          for (var i = 0; i < data.texts.length; i++) {
            if (data.texts[i].path === data.suggest.ref_txt) refText.value = data.texts[i].preview;
          }
        }
        if (!nameInput.value) {
          var parts = p.split(/[\\/]/).filter(Boolean);
          nameInput.value = parts.length ? parts[parts.length - 1] : '';
        }
        updateBadge();
        form.classList.remove('hidden');
        hint.textContent = '扫描到 ' + data.weights.length + ' 个权重、' + data.wavs.length + ' 个参考音、' + data.texts.length + ' 个文本';
      }).catch(function (e) {
        hint.textContent = '扫描失败';
        showToast('扫描失败: ' + (e.message || e), true);
      });
    });

    selS2.addEventListener('change', updateBadge);
    selWav.addEventListener('change', function () {
      if (!scanData) return;
      var base = selWav.value.replace(/\.wav$/i, '');
      for (var i = 0; i < scanData.texts.length; i++) {
        if (scanData.texts[i].path.replace(/\.[^.]+$/, '') === base) {
          refText.value = scanData.texts[i].preview;
          return;
        }
      }
    });

    $('add-install').addEventListener('click', function () {
      var checked = modal.querySelector('input[name="add-mode"]:checked');
      var mode = checked ? checked.value : 'copy';
      var body = {
        source_dir: pathInput.value, s2: selS2.value, s1: selS1.value,
        ref_wav: selWav.value, ref_text: refText.value,
        name: nameInput.value, speaker: nameInput.value,
        language: langSel.value, mode: mode
      };
      var btn = $('add-install');
      btn.disabled = true; btn.textContent = '安装中…';
      fetch('/api/models/install', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      }).then(function (res) {
        if (!res.ok) return readError(res).then(function (msg) { throw new Error(msg); });
        return res.json();
      }).then(function (out) {
        showToast('已添加 ' + out.id + '（引擎 ' + out.sovits_version + '）');
        closeModal();
        return loadModels();
      }).catch(function (e) {
        showToast('安装失败: ' + (e.message || e), true);
      }).then(function () {
        btn.disabled = false; btn.textContent = '安装模型';
      });
    });

    // ---------- HuggingFace 导入 ----------
    var hfRepo = $('hf-repo'), hfName = $('hf-name'), hfLang = $('hf-lang');
    var hfBox = $('hf-progress'), hfBar = $('hf-bar'), hfMsg = $('hf-msg');

    $('hf-start').addEventListener('click', function () {
      var repo = (hfRepo.value || '').trim();
      if (!repo || repo.indexOf('/') < 0) { showToast('仓库 ID 形如 owner/name', true); return; }
      var btn = $('hf-start');
      btn.disabled = true; btn.textContent = '下载中…';
      hfBox.classList.remove('hidden');
      hfBar.style.width = '0%';
      hfMsg.textContent = '准备中…';
      var display = hfName.value || repo.split('/').pop();

      fetch('/api/models/import_hf', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo: repo, name: hfName.value, speaker: display, language: hfLang.value })
      }).then(function (res) {
        if (!res.ok) return readError(res).then(function (msg) { throw new Error(msg); });
        return res.json();
      }).then(function (data) {
        var job = data.job;
        return new Promise(function (resolve, reject) {
          var timer = setInterval(function () {
            fetch('/api/models/import_hf/' + job).then(function (r) { return r.json(); }).then(function (st) {
              hfBar.style.width = Math.round((st.progress || 0) * 100) + '%';
              hfMsg.textContent = st.message || st.status;
              if (st.status === 'done') { clearInterval(timer); resolve(st); }
              else if (st.status === 'error') { clearInterval(timer); reject(new Error(st.error || '下载失败')); }
            }).catch(function (e) { clearInterval(timer); reject(e); });
          }, 800);
        });
      }).then(function () {
        showToast('已安装 ' + display);
        closeModal();
        return loadModels();
      }).catch(function (e) {
        hfMsg.textContent = '失败：' + (e.message || e);
        showToast('导入失败: ' + (e.message || e), true);
      }).then(function () {
        btn.disabled = false; btn.textContent = '下载并安装';
      });
    });

    // ---------- 移除当前模型 ----------
    var btnRemove = $('btn-remove');
    if (btnRemove) {
      btnRemove.addEventListener('click', function () {
        var m = state.activeModel;
        if (!m) { showToast('先选择一个模型', true); return; }
        if (!confirm('把「' + (m.name || m.id) + '」移出模型库？\n（目录会移到 _removed\\，可手动恢复）')) return;
        fetch('/api/models/remove', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: m.id })
        }).then(function (res) {
          if (!res.ok) return readError(res).then(function (msg) { throw new Error(msg); });
          return res.json();
        }).then(function () {
          showToast('已移除 ' + m.id);
          return loadModels();
        }).catch(function (e) {
          showToast('移除失败: ' + (e.message || e), true);
        });
      });
    }
  }

  // ===========================================================================
  // 导出到手机（鸿蒙端模型包）
  // ===========================================================================

  var TIER_CHOICES = [128, 256, 512, 1024];
  var EXPORT_POLL_MS = 800;

  var exState = {
    sources: null,        // GET /api/ondevice/sources 的结果
    status: null,         // GET /api/ondevice/status 的结果
    jobId: null,          // 当前/最近一次任务 id
    timer: null,          // 轮询定时器
    result: null,         // 最近一次成功产物
    busy: false,          // 正在导出
    logs: 0               // 已渲染日志行数（用于只追加）
  };

  /** 统一的 JSON 请求：非 2xx 时抛出后端 detail 文本 */
  async function apiJson(url, options) {
    var res = await fetch(url, options || {});
    if (!res.ok) throw new Error(await readError(res));
    return res.json();
  }

  function jsonPost(url, body) {
    return apiJson(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    });
  }

  function fmtMb(n) {
    var v = Number(n);
    if (!Number.isFinite(v) || v <= 0) return '—';
    return (v >= 100 ? Math.round(v) : Math.round(v * 10) / 10) + ' MB';
  }

  function tierName(t) {
    return 'T' + t;
  }

  /** 画档位多选（只画一次） */
  function renderExportTiers() {
    if (!els.exportTiers) return;
    els.exportTiers.textContent = '';
    for (var i = 0; i < TIER_CHOICES.length; i++) {
      var tier = TIER_CHOICES[i];
      var label = document.createElement('label');
      label.className = 'tier on';
      label.dataset.tier = String(tier);

      var input = document.createElement('input');
      input.type = 'checkbox';
      input.checked = true;
      input.value = String(tier);
      input.dataset.tier = String(tier);

      var name = document.createElement('span');
      name.textContent = tierName(tier);

      var hint = document.createElement('span');
      hint.className = 'tier-mb';
      hint.textContent = tier <= 256 ? '短句' : (tier <= 512 ? '中句' : '长句');

      label.appendChild(input);
      label.appendChild(name);
      label.appendChild(hint);
      els.exportTiers.appendChild(label);
    }
    els.exportTiers.addEventListener('change', function (ev) {
      var input = ev.target;
      if (!input || input.type !== 'checkbox') return;
      var wrap = closestElement(input, '.tier');
      if (wrap) wrap.classList.toggle('on', !!input.checked);
    });
  }

  function selectedExportTiers() {
    if (!els.exportTiers) return [];
    var boxes = els.exportTiers.querySelectorAll('input[type=checkbox]');
    var out = [];
    for (var i = 0; i < boxes.length; i++) {
      if (boxes[i].checked) out.push(Number(boxes[i].value));
    }
    out.sort(function (a, b) { return a - b; });
    return out;
  }

  /** 读 /api/ondevice/status：工具链 / 转换器 / 目录 / hdc */
  async function loadExportStatus() {
    var st = await apiJson('/api/ondevice/status');
    exState.status = st;
    var bits = [];
    bits.push(st.tool.cli_ok ? '打包工具 ✓' : '打包工具 ✗');
    bits.push(st.tool.converter_ok ? '转换器 ✓' : '转换器 ✗');
    if (!st.work_ascii) bits.push('中间目录含非 ASCII ✗');
    if (st.output_ok === false) bits.push('产物目录不可写 ✗');
    var hdc = st.hdc || {};
    if (hdc.available) {
      bits.push('手机 ' + (hdc.devices && hdc.devices.length
        ? '✓ ' + hdc.devices.join('、') : '未连接'));
    } else {
      bits.push('手机 未检测到 hdc');
    }
    els.exportStatus.textContent = bits.join('  ·  ');
    els.exportStatus.title = '打包工具：' + st.tool.cli
      + '\n转换器：' + st.tool.converter
      + '\n中间目录：' + st.work_dir
      + '\n产物目录：' + st.output_dir
      + (hdc.message ? '\n手机：' + hdc.message : '');
    if (!els.exportOut.value) {
      els.exportOut.placeholder = '留空 = ' + st.output_dir;
    }
    a11yPush(els.exportPush);
    return st;
  }

  /** 「直接传到手机」按钮状态：只有检测到 hdc + 设备才可用 */
  function a11yPush(body, st) {
    if (!body) return;
    var st2 = st || exState.status;
    var hdc = (st2 && st2.hdc) || {};
    var canPush = !!(hdc.available && hdc.devices && hdc.devices.length);
    body.disabled = !canPush;
    body.title = canPush
      ? 'hdc file send 到设备 Download 目录（' + hdc.devices.join('、') + '）'
      : '未检测到已连接的鸿蒙设备（USB 调试 / 无线调试）';
  }

  /** 读可导出的模型来源 */
  async function loadExportSources(preferredId) {
    var data = await apiJson('/api/ondevice/sources');
    exState.sources = data;
    var sel = els.exportModel;
    sel.innerHTML = '';
    var list = data.exportable || [];
    for (var i = 0; i < list.length; i++) {
      var s = list[i];
      var opt = document.createElement('option');
      opt.value = s.id;
      opt.textContent = s.name + ' · ' + s.kind_label + ' · ' +
        (s.sample_rate ? (s.sample_rate / 1000) + ' kHz' : '?') +
        (s.size_mb ? ' · ' + fmtMb(s.size_mb) : '');
      opt.title = 'config: ' + (s.config || '—') +
        '\n权重: ' + (s.checkpoint || '—') +
        '\n符号表: ' + (s.symbols_count || '?') +
        (s.reason ? '\n不可用原因: ' + s.reason : '');
      sel.appendChild(opt);
    }

    // 选中：指定模型（同名/同 id）> 第一个
    var chosen = null;
    if (preferredId) {
      for (var j = 0; j < list.length; j++) {
        if (String(list[j].id) === String(preferredId) ||
            String(list[j].name).toLowerCase() === String(preferredId).toLowerCase()) {
          chosen = list[j];
          break;
        }
      }
      if (!chosen) {
        // 桌面端模型（GPT-SoVITS）→ 找同名经典 VITS 来源提示
        var unsupported = findUnsupported(preferredId);
        if (unsupported && unsupported.similar && unsupported.similar.length) {
          for (var k = 0; k < list.length; k++) {
            if (String(list[k].id) === String(unsupported.similar[0])) { chosen = list[k]; break; }
          }
        }
      }
    }
    if (chosen) sel.value = chosen.id;
    else if (list.length) sel.value = list[0].id;

    // 状态提示
    var notes = [];
    if (!list.length) {
      notes.push('没有找到可导出的经典 VITS 模型（需要 config + G_*.pth，22.05 kHz）。' +
        '可把目录放到工程根目录并命名为 inference-* ，或设环境变量 VITS_ONDEVICE_DIRS。');
    }
    if (data.skipped && data.skipped.length) {
      notes.push('跳过 ' + data.skipped.length + ' 个不可导出模型：' +
        data.skipped.map(function (s) { return s.name + '（' + s.reason + '）'; }).join('；'));
    }
    var prefUnsupported = preferredId ? findUnsupported(preferredId) : null;
    if (prefUnsupported) {
      notes.unshift('「' + prefUnsupported.name + '」是桌面端 GPT-SoVITS 模型，端上不能直接跑。' +
        (prefUnsupported.similar && prefUnsupported.similar.length
          ? '已自动选中同名的经典 VITS 模型（' + prefUnsupported.similar[0] + '）作为替代。'
          : '请在下面选择经典 VITS 模型。'));
    }
    setExportBanner(notes);
    els.exportStart.disabled = !list.length || !(exState.status && exState.status.ok);
    return data;
  }

  function findUnsupported(modelId) {
    var data = exState.sources;
    if (!data || !data.unsupported) return null;
    for (var i = 0; i < data.unsupported.length; i++) {
      if (String(data.unsupported[i].id) === String(modelId)) return data.unsupported[i];
    }
    return null;
  }

  function setExportBanner(notes) {
    var arr = (notes || []).filter(Boolean);
    if (!arr.length) {
      els.exportBanner.classList.add('hidden');
      els.exportBanner.textContent = '';
      return;
    }
    els.exportBanner.classList.remove('hidden');
    els.exportBanner.textContent = arr.join('');
  }

  /** 已生成的 zip 列表 */
  async function loadExportPackages() {
    try {
      var data = await apiJson('/api/ondevice/packages');
      var list = data.packages || [];
      els.exportPkgCount.textContent = String(list.length);
      els.exportPackages.textContent = '';
      if (!list.length) {
        var empty = document.createElement('div');
        empty.className = 'empty small';
        empty.textContent = '还没有导出过（' + data.output_dir + '）';
        els.exportPackages.appendChild(empty);
        return;
      }
      var frag = document.createDocumentFragment();
      for (var i = 0; i < list.length; i++) {
        var p = list[i];
        var row = document.createElement('div');
        row.className = 'pkg-item';

        var name = document.createElement('span');
        name.className = 'name';
        name.textContent = p.name;
        name.title = p.path;

        var meta = document.createElement('span');
        meta.className = 'meta';
        meta.textContent = fmtMb(p.size_mb) + ' · ' + p.mtime_text +
          (p.tiers && p.tiers.length ? ' · T' + p.tiers.join('/T') : '') +
          (p.display ? ' · ' + p.display : '');

        var openBtn = document.createElement('button');
        openBtn.type = 'button';
        openBtn.className = 'mini';
        openBtn.textContent = '打开位置';
        openBtn.dataset.pkgPath = p.path;

        var pushBtn = document.createElement('button');
        pushBtn.type = 'button';
        pushBtn.className = 'mini';
        pushBtn.textContent = '传手机';
        pushBtn.dataset.pkgPush = p.path;
        a11yPush(pushBtn);

        row.appendChild(name);
        row.appendChild(meta);
        row.appendChild(openBtn);
        row.appendChild(pushBtn);
        frag.appendChild(row);
      }
      els.exportPackages.appendChild(frag);
    } catch (err) {
      console.warn('[VITS] 读取端上包列表失败：', err);
      els.exportPackages.textContent = '';
      var bad = document.createElement('div');
      bad.className = 'empty small';
      bad.textContent = '包列表加载失败：' + humanizeError(err);
      els.exportPackages.appendChild(bad);
    }
  }

  /** 重置进度/结果区域 */
  function resetExportPanel() {
    exState.logs = 0;
    exState.result = null;
    els.exportProgress.classList.add('hidden');
    els.exportResult.classList.add('hidden');
    els.exportPushResult.textContent = '';
    els.exportBar.style.width = '0%';
    els.exportPct.textContent = '0%';
    els.exportStage.textContent = '准备中…';
    els.exportMessage.textContent = '';
    els.exportMessage.style.color = '';
    els.exportLog.textContent = '';
  }

  /** 打开导出面板；modelId 可选（从模型卡进入） */
  async function openExportPanel(modelId) {
    els.exportModal.classList.remove('hidden');
    els.exportModal.setAttribute('aria-hidden', 'false');
    resetExportPanel();
    els.exportStart.disabled = true;
    els.exportStatus.textContent = '检测工具链…';
    try {
      await loadExportStatus();
      await loadExportSources(modelId || '');
      await loadExportPackages();
    } catch (err) {
      console.error('[VITS] 打开导出面板失败：', err);
      els.exportStatus.textContent = '工具链检测失败：' + humanizeError(err);
      setExportBanner(['无法读取 /api/ondevice/status：' + humanizeError(err)]);
      showToast('导出面板加载失败：' + humanizeError(err), true);
      return;
    }
    // 如果上次任务还在跑，继续轮询
    if (exState.jobId && exState.busy) {
      startExportPolling(exState.jobId);
    }
  }

  function closeExportPanel() {
    els.exportModal.classList.add('hidden');
    els.exportModal.setAttribute('aria-hidden', 'true');
  }

  /** 开始导出 */
  async function startExport() {
    if (exState.busy) return;
    if (!exState.status || !exState.status.ok) {
      showToast('工具链未就绪，无法导出', true);
      return;
    }
    var modelId = els.exportModel.value;
    if (!modelId) {
      showToast('没有可导出的模型', true);
      return;
    }
    var tiers = selectedExportTiers();
    if (!tiers.length) {
      showToast('至少选一个语音档位', true);
      return;
    }
    var body = {
      model_id: modelId,
      tiers: tiers,
      display: (els.exportDisplay.value || '').trim() || undefined,
      output_dir: (els.exportOut.value || '').trim() || undefined
    };

    exState.busy = true;
    els.exportStart.disabled = true;
    els.exportStart.textContent = '导出中…';
    resetExportPanel();
    els.exportProgress.classList.remove('hidden');
    els.exportStage.textContent = '提交任务…';
    els.exportMessage.textContent = '首次导出要加载模型（CPU），约 20–60 秒；档位越多越久。';
    setStatus('busy', '导出中');

    try {
      var job = await jsonPost('/api/ondevice/build', body);
      exState.jobId = job.id;
      els.exportLog.textContent = '[job] ' + job.id + ' 已创建\n';
      startExportPolling(job.id);
    } catch (err) {
      exState.busy = false;
      els.exportStart.disabled = false;
      els.exportStart.textContent = '开始导出';
      setStatus('error', '导出失败');
      els.exportStage.textContent = '失败';
      els.exportMessage.textContent = humanizeError(err);
      showToast('创建导出任务失败：' + humanizeError(err), true);
    }
  }

  function stopExportPolling() {
    if (exState.timer) {
      window.clearInterval(exState.timer);
      exState.timer = null;
    }
  }

  function startExportPolling(jobId) {
    stopExportPolling();
    exState.timer = window.setInterval(function () {
      pollExportJob(jobId).catch(function (err) {
        console.warn('[VITS] 轮询导出任务失败：', err);
      });
    }, EXPORT_POLL_MS);
    pollExportJob(jobId).catch(function (err) {
      console.warn('[VITS] 轮询导出任务失败：', err);
    });
  }

  /** 轮询进度：阶段 / 进度条 / 日志尾部 / 完成态 */
  async function pollExportJob(jobId) {
    var job = null;
    try {
      job = await apiJson('/api/ondevice/build/' + encodeURIComponent(jobId));
    } catch (err) {
      // 后端重启会 404：停掉轮询，明确提示（不静默）
      stopExportPolling();
      exState.busy = false;
      els.exportStart.disabled = false;
      els.exportStart.textContent = '开始导出';
      var detail = humanizeError(err);
      els.exportStage.textContent = '任务丢失';
      els.exportMessage.textContent = detail + '（后端可能已重启，请重新导出）';
      showToast('导出任务查询失败：' + detail, true);
      return;
    }

    var pct = Math.round((Number(job.progress) || 0) * 100);
    els.exportStage.textContent = (job.stage_label || job.stage || '进行中') +
      (job.status === 'done' ? '' : '…');
    els.exportPct.textContent = pct + '%';
    els.exportBar.style.width = pct + '%';
    els.exportMessage.textContent = job.message || '';
    renderExportLog(job);

    if (job.status === 'done') {
      stopExportPolling();
      exState.busy = false;
      exState.result = job.result || null;
      els.exportStart.disabled = false;
      els.exportStart.textContent = '开始导出';
      renderExportResult(job);
      setStatus('ok', '就绪');
      showToast('导出完成：' + ((job.result && job.result.zip_name) || job.model_id));
      loadExportPackages().catch(function (err) {
        console.warn('[VITS] 刷新包列表失败：', err);
      });
    } else if (job.status === 'error') {
      stopExportPolling();
      exState.busy = false;
      els.exportStart.disabled = false;
      els.exportStart.textContent = '开始导出';
      els.exportStage.textContent = '导出失败';
      var msg = job.error || '未知错误';
      els.exportMessage.textContent = msg + (job.error_hint ? '；建议：' + job.error_hint : '');
      els.exportMessage.style.color = 'var(--danger)';
      setStatus('error', '导出失败');
      showToast('导出失败：' + msg, true);
      renderExportLog(job);
    }
  }

  /** 日志尾部：只在最后追加，避免每 0.8 秒重排整块 */
  function renderExportLog(job) {
    var lines = (job && job.logs_tail) || [];
    // 只显示最近 60 行，过滤纯告警噪音（TracerWarning / FutureWarning 不全铺开）
    var keep = [];
    for (var i = 0; i < lines.length; i++) {
      var line = String(lines[i]);
      var body = line.replace(/^!\s*/, '');
      if (/^\s*(WeightNorm\.apply|_C\._jit_|assert t_s ==|pad_length =|slice_start_position|if pad_length)/.test(body)) continue;
      keep.push(line);
    }
    if (keep.length > 60) keep = keep.slice(-60);
    var text = keep.join('\n');
    if (els.exportLog.textContent !== text) {
      els.exportLog.textContent = text;
      els.exportLog.scrollTop = els.exportLog.scrollHeight;
    }
  }

  /** 完成态：zip 路径 + 大小/耗时/自检 + 打开文件夹 / 传手机 */
  function renderExportResult(job) {
    var r = job.result || {};
    var insp = r.inspect || {};
    var summary = insp.meta_summary || {};
    var parts = [];
    parts.push((r.size_mb ? fmtMb(r.size_mb) : '—'));
    if (job.duration_s) parts.push('用时 ' + trimNum(job.duration_s) + 's');
    if (r.tiers && r.tiers.length) parts.push('T' + r.tiers.join('/T'));
    if (insp.entries) parts.push(insp.entries.length + ' 个文件');
    parts.push(insp.ok ? '自检 ✓' : '自检 ✗');
    if (summary.languages) parts.push('语言 ' + summary.languages.join('/'));
    if (summary.prompts) parts.push('示范句 ' + summary.prompts.join('/'));
    els.exportResultMeta.textContent = parts.join(' · ');
    els.exportResultPath.textContent = r.zip || '';
    els.exportResultPath.title = r.zip || '';
    els.exportResult.classList.remove('hidden');
    els.exportPushResult.textContent = '';
    a11yPush(els.exportPush);
    els.exportMessage.style.color = '';
  }

  /** 打开 zip 所在文件夹 */
  async function revealExport(path) {
    if (!path) return;
    try {
      await jsonPost('/api/ondevice/reveal', { path: path });
    } catch (err) {
      showToast('打开文件夹失败：' + humanizeError(err), true);
    }
  }

  /** 推送到手机（hdc file send → 设备 Download） */
  async function pushExport(path) {
    if (!path) return;
    els.exportPush.disabled = true;
    els.exportPushResult.style.color = '';
    els.exportPushResult.textContent = '正在推送到手机…';
    try {
      var out = await jsonPost('/api/ondevice/install', { zip_path: path });
      if (out.ok) {
        els.exportPushResult.style.color = 'var(--ok)';
        els.exportPushResult.textContent = '已推送到 ' + out.device + '：' + out.target +
          '（' + out.seconds + 's）';
        showToast('已推送到手机，App 里「导入模型」选择它');
      } else {
        els.exportPushResult.style.color = 'var(--danger)';
        els.exportPushResult.textContent = '推送失败：' + (out.output || '').slice(0, 200);
        showToast('推送失败：' + (out.hint || '见界面详情'), true);
      }
    } catch (err) {
      els.exportPushResult.style.color = 'var(--danger)';
      els.exportPushResult.textContent = '推送失败：' + humanizeError(err);
      showToast('推送失败：' + humanizeError(err), true);
    } finally {
      a11yPush(els.exportPush);
    }
  }

  /** 绑定导出面板事件 */
  function setupExport() {
    renderExportTiers();

    els.btnExport.addEventListener('click', function () {
      openExportPanel().catch(reportUnexpected);
    });
    if (els.btnExportSide) {
      els.btnExportSide.addEventListener('click', function () {
        openExportPanel().catch(reportUnexpected);
      });
    }
    els.exportClose.addEventListener('click', closeExportPanel);
    els.exportModal.addEventListener('click', function (ev) {
      if (ev.target === els.exportModal) closeExportPanel();
    });

    els.exportBrowse.addEventListener('click', function () {
      var api = window.pywebview && window.pywebview.api;
      if (api && api.pick_dir) {
        api.pick_dir().then(function (dir) {
          if (dir) els.exportOut.value = dir;
        }).catch(function (err) { showToast('打开选择器失败: ' + err, true); });
      } else {
        showToast('当前窗口不支持文件夹选择器，请直接粘贴路径', true);
      }
    });

    els.exportStart.addEventListener('click', function () {
      startExport().catch(reportUnexpected);
    });
    els.exportReveal.addEventListener('click', function () {
      var path = exState.result ? exState.result.zip : '';
      revealExport(path).catch(reportUnexpected);
    });
    els.exportPush.addEventListener('click', function () {
      var path = exState.result ? exState.result.zip : '';
      pushExport(path).catch(reportUnexpected);
    });

    // 已生成包列表：事件委托（打开位置 / 传手机）
    els.exportPackages.addEventListener('click', function (ev) {
      var openBtn = closestElement(ev.target, '[data-pkg-path]');
      if (openBtn) {
        revealExport(openBtn.dataset.pkgPath).catch(reportUnexpected);
        return;
      }
      var pushBtn = closestElement(ev.target, '[data-pkg-push]');
      if (pushBtn) {
        pushExport(pushBtn.dataset.pkgPush).catch(reportUnexpected);
      }
    });

    els.exportModel.addEventListener('change', function () {
      var opt = els.exportModel.options[els.exportModel.selectedIndex];
      if (opt) els.exportModel.title = opt.title || '';
    });
  }

  function bindEvents() {
    // 模型列表（事件委托，适配重新渲染）
    els.modelList.addEventListener('click', function (ev) {
      // 卡片右上角「导出到手机」优先拦截，不触发选中
      var exportBtn = closestElement(ev.target, '.card-export');
      if (exportBtn && els.modelList.contains(exportBtn)) {
        ev.preventDefault();
        ev.stopPropagation();
        openExportPanel(exportBtn.dataset.exportModel).catch(reportUnexpected);
        return;
      }
      var card = closestElement(ev.target, '.model-card');
      if (!card || !els.modelList.contains(card)) return;
      var model = findModel(card.dataset.modelId);
      if (model) handleModelClick(model);
    });
    els.modelList.addEventListener('keydown', function (ev) {
      if (ev.key !== 'Enter' && ev.key !== ' ') return;
      if (closestElement(ev.target, '.card-export')) return;   // 让按钮自己处理
      var card = closestElement(ev.target, '.model-card');
      if (!card) return;
      ev.preventDefault();
      var model = findModel(card.dataset.modelId);
      if (model) handleModelClick(model);
    });

    // 语言 chips
    els.langChips.addEventListener('click', function (ev) {
      var chip = closestElement(ev.target, '.chip');
      if (!chip || !chip.dataset.lang) return;
      setLang(chip.dataset.lang);
    });

    // 文本域：实时字数 + Ctrl/Cmd+Enter 生成
    els.text.addEventListener('input', updateCharCount);
    els.text.addEventListener('keydown', function (ev) {
      if ((ev.ctrlKey || ev.metaKey) && ev.key === 'Enter') {
        ev.preventDefault();
        if (state.busy) return;
        generate().catch(reportUnexpected);
      }
    });

    // 滑块
    els.speed.addEventListener('input', updateSliderLabels);
    els.temp.addEventListener('input', updateSliderLabels);

    // 生成按钮
    els.btnGen.addEventListener('click', function () {
      if (state.busy) return;
      generate().catch(reportUnexpected);
    });

    // 下载 WAV（后端落盘）+ 打开所在文件夹
    els.download.addEventListener('click', function () {
      saveWav().catch(reportUnexpected);
    });
    els.reveal.addEventListener('click', function () {
      revealSaved().catch(reportUnexpected);
    });

    // 历史列表（事件委托）
    els.historyList.addEventListener('click', function (ev) {
      var item = closestElement(ev.target, '.history-item');
      if (!item || !els.historyList.contains(item)) return;
      fillFromHistory(Number(item.dataset.index));
    });
    els.historyList.addEventListener('keydown', function (ev) {
      if (ev.key !== 'Enter' && ev.key !== ' ') return;
      var item = closestElement(ev.target, '.history-item');
      if (!item) return;
      ev.preventDefault();
      fillFromHistory(Number(item.dataset.index));
    });

    // 添加模型弹窗
    els.btnAdd.addEventListener('click', openModal);
    els.modalClose.addEventListener('click', closeModal);
    els.modal.addEventListener('click', function (ev) {
      if (ev.target === els.modal) closeModal();
    });
    document.addEventListener('keydown', function (ev) {
      if (ev.key !== 'Escape') return;
      if (els.modal && !els.modal.classList.contains('hidden')) closeModal();
      if (els.exportModal && !els.exportModal.classList.contains('hidden')) closeExportPanel();
    });

    // 侧栏底部按钮
    els.btnReload.addEventListener('click', function () {
      reloadModels().catch(reportUnexpected);
    });
    els.btnQuit.addEventListener('click', function () {
      quitApp().catch(reportUnexpected);
    });

    // 最后一道保险：任何漏网的 Promise rejection 都不应打到控制台白屏
    window.addEventListener('unhandledrejection', function (ev) {
      ev.preventDefault();
      reportUnexpected(ev.reason);
    });
  }

  /** 兜底异常处理：记录日志、提示用户，并恢复被卡住的按钮 */
  function reportUnexpected(err) {
    console.error('[VITS] 未预期的错误：', err);
    try {
      showToast('发生错误：' + humanizeError(err), true);
    } catch (ignored) {
      // 连 toast 都失败时不再抛错
    }
    if (state.busy) {
      state.busy = false;
      if (els.btnGen) els.btnGen.textContent = '生成语音';
      if (els.text && els.charCount && els.btnGen) updateCharCount();
    }
  }

  // ===========================================================================
  // 启动
  // ===========================================================================

  function boot() {
    // 所有 DOM 查询都发生在 DOMContentLoaded 之后
    els = {
      modelList: byId('model-list'),
      modelCount: byId('model-count'),
      activeName: byId('active-name'),
      activeMeta: byId('active-meta'),
      statusDot: byId('status-dot'),
      statusText: byId('status-text'),
      gpuInfo: byId('gpu-info'),
      text: byId('text'),
      charCount: byId('char-count'),
      langChips: byId('lang-chips'),
      speed: byId('speed'),
      speedVal: byId('speed-val'),
      temp: byId('temp'),
      tempVal: byId('temp-val'),
      btnGen: byId('btn-gen'),
      audio: byId('audio'),
      metaRender: byId('meta-render'),
      download: byId('download'),
      reveal: byId('reveal'),
      savePath: byId('save-path'),
      historyList: byId('history-list'),
      btnAdd: byId('btn-add'),
      btnExport: byId('btn-export'),
      btnExportSide: byId('btn-export-side'),
      btnReload: byId('btn-reload'),
      btnQuit: byId('btn-quit'),
      modal: byId('modal'),
      modalClose: byId('modal-close'),
      exportModal: byId('export-modal'),
      exportClose: byId('export-close'),
      exportStatus: byId('export-status'),
      exportBanner: byId('export-banner'),
      exportModel: byId('export-model'),
      exportDisplay: byId('export-display'),
      exportTiers: byId('export-tiers'),
      exportOut: byId('export-out'),
      exportBrowse: byId('export-browse'),
      exportStart: byId('export-start'),
      exportHint: byId('export-hint'),
      exportProgress: byId('export-progress'),
      exportStage: byId('export-stage'),
      exportPct: byId('export-pct'),
      exportBar: byId('export-bar'),
      exportMessage: byId('export-message'),
      exportLog: byId('export-log'),
      exportResult: byId('export-result'),
      exportResultMeta: byId('export-result-meta'),
      exportResultPath: byId('export-result-path'),
      exportReveal: byId('export-reveal'),
      exportPush: byId('export-push'),
      exportPushResult: byId('export-push-result'),
      exportPackages: byId('export-packages'),
      exportPkgCount: byId('export-pkg-count'),
      toast: byId('toast')
    };

    // 契约检查：缺少节点时只记录错误，不继续执行导致无意义异常
    var missing = [];
    Object.keys(els).forEach(function (key) {
      if (!els[key]) missing.push(key);
    });
    if (missing.length) {
      console.error('[VITS] 页面缺少必需的 DOM 节点：', missing.join(', '));
      return;
    }

    // 基础 UI 初始化
    renderLangChips();
    setLang('ja');
    updateSliderLabels();
    updateCharCount();
    els.modal.setAttribute('aria-hidden', 'true');
    els.toast.setAttribute('role', 'status');
    els.toast.setAttribute('aria-live', 'polite');

    // 历史记录
    state.history = loadHistory();
    renderHistory();

    // 事件 + 首次数据加载
    bindEvents();
    setupAddModel();
    setupExport();
    loadModels().catch(reportUnexpected);
  }

  // 脚本放在 </body> 前，正常情况走 DOMContentLoaded；defer/晚加载时直接 boot
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }
})();
