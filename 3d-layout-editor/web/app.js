import { SceneRoot, setMaterialCatalog, MATERIAL_COLORS, materialLoss } from './scene.js';

const $ = (id) => document.getElementById(id);
const stage = $('stage');
const labelLayer = $('labels');
const tooltip = $('tooltip');
const notice = $('notice');
const toastEl = $('toast');
const loading = $('loading');

let sceneRoot = null;
let layouts = [];
let toastTimer = null;
let history = [];
let analyzeSeq = 0;
let currentFile = null;
let selectedItem = null;
let simulateTimer = null;
let simulateSeq = 0;
let lastSimulateAt = 0;
let presenting = false;
let aiEnvironment = 'empty';
let aiSeq = 0;
let band = '2.4g';
let materialData = null;

const escapeHtml = (value) =>
  String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));

const FURNITURE_LIBRARY = [
  { name: '沙发', kind: 'sofa', w: 2.0, d: 0.9, h: 0.86, color: '#6d7fa6' },
  { name: '床', kind: 'bed', w: 1.5, d: 2.0, h: 0.56, color: '#9db0cf' },
  { name: '衣柜', kind: 'cabinet', w: 1.2, d: 0.6, h: 1.85, color: '#8d6f52' },
  { name: '书桌', kind: 'desk', w: 1.2, d: 0.6, h: 0.75, color: '#b08356' },
  { name: '餐桌', kind: 'table', w: 1.3, d: 0.8, h: 0.75, color: '#9c7148' },
  { name: '椅子', kind: 'chair', w: 0.45, d: 0.45, h: 0.95, color: '#7f8dab' },
  { name: '茶几', kind: 'coffee_table', w: 1.0, d: 0.5, h: 0.45, color: '#8a6a45' },
  { name: '床头柜', kind: 'nightstand', w: 0.45, d: 0.4, h: 0.55, color: '#9a7a58' },
  { name: '边几', kind: 'side_table', w: 0.4, d: 0.4, h: 0.55, color: '#9c7650' },
  { name: '电视柜', kind: 'tv_stand', w: 1.8, d: 0.4, h: 0.5, color: '#6f5540' },
  { name: '冰箱', kind: 'fridge', w: 0.65, d: 0.65, h: 1.82, color: '#d7dde7' },
  { name: '洗衣机', kind: 'washer', w: 0.6, d: 0.6, h: 0.92, color: '#e3e7ee' },
  { name: '绿植', kind: 'plant', w: 0.45, d: 0.45, h: 1.05, color: '#3f8f5a' },
  { name: '空调', kind: 'aircon', w: 0.9, d: 0.32, h: 1.9, color: '#dde3ec' },
  { name: '砖墙 120', kind: 'brick_120', w: 2.0, d: 0.12, h: 2.2, color: '#c08a6a' },
  { name: '砖墙 240', kind: 'brick_240', w: 2.0, d: 0.24, h: 2.2, color: '#a8543f' },
  { name: '木门', kind: 'wood_door', w: 0.9, d: 0.08, h: 2.1, color: '#a97b4f' },
  { name: '玻璃窗', kind: 'glass_window', w: 1.2, d: 0.06, h: 1.2, color: '#7fc4d8' },
  { name: '混凝土柱', kind: 'concrete', w: 0.4, d: 0.4, h: 2.2, color: '#8f9599' },
  { name: '金属板', kind: 'metal_panel', w: 1.0, d: 0.05, h: 2.0, color: '#c9d2dd' },
];

function toast(message) {
  toastEl.textContent = message;
  toastEl.classList.remove('hidden');
  requestAnimationFrame(() => toastEl.classList.add('show'));
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toastEl.classList.remove('show');
    setTimeout(() => toastEl.classList.add('hidden'), 200);
  }, 1800);
}

function setLoading(on) {
  loading.classList.toggle('hidden', !on);
}

function showError(message) {
  notice.className = '';
  notice.innerHTML = escapeHtml(message);
  notice.classList.remove('hidden');
}

async function api(url, options = {}) {
  const init = { cache: 'no-store', ...options };
  if (init.body && typeof init.body !== 'string') {
    init.body = JSON.stringify(init.body);
    init.headers = { 'Content-Type': 'application/json', ...(init.headers || {}) };
  }
  const res = await fetch(url, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `请求失败（${res.status}）`);
  return data;
}

/* ---------- 悬停详情 ---------- */

function furnitureTipHtml(item) {
  const material = item.material_label || (item.material ? item.material : '自动');
  return `
    <div class="tt-title">${escapeHtml(item.name)}<span class="tt-badge ok">家具</span></div>
    <div class="tt-grid">
      <span>尺寸</span><b>${Math.round(item.width_m * 100)} × ${Math.round(item.depth_m * 100)} cm</b>
      <span>中心</span><b>${item.cx.toFixed(2)}, ${item.cz.toFixed(2)} m</b>
      <span>高度</span><b>${item.height_m.toFixed(2)} m（估算）</b>
      <span>材质</span><b>${escapeHtml(material)}</b>
    </div>`;
}

function placeTooltip(client) {
  if (!client) return;
  const pad = 16;
  const rect = tooltip.getBoundingClientRect();
  let x = client.x + pad;
  let y = client.y + pad;
  if (x + rect.width > window.innerWidth - 10) x = client.x - rect.width - pad;
  if (y + rect.height > window.innerHeight - 10) y = client.y - rect.height - pad;
  tooltip.style.left = `${Math.max(8, x)}px`;
  tooltip.style.top = `${Math.max(8, y)}px`;
}

function onHover(info) {
  if (!info) {
    tooltip.classList.remove('show');
    return;
  }
  if (info.type === 'furniture') {
    tooltip.innerHTML = furnitureTipHtml(info.item);
  } else {
    const c = info.cell;
    tooltip.innerHTML = `
      <div class="tt-title">${escapeHtml(c.label)}
        <span class="tt-badge ok">测点</span>
      </div>
      <div class="tt-grid">
        <span>行列</span><b>第 ${c.row} 行 · 第 ${c.column} 列</b>
        <span>坐标</span><b>${c.x_m.toFixed(2)}, ${c.y_m.toFixed(2)} m</b>
        ${info.heat ? `<span>预测信号</span><b>${info.heat.rssi_dbm.toFixed(1)} dBm</b>` : ''}
        ${info.heat ? `<span>穿透损耗</span><b>${info.heat.penetration_db.toFixed(1)} dB</b>` : ''}
      </div>`;
  }
  tooltip.classList.add('show');
  placeTooltip(info.client);
}

/* ---------- HUD 与提示 ---------- */

function updateHud(data) {
  const room = data.room;
  const stats = data.stats;
  const problems = data.problems || [];
  const rows = [
    ['房间', `${room.width_m.toFixed(2)} × ${room.height_m.toFixed(2)} m`],
    ['网格', `${room.rows} × ${room.columns} 格`],
    ['家具', `${stats.furniture_count} 件`],
    ['布局校验', problems.length ? `${problems.length} 个问题` : '通过', problems.length ? 'bad' : 'ok'],
  ];
  const heat = sceneRoot?.heatData;
  if (heat) {
    rows.splice(2, 0, ['平均信号', `${heat.stats.mean_dbm.toFixed(1)} dBm`]);
    rows.splice(3, 0, ['强覆盖', `${heat.stats.strong_cells} 格`]);
  }
  $('hud').innerHTML = `
    <div class="hud-title">物理仿真</div>
    ${rows.map(([k, v, cls]) => `<div class="row"><span>${k}</span><b class="${cls || ''}">${escapeHtml(v)}</b></div>`).join('')}
  `;
}

function updateNotice(data) {
  const problems = data.problems || [];
  if (problems.length) {
    notice.className = '';
    notice.innerHTML = `布局提醒：${problems.map(escapeHtml).join('；')}`;
    notice.classList.remove('hidden');
    return;
  }
  if (!data.furniture.length) {
    notice.className = 'warn';
    notice.innerHTML = '当前布局还没有家具。<button id="btnDemoInline">加载示例房间</button>';
    notice.classList.remove('hidden');
    const btn = $('btnDemoInline');
    if (btn) btn.onclick = () => load('layout.demo.json');
    return;
  }
  notice.classList.add('hidden');
}

/* ---------- 数据加载 ---------- */

function fillSelect(items) {
  const select = $('layoutSelect');
  select.innerHTML = items.map((item) => {
    const label = item.error
      ? `${item.file}（无法读取）`
      : `${item.file} · 家具 ${item.furniture}`;
    return `<option value="${escapeHtml(item.file)}">${escapeHtml(label)}</option>`;
  }).join('');
  select.onchange = () => load(select.value);
}

async function refreshLayouts() {
  const payload = await api('/api/layouts');
  layouts = payload.layouts || [];
  fillSelect(layouts);
}

async function load(file) {
  setLoading(true);
  try {
    const data = await api(`/api/scene?file=${encodeURIComponent(file)}`);
    currentFile = file;
    history = [];
    selectedItem = null;
    updateDeleteButton(null);
    sceneRoot.setLayout(data);
    $('layoutSelect').value = file;
    syncRoomInputs();
    updateHud(data);
    updateNotice(data);
    runSimulate();
  } catch (error) {
    showError(`加载失败：${error.message}`);
  } finally {
    setLoading(false);
  }
}

/* ---------- 编辑 ---------- */

async function reanalyze() {
  if (!sceneRoot) return;
  const seq = ++analyzeSeq;
  try {
    const state = await api('/api/analyze', {
      method: 'POST',
      body: sceneRoot.getLayoutPayload(),
    });
    if (seq !== analyzeSeq) return;
    sceneRoot.applyState(state);
    updateHud(sceneRoot.data);
    updateNotice(sceneRoot.data);
    scheduleSimulate();
  } catch (error) {
    if (seq === analyzeSeq) showError(`重新计算失败：${error.message}`);
  }
}

async function runSimulate() {
  if (!sceneRoot) return;
  const seq = ++simulateSeq;
  try {
    const data = await api('/api/simulate', {
      method: 'POST',
      body: { ...sceneRoot.getLayoutPayload(), band },
    });
    if (seq !== simulateSeq) return;
    sceneRoot.setHeatmap(data);
    updateHud(sceneRoot.data);
    updateHeatLegend(data);
    scheduleAI();
  } catch (error) {
    console.warn('仿真失败', error);
  }
}

function throttleSimulate(interval = 160) {
  const now = performance.now();
  if (now - lastSimulateAt < interval) return;
  lastSimulateAt = now;
  runSimulate();
}

function scheduleSimulate(delay = 220) {
  clearTimeout(simulateTimer);
  simulateTimer = setTimeout(runSimulate, delay);
}

function updateHeatLegend(data) {
  if (!data) return;
  const text = `平均 ${data.stats.mean_dbm.toFixed(1)} dBm · 强覆盖 ${data.stats.strong_cells} 格`;
  const el = $('heatStats');
  if (el) el.textContent = text;
  const present = $('presentStats');
  if (present) present.textContent = text;
}

function setBand(value) {
  band = value;
  $('btnBand24')?.classList.toggle('active', value === '2.4g');
  $('btnBand5')?.classList.toggle('active', value === '5g');
  runSimulate();
}

async function loadMaterials() {
  try {
    const data = await api('/api/materials');
    materialData = data.materials;
    setMaterialCatalog(data.materials, data.kind_material);
    sceneRoot?.refreshMaterials();
    const dot = (key) => {
      const color = MATERIAL_COLORS[key] ?? 0x8a93a5;
      return `<i class="mat-dot" style="background:#${color.toString(16).padStart(6, '0')}"></i>`;
    };
    const tbody = document.querySelector('#materialTable tbody');
    if (tbody) {
      tbody.innerHTML = Object.entries(data.materials)
        .map(
          ([key, m]) =>
            `<tr><td>${dot(key)}${m.label}</td><td>${m.thickness_mm}</td><td>${m.loss['2.4g'].toFixed(0)}</td><td>${m.loss['5g'].toFixed(0)}</td></tr>`
        )
        .join('');
    }
    const entries = Object.entries(data.materials);
    const prop = $('propMaterial');
    if (prop) {
      prop.innerHTML =
        '<option value="">自动（按家具类型）</option>' +
        entries
          .map(
            ([key, m]) =>
              `<option value="${key}">${m.label} · ${m.loss['2.4g'].toFixed(0)}/${m.loss['5g'].toFixed(0)} dB</option>`
          )
          .join('');
    }
    const lib = $('libMaterial');
    if (lib) {
      lib.innerHTML =
        '<option value="">自动</option>' +
        entries.map(([key, m]) => `<option value="${key}">${m.label}</option>`).join('');
    }
    const card = $('itemCardMaterial');
    if (card) {
      card.innerHTML =
        '<option value="">自动</option>' +
        entries
          .map(
            ([key, m]) =>
              `<option value="${key}">${m.label} · ${m.loss['2.4g'].toFixed(0)}/${m.loss['5g'].toFixed(0)} dB</option>`
          )
          .join('');
    }
  } catch (error) {
    console.warn('材料表加载失败', error);
  }
}

function renderProperties(item) {
  const section = $('propSection');
  if (!section) return;
  if (!item) {
    section.classList.add('hidden');
    renderItemCard(null);
    return;
  }
  const wasHidden = section.classList.contains('hidden');
  section.classList.remove('hidden');
  const nameEl = $('propName');
  if (nameEl) nameEl.textContent = item.name;
  const sizeEl = $('propSize');
  if (sizeEl) {
    sizeEl.textContent = `${Math.round(item.width_m * 100)}×${Math.round(item.depth_m * 100)} cm`;
  }
  const select = $('propMaterial');
  if (select) select.value = item.material || '';
  const tip = $('propTip');
  if (tip) {
    const m = item.material ? materialData?.[item.material] : null;
    tip.textContent = m
      ? `按「${m.label}」：2.4G ${m.loss['2.4g'].toFixed(0)} dB / 5G ${m.loss['5g'].toFixed(0)} dB`
      : '自动模式：按家具类型推断材料';
  }
  renderItemCard(item);
  if (wasHidden) {
    requestAnimationFrame(() => section.scrollIntoView({ block: 'nearest', behavior: 'smooth' }));
  }
}

// 3D 场景里跟随选中家具的材质卡
function renderItemCard(item) {
  const card = $('itemCard');
  if (!card) return;
  if (!item) {
    card.classList.add('hidden');
    return;
  }
  const nameEl = $('itemCardName');
  if (nameEl) nameEl.textContent = item.name;
  const lossEl = $('itemCardLoss');
  if (lossEl) {
    const a = Math.round(materialLoss(item, '2.4g'));
    const b = Math.round(materialLoss(item, '5g'));
    lossEl.textContent = `${a}/${b} dB`;
  }
  const select = $('itemCardMaterial');
  if (select) select.value = item.material || '';
  card.classList.remove('hidden');
  card.style.opacity = '0';
  card.style.pointerEvents = 'none';
}

function positionItemCard(anchor) {
  const card = $('itemCard');
  if (!card || card.classList.contains('hidden')) return;
  if (!anchor || !anchor.visible) {
    card.style.opacity = '0';
    card.style.pointerEvents = 'none';
    return;
  }
  const cardW = card.offsetWidth || 200;
  const cardH = card.offsetHeight || 62;
  const gap = 10;
  const minY = 78 + cardH; // 避开顶栏
  const maxX = window.innerWidth - 320; // 避开右侧栏
  const half = cardW / 2 + gap;
  const x = Math.min(Math.max(anchor.x, half), Math.max(half, maxX - cardW / 2));
  const y = Math.max(anchor.y, minY);
  card.style.left = `${x}px`;
  card.style.top = `${y}px`;
  card.style.opacity = '1';
  card.style.pointerEvents = 'auto';
}

async function applyFurnitureMaterial(material) {
  if (!selectedItem) return;
  const target = selectedItem;
  const next = material || '';
  if ((target.material || '') === next) return;
  const before = sceneRoot.snapshot();
  sceneRoot.setFurnitureMaterial(target.index, next);
  history.push(before);
  if (history.length > 50) history.shift();
  renderProperties(target);
  await reanalyze();
  const label = next ? materialData?.[next]?.label || next : '自动';
  toast(`「${target.name}」材质：${label}`);
}

function syncRoomInputs() {
  if (!sceneRoot) return;
  const w = $('roomWidth');
  const d = $('roomDepth');
  if (w) w.value = sceneRoot.room.w.toFixed(2);
  if (d) d.value = sceneRoot.room.d.toFixed(2);
}

async function applyRoomSize() {
  if (!sceneRoot) return;
  const w = Number($('roomWidth').value);
  const d = Number($('roomDepth').value);
  if (!Number.isFinite(w) || !Number.isFinite(d) || w < 2 || w > 30 || d < 2 || d > 30) {
    toast('房间尺寸需在 2–30 m 之间');
    return;
  }
  if (Math.abs(w - sceneRoot.room.w) < 1e-6 && Math.abs(d - sceneRoot.room.d) < 1e-6) {
    toast('尺寸没有变化');
    return;
  }
  const before = sceneRoot.snapshot();
  sceneRoot.setRoomSize(w, d);
  history.push(before);
  if (history.length > 50) history.shift();
  selectedItem = null;
  updateDeleteButton(null);
  renderProperties(null);
  syncRoomInputs();
  await reanalyze();
  runSimulate();
  toast(`房间已改为 ${w.toFixed(2)} × ${d.toFixed(2)} m`);
}

function setAIMode(kind, value) {
  if (kind !== 'environment') return;
  aiEnvironment = value;
  $('btnAIEmpty')?.classList.toggle('active', value === 'empty');
  $('btnAIFurnished')?.classList.toggle('active', value === 'furnished');
  if (aiAuto) scheduleAI(120);
}

function updateAIStats(data) {
  if (!data?.stats?.class_counts) return;
  const counts = data.stats.class_counts;
  const total = counts.reduce((sum, value) => sum + value, 0) || 1;
  const strong = ((counts[3] + counts[4]) / total) * 100;
  const confidence = (data.stats.mean_confidence ?? 0) * 100;
  const el = $('aiStats');
  if (el) el.textContent = `强覆盖 ${strong.toFixed(0)}% · 平均置信 ${confidence.toFixed(0)}% · 64×76`;
  const present = $('presentAiStats');
  if (present) present.textContent = `强覆盖 ${strong.toFixed(0)}% · 置信 ${confidence.toFixed(0)}%`;
}

let aiAuto = false;
let aiTimer = null;

function scheduleAI(delay = 1400) {
  if (!aiAuto || !sceneRoot) return;
  clearTimeout(aiTimer);
  aiTimer = setTimeout(() => runAI({ auto: true }), delay);
}

async function runAI(options = {}) {
  if (!sceneRoot) return;
  if (!options.auto) aiAuto = true;
  const buttons = [$('btnAIRun'), $('btnAI')].filter(Boolean);
  const labels = buttons.map((button) => button.textContent);
  buttons.forEach((button) => {
    button.disabled = true;
  });
  const runButton = $('btnAIRun');
  if (runButton && !options.auto) runButton.textContent = '推理中…';
  const seq = ++aiSeq;
  try {
    const payload = {
      environment: aiEnvironment,
      input: 'layout',
      band,
      layout: sceneRoot.getLayoutPayload(),
    };
    const data = await api('/api/predict', { method: 'POST', body: payload });
    if (seq !== aiSeq) return;
    const heatToggle = $('tgHeat');
    if (heatToggle?.checked) {
      heatToggle.checked = false;
      sceneRoot.setLayer('heatmap', false);
    }
    sceneRoot.setAiHeatmap(data);
    updateAIStats(data);
    document.body.classList.add('ai-presenting');
    if (!options.auto) toast('AI 补全完成，后续改动会自动跟随');
  } catch (error) {
    if (seq === aiSeq) showError(`AI 补全失败：${error.message}`);
  } finally {
    if (seq === aiSeq) {
      buttons.forEach((button, index) => {
        button.disabled = false;
        button.textContent = labels[index];
      });
    }
  }
}

function togglePresent(force) {
  presenting = typeof force === 'boolean' ? force : !presenting;
  document.body.classList.toggle('presenting', presenting);
  const button = $('btnPresent');
  if (button) button.textContent = presenting ? '退出演示' : '演示';
  if (!presenting) {
    document.body.classList.remove('ai-presenting');
    if (sceneRoot?.aiData) {
      sceneRoot.clearAiHeatmap();
      const heatToggle = $('tgHeat');
      if (heatToggle) heatToggle.checked = true;
      sceneRoot.setLayer('heatmap', true);
      runSimulate();
    }
  }
  sceneRoot.setView(presenting ? 'corner' : 'iso', 520);
}

function onCommit(change) {
  history.push(change.before);
  if (history.length > 50) history.shift();
  if (change.type === 'ap') {
    toast(`路由器已移到 ${sceneRoot.data.ap.label}`);
  } else {
    const item = sceneRoot.data.furniture.find((f) => f.index === change.index);
    if (item) toast(`已移动「${item.name}」`);
  }
  reanalyze();
}

async function undo() {
  const before = history.pop();
  if (!before) {
    toast('没有可撤销的操作');
    return;
  }
  sceneRoot.restore(before);
  selectedItem = null;
  updateDeleteButton(null);
  renderProperties(null);
  syncRoomInputs();
  await reanalyze();
  toast('已撤销');
}

function updateDeleteButton(item) {
  const btn = $('btnDelete');
  if (!btn) return;
  btn.disabled = !item;
  btn.textContent = item ? `删除「${item.name}」` : '删除选中家具';
}

async function deleteSelected() {
  const item = selectedItem;
  if (!item) {
    toast('先点选一件家具');
    return;
  }
  const before = sceneRoot.snapshot();
  sceneRoot.deleteFurniture(item.index);
  history.push(before);
  if (history.length > 50) history.shift();
  selectedItem = null;
  updateDeleteButton(null);
  await reanalyze();
  toast(`已删除「${item.name}」`);
}

function renderLibrary() {
  const list = $('libList');
  if (!list) return;
  list.innerHTML = FURNITURE_LIBRARY.map(
    (item, index) => `
      <button class="lib-item" data-index="${index}" title="添加${escapeHtml(item.name)}">
        <i style="background:${item.color}"></i>
        <span>${escapeHtml(item.name)}</span>
        <em>${Math.round(item.w * 100)}×${Math.round(item.d * 100)}</em>
      </button>`
  ).join('');
  list.querySelectorAll('.lib-item').forEach((button) => {
    button.addEventListener('click', () =>
      addFromLibrary(FURNITURE_LIBRARY[Number(button.dataset.index)])
    );
  });
}

async function addFromLibrary(spec) {
  if (!sceneRoot) return;
  const before = sceneRoot.snapshot();
  const material = $('libMaterial')?.value || '';
  const { item } = sceneRoot.addFurniture({ ...spec, material });
  history.push(before);
  if (history.length > 50) history.shift();
  selectedItem = item;
  updateDeleteButton(item);
  renderProperties(item);
  await reanalyze();
  const label = material ? materialData?.[material]?.label || material : '自动';
  toast(`已添加「${item.name}」（${label}），拖动它摆位置`);
}

async function save() {
  if (!sceneRoot) return;
  try {
    const result = await api('/api/save', {
      method: 'POST',
      body: { file: 'layout.edited.json', layout: sceneRoot.getLayoutPayload() },
    });
    await refreshLayouts();
    $('layoutSelect').value = result.file;
    currentFile = result.file;
    toast(`已保存到 ${result.file}`);
    if (result.problems?.length) {
      notice.className = '';
      notice.innerHTML = `已保存，但布局有提醒：${result.problems.map(escapeHtml).join('；')}`;
      notice.classList.remove('hidden');
    }
  } catch (error) {
    showError(`保存失败：${error.message}`);
  }
}

function download() {
  if (!sceneRoot) return;
  const payload = sceneRoot.getLayoutPayload();
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: 'application/json;charset=utf-8',
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = 'layout.edited.json';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast('已下载 layout.edited.json');
}

/* ---------- 界面绑定 ---------- */

function setActiveView(button) {
  document.querySelectorAll('[data-view]').forEach((el) => el.classList.toggle('active', el === button));
}

function bindUi() {
  document.querySelectorAll('[data-view]').forEach((button) => {
    button.addEventListener('click', () => {
      sceneRoot.setView(button.dataset.view);
      setActiveView(button);
    });
  });

  const layerMap = {
    tgGrid: 'grid',
    tgLabels: 'labels',
    tgWalls: 'walls',
  };
  Object.entries(layerMap).forEach(([id, layer]) => {
    const input = $(id);
    if (!input) return;
    input.addEventListener('change', () => sceneRoot.setLayer(layer, input.checked));
  });

  $('tgSnap').addEventListener('change', (event) => {
    sceneRoot.snapStep = event.target.checked ? 0.05 : 0;
  });

  $('tgHeat').addEventListener('change', (event) => {
    sceneRoot.setLayer('heatmap', event.target.checked);
    if (event.target.checked) {
      sceneRoot.clearAiHeatmap();
      document.body.classList.remove('ai-presenting');
    }
  });

  const orbitBtn = $('btnOrbit');
  orbitBtn.addEventListener('click', () => {
    const on = !orbitBtn.classList.contains('active');
    orbitBtn.classList.toggle('active', on);
    sceneRoot.setAutoRotate(on);
  });

  $('btnSave').addEventListener('click', save);
  $('btnUndo').addEventListener('click', undo);
  $('btnDelete').addEventListener('click', deleteSelected);
  $('btnDownload').addEventListener('click', download);
  $('btnAI').addEventListener('click', () => runAI());
  $('btnBand24')?.addEventListener('click', () => setBand('2.4g'));
  $('btnBand5')?.addEventListener('click', () => setBand('5g'));
  $('btnRoomApply')?.addEventListener('click', applyRoomSize);
  $('propMaterial')?.addEventListener('change', (event) => applyFurnitureMaterial(event.target.value));
  $('itemCardMaterial')?.addEventListener('change', (event) => applyFurnitureMaterial(event.target.value));
  $('btnPresent').addEventListener('click', () => togglePresent());
  $('btnAIEmpty')?.addEventListener('click', () => setAIMode('environment', 'empty'));
  $('btnAIFurnished')?.addEventListener('click', () => setAIMode('environment', 'furnished'));
  $('btnAIRun')?.addEventListener('click', runAI);
  $('btnDemoLayout').addEventListener('click', () => load('layout.demo.json'));
  $('btnEmptyLayout').addEventListener('click', () => load('layout.json'));

  window.addEventListener('keydown', (event) => {
    if (['SELECT', 'INPUT', 'TEXTAREA'].includes(event.target.tagName)) return;
    if (event.key === 'Escape' && presenting) {
      togglePresent(false);
      return;
    }
    const key = event.key.toLowerCase();
    if ((event.ctrlKey || event.metaKey) && key === 'z') {
      event.preventDefault();
      undo();
      return;
    }
    if ((event.ctrlKey || event.metaKey) && key === 's') {
      event.preventDefault();
      save();
      return;
    }
    if (event.ctrlKey || event.metaKey || event.altKey) return;

    if (key === 'delete' || key === 'backspace') {
      event.preventDefault();
      deleteSelected();
      return;
    }

    const viewKeys = { '1': 'iso', '2': 'top', '3': 'corner', '4': 'front' };
    if (viewKeys[key]) {
      const btn = document.querySelector(`[data-view="${viewKeys[key]}"]`);
      sceneRoot.setView(viewKeys[key]);
      setActiveView(btn);
      return;
    }
    const toggles = { g: 'tgGrid', l: 'tgLabels', w: 'tgWalls', h: 'tgHeat' };
    if (toggles[key]) {
      const input = $(toggles[key]);
      input.checked = !input.checked;
      input.dispatchEvent(new Event('change'));
      return;
    }
    if (key === ' ') {
      event.preventDefault();
      orbitBtn.click();
    }
  });
}

/* ---------- 启动 ---------- */

async function init() {
  try {
    sceneRoot = new SceneRoot(stage, labelLayer, {
      onHover,
      onCommit,
      onDrag: (item) => {
        if (item) {
          const client = sceneRoot.pointerClient;
          if (client) {
            tooltip.innerHTML = furnitureTipHtml(item);
            tooltip.classList.add('show');
            placeTooltip(client);
          }
        }
        throttleSimulate();
      },
      onSelect: (item) => {
        selectedItem = item;
        updateDeleteButton(item);
        renderProperties(item);
        if (!item) tooltip.classList.remove('show');
      },
      onSelectedFrame: (anchor) => positionItemCard(anchor),
    });
  } catch (error) {
    setLoading(false);
    showError('浏览器无法初始化 3D 渲染（可能不支持 WebGL）。请换用较新的 Chrome / Edge 打开。');
    console.error(error);
    return;
  }

  bindUi();
  renderLibrary();
  loadMaterials();
  window.__miniAuto3D = sceneRoot; // 便于调试和自动化检查

  try {
    await refreshLayouts();
    if (!layouts.length) {
      showError('data 目录下没有可用的 layout JSON。');
      setLoading(false);
      return;
    }
  } catch (error) {
    showError(`读取布局列表失败：${error.message}`);
    setLoading(false);
    return;
  }

  const wanted = new URLSearchParams(location.search).get('layout');
  const preferred =
    layouts.find((item) => item.file === wanted)?.file ||
    layouts.find((item) => item.file === 'layout.json')?.file ||
    layouts[0].file;

  await load(preferred);
  setLoading(false);
  if (new URLSearchParams(location.search).get('present') === '1') {
    togglePresent(true);
  }
}

init();
