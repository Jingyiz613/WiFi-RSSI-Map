import { SceneRoot } from './scene.js';

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
  return `
    <div class="tt-title">${escapeHtml(item.name)}<span class="tt-badge ok">家具</span></div>
    <div class="tt-grid">
      <span>尺寸</span><b>${Math.round(item.width_m * 100)} × ${Math.round(item.depth_m * 100)} cm</b>
      <span>中心</span><b>${item.cx.toFixed(2)}, ${item.cz.toFixed(2)} m</b>
      <span>高度</span><b>${item.height_m.toFixed(2)} m（估算）</b>
      <span>禁行</span><b>${item.blocked_cells} 格</b>
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
    const state = c.blocked
      ? c.reason === 'furniture'
        ? `家具「${escapeHtml(c.owner || '未知')}」`
        : '手动障碍格'
      : '可测量';
    tooltip.innerHTML = `
      <div class="tt-title">${escapeHtml(c.label)}
        <span class="tt-badge ${c.blocked ? 'bad' : 'ok'}">${c.blocked ? '禁行' : '可采'}</span>
      </div>
      <div class="tt-grid">
        <span>状态</span><b>${state}</b>
        <span>行列</span><b>第 ${c.row} 行 · 第 ${c.column} 列</b>
        <span>坐标</span><b>${c.x_m.toFixed(2)}, ${c.y_m.toFixed(2)} m</b>
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
    ['家具', `${stats.furniture_count} 件 · 挡 ${stats.furniture_cells} 格`],
    ['可测格', `${stats.usable} 格`],
    ['布局校验', problems.length ? `${problems.length} 个问题` : '通过', problems.length ? 'bad' : 'ok'],
  ];
  $('hud').innerHTML = `
    <div class="hud-title">当前布局</div>
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
    updateHud(data);
    updateNotice(data);
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
  } catch (error) {
    if (seq === analyzeSeq) showError(`重新计算失败：${error.message}`);
  }
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
  const { item } = sceneRoot.addFurniture(spec);
  history.push(before);
  if (history.length > 50) history.shift();
  selectedItem = item;
  updateDeleteButton(item);
  await reanalyze();
  toast(`已添加「${item.name}」，拖动它摆位置`);
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
    tgBlocked: 'blocked',
    tgLabels: 'labels',
    tgWalls: 'walls',
  };
  Object.entries(layerMap).forEach(([id, layer]) => {
    const input = $(id);
    input.addEventListener('change', () => sceneRoot.setLayer(layer, input.checked));
  });

  $('tgSnap').addEventListener('change', (event) => {
    sceneRoot.snapStep = event.target.checked ? 0.05 : 0;
  });

  const orbitBtn = $('btnOrbit');
  orbitBtn.addEventListener('click', () => {
    const on = !orbitBtn.classList.contains('active');
    orbitBtn.classList.toggle('active', on);
    sceneRoot.setAutoRotate(on);
  });

  $('btnShot').addEventListener('click', () => {
    sceneRoot.screenshot();
    toast('已保存 PNG');
  });

  $('btnSave').addEventListener('click', save);
  $('btnUndo').addEventListener('click', undo);
  $('btnDelete').addEventListener('click', deleteSelected);
  $('btnDownload').addEventListener('click', download);

  const fullBtn = $('btnFull');
  fullBtn.addEventListener('click', async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch {
      toast('当前环境不支持全屏');
    }
  });

  window.addEventListener('keydown', (event) => {
    if (['SELECT', 'INPUT', 'TEXTAREA'].includes(event.target.tagName)) return;
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
    const toggles = { g: 'tgGrid', b: 'tgBlocked', l: 'tgLabels', w: 'tgWalls' };
    if (toggles[key]) {
      const input = $(toggles[key]);
      input.checked = !input.checked;
      input.dispatchEvent(new Event('change'));
      return;
    }
    if (key === ' ') {
      event.preventDefault();
      orbitBtn.click();
    } else if (key === 's') {
      sceneRoot.screenshot();
      toast('已保存 PNG');
    } else if (key === 'f') {
      fullBtn.click();
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
        const client = sceneRoot.pointerClient;
        if (client) {
          tooltip.innerHTML = furnitureTipHtml(item);
          tooltip.classList.add('show');
          placeTooltip(client);
        }
      },
      onSelect: (item) => {
        selectedItem = item;
        updateDeleteButton(item);
        if (!item) tooltip.classList.remove('show');
      },
    });
  } catch (error) {
    setLoading(false);
    showError('浏览器无法初始化 3D 渲染（可能不支持 WebGL）。请换用较新的 Chrome / Edge 打开。');
    console.error(error);
    return;
  }

  bindUi();
  renderLibrary();
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
}

init();
