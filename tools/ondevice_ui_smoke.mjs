/* 「导出到手机」UI 冒烟测试 / 截图取证（headless Chrome 或 EXE 的 WebView2 + CDP）。
 *
 * 用法：
 *   node app\tools\ondevice_ui_smoke.mjs <URL> <截图目录> [modelId] [tiers]
 *
 * 例 A：对开发后端（headless Chrome，自己开标签页）
 *   chrome --headless=new --remote-debugging-port=9222 ... about:blank
 *   node app\tools\ondevice_ui_smoke.mjs http://127.0.0.1:7788 shots registry:xya 128,256,512,1024
 *
 * 例 B：对**真正运行的 EXE 窗口**（WebView2 开 CDP：启动 EXE 前设
 *   WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS="--remote-debugging-port=9333 --remote-allow-origins=*"）
 *   set CDP_URL=http://127.0.0.1:9333
 *   set CDP_REUSE=1
 *   node app\tools\ondevice_ui_smoke.mjs http://127.0.0.1:<EXE内部端口>/ shots registry:HK416 128,256
 *
 * 环境变量：
 *   CDP_URL     默认 http://127.0.0.1:9222
 *   CDP_REUSE   1 = 复用已打开的页面（EXE 窗口）；否则新开标签页
 *   PANEL_ONLY  1 = 只截「面板打开」这一步，不点开始导出
 *
 * 需要 Node 22+（内置 WebSocket / fetch），无需 puppeteer。
 * 产物：01_home.png、02_panel.png、03_progress.png、04_done.png
 */
const [, , url = 'http://127.0.0.1:7799', outDir = '.', modelId = 'registry:xya', tiers = '128,256,512,1024'] = process.argv;
const fs = await import('node:fs');
const path = await import('node:path');
fs.mkdirSync(outDir, { recursive: true });

const DEBUG = process.env.CDP_URL || 'http://127.0.0.1:9222';
const REUSE = process.env.CDP_REUSE === '1';

async function cdp() {
  let target;
  if (REUSE) {
    // 连到已经打开的页面（例如 pywebview 的 WebView2 窗口）
    const list = await (await fetch(`${DEBUG}/json/list`)).json();
    target = list.find((t) => t.type === 'page' && /^http/.test(t.url || ''));
    if (!target) throw new Error('no page target: ' + JSON.stringify(list.map((t) => t.url)));
  } else {
    const res = await fetch(`${DEBUG}/json/new?about:blank`, { method: 'PUT' });
    if (!res.ok) throw new Error('new tab failed ' + res.status);
    target = await res.json();
  }
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.onopen = resolve;
    ws.onerror = (e) => reject(new Error('ws error'));
  });
  let id = 0;
  const pending = new Map();
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) reject(new Error(JSON.stringify(msg.error)));
      else resolve(msg.result);
    }
  };
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const mid = ++id;
    pending.set(mid, { resolve, reject });
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  return { ws, send };
}

const { ws, send } = await cdp();
await send('Page.enable');
await send('Runtime.enable');
await send('Page.addScriptToEvaluateOnNewDocument', {
  source: `window.__errs=[];window.addEventListener('error',e=>window.__errs.push(String(e.message)));window.addEventListener('unhandledrejection',e=>window.__errs.push('rej:'+String(e.reason&&e.reason.message||e.reason)));`
});

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function evalJs(expression, awaitPromise = false) {
  const res = await send('Runtime.evaluate', {
    expression, awaitPromise, returnByValue: true, userGesture: true,
  });
  if (res.exceptionDetails) throw new Error('page error: ' + JSON.stringify(res.exceptionDetails).slice(0, 500));
  return res.result.value;
}

async function shot(name) {
  const res = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  const file = path.join(outDir, name);
  fs.writeFileSync(file, Buffer.from(res.data, 'base64'));
  console.log('[shot]', file);
}

console.log('[nav]', url);
await send('Page.navigate', { url });
for (let i = 0; i < 60; i++) {
  await sleep(300);
  const ready = await evalJs(`!!document.querySelector('#model-list .model-card')`);
  if (ready) break;
}
await shot('01_home.png');

// 卡片按钮 + 顶部按钮都在
const uiBits = await evalJs(`JSON.stringify({
  cardExport: document.querySelectorAll('.card-export').length,
  topExport: !!document.getElementById('btn-export'),
  sideExport: !!document.getElementById('btn-export-side'),
  cards: document.querySelectorAll('.model-card').length
})`);
console.log('[ui]', uiBits);

// 从模型卡进入导出面板（体现「模型卡里的入口」）
const clicked = await evalJs(`(() => { const b=document.querySelector('.model-card .card-export'); if(!b) return 'no-card-btn'; b.click(); return 'ok'; })()`);
console.log('[click card export]', clicked);
for (let i = 0; i < 40; i++) {
  await sleep(250);
  const open = await evalJs(`!document.getElementById('export-modal').classList.contains('hidden') && document.getElementById('export-model').options.length>0`);
  if (open) break;
}
await sleep(400);
console.log('[panel]', await evalJs(`JSON.stringify({status:document.getElementById('export-status').textContent, banner:(document.getElementById('export-banner').textContent||'').slice(0,160), models:[...document.getElementById('export-model').options].map(o=>o.value)})`));
await shot('02_panel.png');
if (process.env.PANEL_ONLY === '1') {
  console.log('[panel-only] done');
  ws.close();
  process.exit(0);
}

// 选模型 + 档位 + 显示名（显示名只在 xya 时填，避免串到别的模型）
const setup = await evalJs(`(() => {
  const sel=document.getElementById('export-model');
  const wanted=${JSON.stringify(modelId)};
  if ([...sel.options].some(o=>o.value===wanted)) sel.value=wanted;
  const wantTiers=${JSON.stringify(tiers.split(',').map(Number))};
  document.querySelectorAll('#export-tiers input[type=checkbox]').forEach(b=>{
    b.checked = wantTiers.includes(Number(b.value));
    b.closest('.tier').classList.toggle('on', b.checked);
  });
  document.getElementById('export-display').value = wanted.indexOf('xya')>=0 ? 'xya · 星野爱' : '';
  return JSON.stringify({model:sel.value, tiers:[...document.querySelectorAll('#export-tiers input:checked')].map(b=>Number(b.value))});
})()`);
console.log('[setup]', setup);

await evalJs(`document.getElementById('export-start').click()`);
console.log('[start] clicked');

let midShot = false;
let final = null;
for (let i = 0; i < 500; i++) {
  await sleep(1000);
  const st = await evalJs(`JSON.stringify({
    stage: document.getElementById('export-stage').textContent,
    pct: document.getElementById('export-pct').textContent,
    msg: document.getElementById('export-message').textContent,
    progHidden: document.getElementById('export-progress').classList.contains('hidden'),
    resultHidden: document.getElementById('export-result').classList.contains('hidden'),
    path: document.getElementById('export-result-path').textContent,
    meta: document.getElementById('export-result-meta').textContent,
    push: document.getElementById('export-push').textContent,
    pushDisabled: document.getElementById('export-push').disabled,
    logLines: document.getElementById('export-log').textContent.split('\\n').length
  })`);
  const o = JSON.parse(st);
  if (!midShot && parseInt(o.pct, 10) >= 45 && o.resultHidden) {
    await shot('03_progress.png');
    console.log('[progress]', o.stage, o.pct);
    midShot = true;
  }
  if (!o.resultHidden) { final = o; break; }
  if (/失败|任务丢失/.test(o.stage)) { final = o; break; }
  if (i % 5 === 0) console.log(`[poll ${i}s] ${o.pct} ${o.stage} ${String(o.msg).slice(0, 60)}`);
}
if (!final) throw new Error('timeout waiting build');
console.log('[final]', JSON.stringify(final).slice(0, 800));
// 滚到完成态，截图里能看到 zip 路径 + 按钮
await evalJs(`(() => { const r=document.getElementById('export-result'); if(r) r.scrollIntoView({block:'center'}); })()`);
await sleep(700);
await shot('04_done.png');

// 已生成包列表
console.log('[packages]', await evalJs(`JSON.stringify({count:document.getElementById('export-pkg-count').textContent, rows:[...document.querySelectorAll('#export-packages .pkg-item')].map(r=>r.innerText.replace(/\\n/g,' | '))})`));
console.log('[errors]', await evalJs(`JSON.stringify(window.__errs)`));

ws.close();
process.exit(0);
