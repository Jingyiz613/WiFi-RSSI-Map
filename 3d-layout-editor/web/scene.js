import * as THREE from 'three';
import { OrbitControls } from './vendor/OrbitControls.js';

const ACCENT = 0x35d6c4;
const ACCENT_DIM = 0x1b7a72;
const BLUE = 0x5aa9ff;
const DANGER = 0xff5d6c;
const HEAT_STOPS = [
  [-60, [70, 18, 70]],
  [-55, [140, 25, 80]],
  [-50, [210, 45, 70]],
  [-45, [240, 120, 55]],
  [-40, [245, 200, 75]],
  [-35, [130, 225, 120]],
  [-25, [53, 214, 196]],
  [-15, [210, 255, 245]],
];

function heatColor(value) {
  const v = Math.max(-60, Math.min(-15, value));
  for (let i = 0; i < HEAT_STOPS.length - 1; i++) {
    const [low, c1] = HEAT_STOPS[i];
    const [high, c2] = HEAT_STOPS[i + 1];
    if (v >= low && v <= high) {
      const t = (v - low) / (high - low);
      const r = Math.round(c1[0] + (c2[0] - c1[0]) * t);
      const g = Math.round(c1[1] + (c2[1] - c1[1]) * t);
      const b = Math.round(c1[2] + (c2[2] - c1[2]) * t);
      return `rgb(${r},${g},${b})`;
    }
  }
  const last = HEAT_STOPS[HEAT_STOPS.length - 1][1];
  return `rgb(${last[0]},${last[1]},${last[2]})`;
}

const FURNITURE_COLORS = {
  sofa: 0x6d7fa6,
  bed: 0x9db0cf,
  cabinet: 0x8d6f52,
  nightstand: 0x9a7a58,
  side_table: 0x9c7650,
  desk: 0xb08356,
  table: 0x9c7148,
  chair: 0x7f8dab,
  coffee_table: 0x8a6a45,
  tv: 0x252a33,
  tv_stand: 0x6f5540,
  fridge: 0xd7dde7,
  washer: 0xe3e7ee,
  plant: 0x3f8f5a,
  box: 0x8d7f6b,
  rug: 0x8b5f5f,
  aircon: 0xdde3ec,
};

/* ---------- 程序化贴图：不依赖任何外部图片，离线可用 ---------- */

function makeWoodTexture() {
  const size = 512;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#a98a68';
  ctx.fillRect(0, 0, size, size);

  const plank = 64;
  for (let y = 0; y < size; y += plank) {
    const tone = 0.9 + Math.random() * 0.16;
    ctx.fillStyle = `rgb(${Math.round(169 * tone)},${Math.round(138 * tone)},${Math.round(104 * tone)})`;
    ctx.fillRect(0, y, size, plank - 2);
    // 板间缝
    ctx.fillStyle = 'rgba(70,48,30,0.2)';
    ctx.fillRect(0, y + plank - 2, size, 2);
    // 木纹
    for (let i = 0; i < 22; i++) {
      const gy = y + 4 + Math.random() * (plank - 10);
      ctx.strokeStyle = `rgba(92,64,38,${0.04 + Math.random() * 0.08})`;
      ctx.lineWidth = 1 + Math.random();
      ctx.beginPath();
      ctx.moveTo(0, gy);
      ctx.bezierCurveTo(size * 0.3, gy + (Math.random() - 0.5) * 6, size * 0.7, gy + (Math.random() - 0.5) * 6, size, gy);
      ctx.stroke();
    }
    // 竖向拼缝，错缝排列
    const offset = (y / plank) % 2 ? size * 0.5 : 0;
    ctx.fillStyle = 'rgba(70,48,30,0.13)';
    ctx.fillRect((size * 0.5 + offset) % size, y, 1.5, plank - 2);
  }
  const tex = new THREE.CanvasTexture(canvas);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function makeStripeTexture() {
  const size = 64;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, size, size);
  ctx.strokeStyle = 'rgba(18,28,42,0.42)';
  ctx.lineWidth = 4;
  for (let i = -size; i < size * 2; i += 16) {
    ctx.beginPath();
    ctx.moveTo(i, 0);
    ctx.lineTo(i + size, size);
    ctx.stroke();
  }
  ctx.strokeStyle = 'rgba(18,28,42,0.5)';
  ctx.lineWidth = 3;
  ctx.strokeRect(1.5, 1.5, size - 3, size - 3);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function makeGlowTexture() {
  const size = 128;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d');
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  g.addColorStop(0, 'rgba(255,255,255,0.95)');
  g.addColorStop(0.35, 'rgba(120,240,225,0.5)');
  g.addColorStop(1, 'rgba(120,240,225,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

/* ---------- 通用小工具 ---------- */

const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const lerp = (a, b, t) => a + (b - a) * t;
const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);

function standardMat(color, opts = {}) {
  return new THREE.MeshStandardMaterial({
    color,
    roughness: 0.76,
    metalness: 0.04,
    ...opts,
  });
}

function shade(color, delta) {
  return new THREE.Color(color).offsetHSL(0, 0, delta);
}

function addBox(parent, w, h, d, material, x = 0, y = 0, z = 0, radius = 0) {
  const geo = new THREE.BoxGeometry(w, h, d);
  const mesh = new THREE.Mesh(geo, material);
  mesh.position.set(x, y, z);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  parent.add(mesh);
  return mesh;
}

function addCylinder(parent, rTop, rBottom, h, material, x = 0, y = 0, z = 0, segments = 20) {
  const mesh = new THREE.Mesh(new THREE.CylinderGeometry(rTop, rBottom, h, segments), material);
  mesh.position.set(x, y, z);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  parent.add(mesh);
  return mesh;
}

/* ---------- 家具工厂：参数化几何，按类型生成 ---------- */

function buildFurniture(item, roomW, roomD) {
  const group = new THREE.Group();
  const w = Math.max(item.width_m, 0.05);
  const d = Math.max(item.depth_m, 0.05);
  const h = Math.max(item.height_m, 0.02);
  const kind = item.kind;
  const base = FURNITURE_COLORS[kind] ?? FURNITURE_COLORS.box;

  const main = standardMat(base);
  const light = standardMat(shade(base, 0.1));
  const dark = standardMat(shade(base, -0.12));
  const legMat = standardMat(0x4a3a2c, { roughness: 0.6, metalness: 0.15 });
  const fabric = standardMat(shade(base, 0.06), { roughness: 0.95 });

  if (kind === 'sofa') {
    addBox(group, w, 0.22, d, main, 0, 0.17, 0);
    addBox(group, w * 0.9, 0.17, d * 0.64, fabric, 0, 0.365, d * 0.1);
    addBox(group, w, 0.5, d * 0.15, main, 0, 0.5, -d / 2 + d * 0.075);
    addBox(group, w * 0.07, 0.34, d * 0.96, main, -w / 2 + w * 0.035, 0.4, 0);
    addBox(group, w * 0.07, 0.34, d * 0.96, main, w / 2 - w * 0.035, 0.4, 0);
    const cushionCount = w > 1.6 ? 3 : 2;
    for (let i = 0; i < cushionCount; i++) {
      const cw = (w * 0.82) / cushionCount;
      addBox(group, cw * 0.92, 0.1, d * 0.16, light, -w * 0.41 + cw * (i + 0.5), 0.62, -d * 0.34);
    }
    for (const sx of [-1, 1]) for (const sz of [-1, 1]) {
      addBox(group, 0.05, 0.1, 0.05, legMat, sx * (w / 2 - 0.07), 0.05, sz * (d / 2 - 0.07));
    }
  } else if (kind === 'bed') {
    addBox(group, w, 0.16, d, dark, 0, 0.14, 0);
    addBox(group, w * 0.97, 0.24, d * 0.94, light, 0, 0.34, 0);
    addBox(group, w, 0.72, 0.07, main, 0, 0.53, -d / 2 + 0.035);
    addBox(group, w * 0.97, 0.03, d * 0.52, standardMat(0xe8e2d6, { roughness: 0.98 }), 0, 0.475, d * 0.17);
    addBox(group, w * 0.39, 0.09, d * 0.16, standardMat(0xf2eee6, { roughness: 1 }), -w * 0.22, 0.505, -d * 0.31);
    addBox(group, w * 0.39, 0.09, d * 0.16, standardMat(0xf2eee6, { roughness: 1 }), w * 0.22, 0.505, -d * 0.31);
    for (const sx of [-1, 1]) for (const sz of [-1, 1]) {
      addBox(group, 0.06, 0.08, 0.06, legMat, sx * (w / 2 - 0.08), 0.04, sz * (d / 2 - 0.08));
    }
  } else if (kind === 'desk' || kind === 'table' || kind === 'coffee_table' || kind === 'side_table') {
    const topH = Math.max(h, kind === 'coffee_table' ? 0.42 : 0.45);
    const legInset = Math.min(0.09, w * 0.1, d * 0.1);
    addBox(group, w, 0.055, d, main, 0, topH - 0.028, 0);
    if (kind === 'coffee_table' || kind === 'side_table') {
      addBox(group, w * 0.12, topH - 0.06, d * 0.5, dark, 0, (topH - 0.06) / 2, 0);
      addBox(group, w * 0.7, 0.04, d * 0.8, dark, 0, 0.02, 0);
    } else {
      const lh = topH - 0.055;
      for (const sx of [-1, 1]) for (const sz of [-1, 1]) {
        addBox(group, 0.055, lh, 0.055, legMat, sx * (w / 2 - legInset), lh / 2, sz * (d / 2 - legInset));
      }
      if (w > 1.2) addBox(group, w * 0.9, 0.07, 0.02, dark, 0, topH - 0.11, -d / 2 + 0.05);
    }
  } else if (kind === 'chair') {
    const seatH = Math.max(h * 0.48, 0.42);
    addBox(group, w * 0.9, 0.06, d * 0.9, main, 0, seatH, 0);
    addBox(group, w * 0.9, 0.46, 0.05, main, 0, seatH + 0.24, -d / 2 + 0.03);
    for (const sx of [-1, 1]) for (const sz of [-1, 1]) {
      addBox(group, 0.04, seatH, 0.04, legMat, sx * (w / 2 - 0.05), seatH / 2, sz * (d / 2 - 0.05));
    }
  } else if (kind === 'nightstand') {
    addBox(group, w, h, d, main, 0, h / 2, 0);
    addBox(group, w * 0.88, h * 0.3, 0.014, light, 0, h * 0.75, d / 2 + 0.007);
    addBox(group, w * 0.88, h * 0.38, 0.014, light, 0, h * 0.35, d / 2 + 0.007);
    const knobMat = standardMat(0x30363f, { metalness: 0.7, roughness: 0.3 });
    addBox(group, 0.018, 0.03, 0.018, knobMat, 0, h * 0.75, d / 2 + 0.02);
    addBox(group, 0.018, 0.03, 0.018, knobMat, 0, h * 0.35, d / 2 + 0.02);
    for (const sx of [-1, 1]) for (const sz of [-1, 1]) {
      addBox(group, 0.04, 0.07, 0.04, legMat, sx * (w / 2 - 0.05), 0.035, sz * (d / 2 - 0.05));
    }
  } else if (kind === 'cabinet') {
    addBox(group, w, h, d, main, 0, h / 2, 0);
    const doorW = w * 0.47;
    addBox(group, doorW, h * 0.9, 0.015, light, -w * 0.245, h * 0.5, d / 2 + 0.008);
    addBox(group, doorW, h * 0.9, 0.015, light, w * 0.245, h * 0.5, d / 2 + 0.008);
    addBox(group, 0.018, 0.16, 0.018, standardMat(0x30363f, { metalness: 0.7, roughness: 0.3 }), -0.014, h * 0.52, d / 2 + 0.02);
    addBox(group, 0.018, 0.16, 0.018, standardMat(0x30363f, { metalness: 0.7, roughness: 0.3 }), 0.014, h * 0.52, d / 2 + 0.02);
  } else if (kind === 'tv_stand') {
    addBox(group, w, 0.48, d, main, 0, 0.24, 0);
    addBox(group, w * 0.96, 0.03, d * 0.96, light, 0, 0.5, 0);
    addBox(group, 0.012, 0.34, 0.01, dark, 0, 0.26, d / 2 + 0.006);
    addBox(group, 0.012, 0.34, 0.01, dark, 0, 0.26, -d / 2 - 0.006);
    const tvW = Math.min(w * 0.62, 1.2);
    addBox(group, tvW, 0.6, 0.04, standardMat(0x14171d, { roughness: 0.32, metalness: 0.35 }), 0, 0.83, 0);
    addBox(group, 0.05, 0.09, 0.05, dark, 0, 0.55, 0);
  } else if (kind === 'tv') {
    addBox(group, w, h * 0.62, 0.05, standardMat(0x14171d, { roughness: 0.35, metalness: 0.3 }), 0, h * 0.62, 0);
    addBox(group, w * 0.34, 0.03, d * 0.5, dark, 0, 0.015, 0);
    addBox(group, 0.06, h * 0.16, 0.06, dark, 0, 0.09, 0);
  } else if (kind === 'fridge' || kind === 'washer') {
    addBox(group, w, h, d, main, 0, h / 2, 0);
    if (kind === 'fridge') {
      addBox(group, w * 0.98, 0.012, 0.01, standardMat(0x9aa2ad), 0, h * 0.64, d / 2 + 0.006);
      addBox(group, 0.02, 0.34, 0.03, standardMat(0x30363f, { metalness: 0.7, roughness: 0.25 }), w * 0.36, h * 0.42, d / 2 + 0.02);
    } else {
      const door = new THREE.Mesh(
        new THREE.CylinderGeometry(Math.min(w, d) * 0.28, Math.min(w, d) * 0.28, 0.02, 28),
        standardMat(0x2a313c, { roughness: 0.25, metalness: 0.4 })
      );
      door.rotation.x = Math.PI / 2;
      door.position.set(0, h * 0.52, d / 2 + 0.012);
      group.add(door);
    }
  } else if (kind === 'plant') {
    addCylinder(group, w * 0.3, w * 0.24, 0.26, standardMat(0xa9603f, { roughness: 0.9 }), 0, 0.13, 0);
    const leafMat = standardMat(0x3d8a53, { roughness: 0.85 });
    const leafMat2 = standardMat(0x4f9f63, { roughness: 0.85 });
    addCylinder(group, 0.035, 0.045, 0.4, standardMat(0x5a4632), 0, 0.42, 0, 8);
    const blobs = [
      [0, 0.72, 0, 0.34], [0.16, 0.62, 0.1, 0.22], [-0.15, 0.66, -0.08, 0.2],
    ];
    blobs.forEach(([x, y, z, r], i) => {
      const blob = new THREE.Mesh(new THREE.IcosahedronGeometry(r, 1), i % 2 ? leafMat2 : leafMat);
      blob.position.set(x, y, z);
      blob.castShadow = true;
      group.add(blob);
    });
  } else if (kind === 'rug') {
    addBox(group, w, 0.02, d, main, 0, 0.01, 0);
  } else {
    addBox(group, w, h, d, main, 0, h / 2, 0);
    addBox(group, w * 0.96, 0.03, d * 0.96, light, 0, h + 0.015, 0);
  }

  group.position.set(item.cx - roomW / 2, 0, item.cz - roomD / 2);
  group.userData.furniture = item;
  group.traverse((obj) => {
    if (obj.isMesh) obj.userData.furnitureIndex = item.index;
  });
  return group;
}

/* ---------- 场景根 ---------- */

export class SceneRoot {
  constructor(container, labelLayer, options = {}) {
    this.container = container;
    this.labelLayer = labelLayer;
    this.onHover = options.onHover || (() => {});
    this.onCommit = options.onCommit || null;
    this.onSelect = options.onSelect || null;
    this.onDrag = options.onDrag || null;
    this.onDragStart = options.onDragStart || null;
    this.data = null;
    this.room = { w: 8, d: 7, cw: 1, cd: 1, rows: 7, cols: 9 };
    this.tags = [];
    this.pickables = [];
    this.hoverIndex = -1;
    this.pointer = new THREE.Vector2(-2, -2);
    this.pointerDirty = false;
    this.anim = null;
    this.autoRotate = false;

    this._initRenderer();
    this._initScene();
    this._bind();
    this.resize();
    this._tick = this._tick.bind(this);
    this._raf = requestAnimationFrame(this._tick);
  }

  _initRenderer() {
    this.renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: true,
      preserveDrawingBuffer: true,
      powerPreference: 'high-performance',
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;
    this.renderer.domElement.className = 'scene-canvas';
    this.container.appendChild(this.renderer.domElement);
  }

  _initScene() {
    this.scene = new THREE.Scene();
    this.camera = new THREE.OrthographicCamera(-8, 8, 6, -6, 0.1, 200);
    this.camera.position.set(10, 12, 14);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.075;
    this.controls.screenSpacePanning = true;
    this.controls.minPolarAngle = 0.001;
    this.controls.maxPolarAngle = Math.PI * 0.492;
    this.controls.minZoom = 0.55;
    this.controls.maxZoom = 3.4;
    this.controls.autoRotateSpeed = 0.7;
    this.controls.target.set(0, 0.5, 0);
    this.controls.enablePan = true;

    this.scene.add(new THREE.HemisphereLight(0xdfeaff, 0x2a2118, 1.65));
    this.scene.add(new THREE.AmbientLight(0xffffff, 0.35));

    const key = new THREE.DirectionalLight(0xfff2df, 2.35);
    key.position.set(8, 14, 9);
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    key.shadow.bias = -0.0004;
    key.shadow.normalBias = 0.02;
    this.scene.add(key);
    this.keyLight = key;

    const rim = new THREE.DirectionalLight(0x86b8ff, 0.75);
    rim.position.set(-9, 7, -7);
    this.scene.add(rim);

    this.root = new THREE.Group();
    this.scene.add(this.root);

    this.wallGroup = new THREE.Group();
    this.floorGroup = new THREE.Group();
    this.gridGroup = new THREE.Group();
    this.blockedGroup = new THREE.Group();
    this.stopGroup = new THREE.Group();
    this.routeGroup = new THREE.Group();
    this.furnitureGroup = new THREE.Group();
    this.apGroup = new THREE.Group();
    this.startGroup = new THREE.Group();
    this.heatGroup = new THREE.Group();
    this.aiGroup = new THREE.Group();
    this.aiPointGroup = new THREE.Group();
    this.aiGroup.add(this.aiPointGroup);
    this.root.add(
      this.wallGroup, this.floorGroup, this.gridGroup, this.blockedGroup,
      this.stopGroup, this.routeGroup, this.furnitureGroup, this.apGroup, this.startGroup,
      this.heatGroup, this.aiGroup
    );

    // 悬停高亮（常驻，不随布局重建）
    this.hoverBox = new THREE.Box3Helper(new THREE.Box3(), 0x35d6c4);
    this.hoverBox.material.depthTest = false;
    this.hoverBox.material.transparent = true;
    this.hoverBox.visible = false;
    this.scene.add(this.hoverBox);

    const cellMat = new THREE.MeshBasicMaterial({
      color: 0x35d6c4, transparent: true, opacity: 0.16, depthWrite: false,
    });
    this.cellPlane = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), cellMat);
    this.cellPlane.rotation.x = -Math.PI / 2;
    this.cellPlane.position.y = 0.014;
    this.cellPlane.visible = false;
    this.cellEdges = new THREE.LineSegments(
      new THREE.EdgesGeometry(new THREE.PlaneGeometry(1, 1)),
      new THREE.LineBasicMaterial({ color: 0x7cffe9, transparent: true, opacity: 0.9, depthTest: false })
    );
    this.cellEdges.rotation.x = -Math.PI / 2;
    this.cellEdges.position.y = 0.015;
    this.cellEdges.visible = false;
    this.scene.add(this.cellPlane, this.cellEdges);

    this.raycaster = new THREE.Raycaster();
    this.clock = new THREE.Clock();
    this.groundPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
    this._pointerTmp = new THREE.Vector2();
    this.snapStep = 0.05;
  }

  _bind() {
    this._onResize = () => this.resize();
    window.addEventListener('resize', this._onResize);

    this._onMove = (event) => {
      const rect = this.renderer.domElement.getBoundingClientRect();
      this.pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      this.pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      this.pointerClient = { x: event.clientX, y: event.clientY };
      this.pointerDirty = true;
    };
    this._onLeave = () => {
      this.pointer.set(-2, -2);
      this.pointerDirty = true;
      this.pointerClient = null;
    };
    this._onDown = (event) => this._handleDown(event);
    this._onDragMove = (event) => this._handleDragMove(event);
    this._onUp = (event) => this._handleUp(event);

    const canvas = this.renderer.domElement;
    canvas.style.cursor = 'grab';
    canvas.addEventListener('pointermove', this._onMove);
    canvas.addEventListener('pointerleave', this._onLeave);
    canvas.addEventListener('pointerdown', this._onDown, true);
    canvas.addEventListener('pointermove', this._onDragMove);
    canvas.addEventListener('pointerup', this._onUp);
    canvas.addEventListener('pointercancel', this._onUp);
  }

  /* ---------- 拖动编辑 ---------- */

  _handleDown(event) {
    if (event.button !== 0 || !this.data) return;

    if (this._pickAp(event)) {
      event.preventDefault();
      event.stopImmediatePropagation();
      this.controls.enabled = false;
      this._select(-1);
      this.dragging = { type: 'ap', before: this.snapshot(), moved: false };
      this.renderer.domElement.style.cursor = 'grabbing';
      try {
        this.renderer.domElement.setPointerCapture(event.pointerId);
      } catch {
        /* ignore */
      }
      return;
    }

    const index = this._pickFurnitureIndex(event);
    if (index < 0) {
      this._select(-1);
      return;
    }
    const item = this.data.furniture.find((entry) => entry.index === index);
    const point = this._groundPoint(event);
    if (!item || !point) return;

    event.preventDefault();
    event.stopImmediatePropagation();
    this.controls.enabled = false;
    this.dragging = {
      type: 'furniture',
      index,
      offsetX: item.cx - (point.x + this.room.w / 2),
      offsetZ: item.cz - (point.z + this.room.d / 2),
      before: this.snapshot(),
      moved: false,
    };
    this._select(index);
    this.renderer.domElement.style.cursor = 'grabbing';
    try {
      this.renderer.domElement.setPointerCapture(event.pointerId);
    } catch {
      /* ignore */
    }
    this.onDragStart?.(item);
  }

  _handleDragMove(event) {
    if (!this.dragging) return;

    if (this.dragging.type === 'ap') {
      const point = this._groundPoint(event);
      if (!point) return;
      const roomX = point.x + this.room.w / 2;
      const roomZ = point.z + this.room.d / 2;
      const col = clamp(Math.round(roomX / this.room.cw - 0.5) + 1, 1, this.room.cols);
      const row = clamp(Math.round(roomZ / this.room.cd - 0.5) + 1, 1, this.room.rows);
      const cx = (col - 0.5) * this.room.cw;
      const cz = (row - 0.5) * this.room.cd;
      if (this.apRoot) this.apRoot.position.set(cx - this.room.w / 2, 0, cz - this.room.d / 2);
      this.data.ap = { label: `P${row}${col}`, row, column: col, x_m: cx, y_m: cz };
      if (this.apTag) this.apTag.pos.set(cx - this.room.w / 2, 1.2, cz - this.room.d / 2);
      const cell = this.data.cells.find((c) => c.row === row && c.column === col);
      if (cell) this._setHover(-1, cell);
      this.dragging.moved = true;
      this.onDrag?.(null);
      return;
    }

    const item = this.data.furniture.find((entry) => entry.index === this.dragging.index);
    if (!item) return;
    const point = this._groundPoint(event);
    if (!point) return;

    let cx = point.x + this.room.w / 2 + this.dragging.offsetX;
    let cz = point.z + this.room.d / 2 + this.dragging.offsetZ;
    const hw = (item.x2 - item.x1) / 2;
    const hd = (item.y2 - item.y1) / 2;
    const step = event.altKey ? 0 : this.snapStep;
    if (step > 0) {
      cx = Math.round(cx / step) * step;
      cz = Math.round(cz / step) * step;
    }
    cx = clamp(cx, hw + 0.01, this.room.w - hw - 0.01);
    cz = clamp(cz, hd + 0.01, this.room.d - hd - 0.01);

    item.cx = cx;
    item.cz = cz;
    item.x1 = cx - hw;
    item.x2 = cx + hw;
    item.y1 = cz - hd;
    item.y2 = cz + hd;

    const group = this.furnitureGroup.children.find(
      (entry) => entry.userData.furniture?.index === item.index
    );
    if (group) group.position.set(cx - this.room.w / 2, 0, cz - this.room.d / 2);
    const tag = this.tagByIndex.get(item.index);
    if (tag) tag.pos.set(cx - this.room.w / 2, tag.baseY, cz - this.room.d / 2);

    this._showBox(item.index);
    this.dragging.moved = true;
    this.onDrag?.(item);
  }

  _handleUp(event) {
    if (!this.dragging) return;
    const drag = this.dragging;
    this.dragging = null;
    this.controls.enabled = true;
    this.renderer.domElement.style.cursor = 'grab';
    try {
      this.renderer.domElement.releasePointerCapture(event.pointerId);
    } catch {
      /* ignore */
    }
    if (drag.moved) {
      this.onCommit?.({
        type: drag.type || 'furniture',
        index: drag.index,
        before: drag.before,
        after: this.snapshot(),
      });
    }
  }

  _pickAp(event) {
    if (!this.apRoot) return false;
    const rect = this.renderer.domElement.getBoundingClientRect();
    this._pointerTmp.set(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1
    );
    this.raycaster.setFromCamera(this._pointerTmp, this.camera);
    return this.raycaster.intersectObject(this.apRoot, true).length > 0;
  }

  _pickFurnitureIndex(event) {
    if (!this.pickables.length) return -1;
    const rect = this.renderer.domElement.getBoundingClientRect();
    this._pointerTmp.set(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1
    );
    this.raycaster.setFromCamera(this._pointerTmp, this.camera);
    const hits = this.raycaster.intersectObjects(this.pickables, true);
    for (const hit of hits) {
      let obj = hit.object;
      while (obj) {
        if (obj.userData.furnitureIndex !== undefined) return obj.userData.furnitureIndex;
        obj = obj.parent;
      }
    }
    return -1;
  }

  _groundPoint(event) {
    const rect = this.renderer.domElement.getBoundingClientRect();
    this._pointerTmp.set(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1
    );
    this.raycaster.setFromCamera(this._pointerTmp, this.camera);
    const point = new THREE.Vector3();
    return this.raycaster.ray.intersectPlane(this.groundPlane, point) ? point : null;
  }

  _select(index) {
    this.selectedIndex = index;
    if (index >= 0) this._showBox(index);
    else this.hoverBox.visible = false;
    const item = index >= 0 ? this.data.furniture.find((f) => f.index === index) : null;
    this.onSelect?.(item || null);
  }

  _showBox(index) {
    const group = this.furnitureGroup.children.find(
      (entry) => entry.userData.furniture?.index === index
    );
    if (!group) return;
    group.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(group);
    box.expandByScalar(0.025);
    this.hoverBox.box.copy(box);
    this.hoverBox.visible = true;
  }

  /* ---------- 编辑状态 ---------- */

  snapshot() {
    return {
      ap: { ...this.data.ap },
      furniture: this.data.furniture.map((item) => ({ ...item })),
      room: { w: this.room.w, d: this.room.d },
    };
  }

  restore(snapshot) {
    const room = snapshot.room;
    if (room && (Math.abs(room.w - this.room.w) > 1e-6 || Math.abs(room.d - this.room.d) > 1e-6)) {
      this._rebuildRoom(room.w, room.d);
    }
    if (snapshot.ap) this.data.ap = { ...snapshot.ap };
    this.data.furniture = (snapshot.furniture || []).map((item) => ({ ...item }));
    this.data.room = {
      ...this.data.room,
      width_m: this.room.w,
      height_m: this.room.d,
      cell_width_m: this.room.cw,
      cell_height_m: this.room.cd,
    };
    this.rebuildObjects();
  }

  /** 只按当前 room 尺寸重建地板/墙体/网格与热力图层，不动家具数据。 */
  _rebuildRoom(width, height) {
    this.room.w = width;
    this.room.d = height;
    this.room.cw = width / this.room.cols;
    this.room.cd = height / this.room.rows;
    this._clearGroup(this.floorGroup);
    this._clearGroup(this.wallGroup);
    this._clearGroup(this.gridGroup);
    this._clearGroup(this.heatGroup);
    this._clearGroup(this.aiGroup);
    this.floor = null;
    this.heatMesh = null;
    this.heatTexture = null;
    this.heatCanvas = null;
    this.heatCtx = null;
    this.heatBlur = null;
    this.heatData = null;
    this.aiMesh = null;
    this.aiTexture = null;
    this.aiCanvas = null;
    this.aiCtx = null;
    this.aiBlur = null;
    this.aiData = null;
    this._buildFloor(width, height);
    this._buildWalls(width, height);
    this._buildGrid(width, height);
    this.resize();
  }

  /** 改房间长宽：格数不变，家具保持原尺寸并贴边回收。 */
  setRoomSize(width, height) {
    if (!this.data) return false;
    const w = clamp(Number(width) || this.room.w, 2, 30);
    const d = clamp(Number(height) || this.room.d, 2, 30);
    const oldW = this.room.w;
    const oldD = this.room.d;
    this._rebuildRoom(w, d);
    this.data.room = {
      ...this.data.room,
      width_m: w,
      height_m: d,
      cell_width_m: this.room.cw,
      cell_height_m: this.room.cd,
    };
    const sx = oldW > 1e-6 ? w / oldW : 1;
    const sy = oldD > 1e-6 ? d / oldD : 1;
    this.data.furniture = this.data.furniture.map((item) => {
      const iw = Math.min(item.width_m, w - 0.1);
      const id = Math.min(item.depth_m, d - 0.1);
      const cx = clamp(item.cx * sx, iw / 2, w - iw / 2);
      const cz = clamp(item.cz * sy, id / 2, d - id / 2);
      return {
        ...item,
        width_m: iw,
        depth_m: id,
        cx,
        cz,
        x1: cx - iw / 2,
        x2: cx + iw / 2,
        y1: cz - id / 2,
        y2: cz + id / 2,
        area_m2: iw * id,
      };
    });
    this.data.ap = {
      ...this.data.ap,
      x_m: (this.data.ap.column - 0.5) * this.room.cw,
      y_m: (this.data.ap.row - 0.5) * this.room.cd,
    };
    if (this.data.start) {
      this.data.start = {
        ...this.data.start,
        x_m: (this.data.start.column - 0.5) * this.room.cw,
        y_m: (this.data.start.row - 0.5) * this.room.cd,
      };
    }
    this.data.cells = [];
    this.rebuildObjects();
    this.setCells([]);
    return true;
  }

  setFurnitureMaterial(index, material) {
    const item = this.data.furniture.find((f) => f.index === index);
    if (!item) return false;
    item.material = material || '';
    return true;
  }

  deleteFurniture(index) {
    this.data.furniture = this.data.furniture.filter((item) => item.index !== index);
    this.rebuildObjects();
  }

  addFurniture(spec) {
    const before = this.snapshot();
    const width = Math.min(spec.w, this.room.w - 0.06);
    const depth = Math.min(spec.d, this.room.d - 0.06);
    const spot = this._findFreeSpot(width, depth) || {
      cx: this.room.w / 2,
      cz: this.room.d / 2,
    };
    const item = {
      index: this.data.furniture.length,
      name: spec.name,
      kind: spec.kind,
      material: spec.material || '',
      height_m: spec.h,
      x1: spot.cx - width / 2,
      x2: spot.cx + width / 2,
      y1: spot.cz - depth / 2,
      y2: spot.cz + depth / 2,
      cx: spot.cx,
      cz: spot.cz,
      width_m: width,
      depth_m: depth,
      area_m2: width * depth,
      blocked_cells: 0,
    };
    this.data.furniture.push(item);
    this.rebuildObjects();
    this._select(item.index);
    return { before, item };
  }

  _findFreeSpot(width, depth) {
    const step = 0.15;
    const cxMin = width / 2 + 0.03;
    const cxMax = this.room.w - width / 2 - 0.03;
    const czMin = depth / 2 + 0.03;
    const czMax = this.room.d - depth / 2 - 0.03;
    if (cxMax < cxMin || czMax < czMin) return null;

    const candidates = [];
    for (let cx = cxMin; cx <= cxMax + 1e-6; cx += step) {
      for (let cz = czMin; cz <= czMax + 1e-6; cz += step) {
        candidates.push({ cx, cz });
      }
    }
    const centerX = this.room.w / 2;
    const centerZ = this.room.d / 2;
    candidates.sort(
      (a, b) =>
        (a.cx - centerX) ** 2 + (a.cz - centerZ) ** 2 -
        ((b.cx - centerX) ** 2 + (b.cz - centerZ) ** 2)
    );

    const ap = this.data.ap;
    for (const spot of candidates) {
      if (
        Math.abs(spot.cx - ap.x_m) < width / 2 + this.room.cw * 0.5 &&
        Math.abs(spot.cz - ap.y_m) < depth / 2 + this.room.cd * 0.5
      ) {
        continue;
      }
      const x1 = spot.cx - width / 2;
      const x2 = spot.cx + width / 2;
      const y1 = spot.cz - depth / 2;
      const y2 = spot.cz + depth / 2;
      const overlaps = this.data.furniture.some(
        (f) => x1 < f.x2 - 1e-6 && f.x1 < x2 - 1e-6 && y1 < f.y2 - 1e-6 && f.y1 < y2 - 1e-6
      );
      if (!overlaps) return spot;
    }
    return null;
  }

  rebuildObjects() {
    this._clearGroup(this.furnitureGroup);
    this._clearGroup(this.apGroup);
    this.pickables = [];
    this.tags.forEach((tag) => tag.el.remove());
    this.tags = [];
    this.tagByIndex.clear();
    this.apRoot = null;
    this.apTag = null;
    this.apRings = [];
    this.apBulb = null;
    this.selectedIndex = -1;
    this.dragging = null;
    this.hoverBox.visible = false;
    this.cellPlane.visible = false;
    this.cellEdges.visible = false;
    this.data.furniture.forEach((item, i) => {
      item.index = i;
    });
    this._buildFurniture(this.data.furniture);
    this._buildAp(this.data.ap);
    this._buildLabels();
    this.onSelect?.(null);
  }

  setHeatmap(data) {
    if (!data || !data.points || !this.room.cols) return;
    this.heatData = data;
    const cols = this.room.cols;
    const rows = this.room.rows;
    const cell = 64;

    if (!this.heatCanvas) {
      this.heatCanvas = document.createElement('canvas');
      this.heatCtx = this.heatCanvas.getContext('2d');
      this.heatTexture = new THREE.CanvasTexture(this.heatCanvas);
      this.heatTexture.colorSpace = THREE.SRGBColorSpace;
      this.heatTexture.minFilter = THREE.LinearFilter;
      this.heatTexture.magFilter = THREE.LinearFilter;
      const geo = new THREE.PlaneGeometry(this.room.w, this.room.d);
      const mat = new THREE.MeshBasicMaterial({
        map: this.heatTexture,
        transparent: true,
        opacity: 0.92,
        depthWrite: false,
        side: THREE.DoubleSide,
      });
      this.heatMesh = new THREE.Mesh(geo, mat);
      this.heatMesh.rotation.x = -Math.PI / 2;
      this.heatMesh.position.y = 0.004;
      this.heatGroup.add(this.heatMesh);
    }

    const canvas = this.heatCanvas;
    canvas.width = cols * cell;
    canvas.height = rows * cell;
    const ctx = this.heatCtx;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    for (const point of data.points) {
      ctx.fillStyle = heatColor(point.rssi_dbm);
      ctx.fillRect((point.column - 1) * cell, (point.row - 1) * cell, cell, cell);
    }

    if (!this.heatBlur) this.heatBlur = document.createElement('canvas');
    const blur = this.heatBlur;
    blur.width = canvas.width;
    blur.height = canvas.height;
    const bctx = blur.getContext('2d');
    bctx.clearRect(0, 0, blur.width, blur.height);
    bctx.filter = 'blur(20px)';
    bctx.drawImage(canvas, 0, 0);
    bctx.filter = 'none';
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(blur, 0, 0);
    this.heatTexture.needsUpdate = true;
  }

  setAiHeatmap(data) {
    if (!data || !Array.isArray(data.classes) || !this.room.cols) return;
    this.aiData = data;
    const ny = data.grid?.ny || data.classes.length;
    const nx = data.grid?.nx || data.classes[0].length;
    const cell = 12;

    if (!this.aiCanvas) {
      this.aiCanvas = document.createElement('canvas');
      this.aiCtx = this.aiCanvas.getContext('2d');
      this.aiTexture = new THREE.CanvasTexture(this.aiCanvas);
      this.aiTexture.colorSpace = THREE.SRGBColorSpace;
      this.aiTexture.minFilter = THREE.LinearFilter;
      this.aiTexture.magFilter = THREE.LinearFilter;
      const geo = new THREE.PlaneGeometry(this.room.w, this.room.d);
      const mat = new THREE.MeshBasicMaterial({
        map: this.aiTexture,
        transparent: true,
        opacity: 0.9,
        depthWrite: false,
        side: THREE.DoubleSide,
      });
      this.aiMesh = new THREE.Mesh(geo, mat);
      this.aiMesh.rotation.x = -Math.PI / 2;
      this.aiMesh.position.y = 0.006;
      this.aiGroup.add(this.aiMesh);
    }

    const canvas = this.aiCanvas;
    canvas.width = nx * cell;
    canvas.height = ny * cell;
    const ctx = this.aiCtx;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const colors = ['#3b1f6e', '#2f6fdb', '#25b3a4', '#f2c14e', '#ef6c4d'];
    for (let r = 0; r < ny; r += 1) {
      for (let c = 0; c < nx; c += 1) {
        const cls = data.classes[r]?.[c] ?? 0;
        ctx.fillStyle = colors[cls] || colors[0];
        ctx.fillRect(c * cell, r * cell, cell, cell);
      }
    }

    if (!this.aiBlur) this.aiBlur = document.createElement('canvas');
    const blur = this.aiBlur;
    blur.width = canvas.width;
    blur.height = canvas.height;
    const bctx = blur.getContext('2d');
    bctx.clearRect(0, 0, blur.width, blur.height);
    bctx.filter = 'blur(6px)';
    bctx.drawImage(canvas, 0, 0);
    bctx.filter = 'none';
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(blur, 0, 0);
    this.aiTexture.needsUpdate = true;

    this._clearGroup(this.aiPointGroup);
    for (const point of data.points || []) {
      const dot = new THREE.Mesh(
        new THREE.SphereGeometry(0.055, 12, 12),
        new THREE.MeshBasicMaterial({ color: 0xffffff, depthTest: false })
      );
      dot.position.set(point.x_m - this.room.w / 2, 0.035, point.y_m - this.room.d / 2);
      dot.renderOrder = 6;
      this.aiPointGroup.add(dot);
    }
    this.aiGroup.visible = true;
    this.aiPointGroup.visible = true;
  }

  clearAiHeatmap() {
    this.aiData = null;
    if (this.aiGroup) this.aiGroup.visible = false;
    if (this.aiPointGroup) this.aiPointGroup.visible = false;
  }

  setCells(cells) {
    this.data.cells = cells;
    this._clearGroup(this.blockedGroup);
    this._buildBlocked(cells);
  }

  applyState(state) {
    if (state.cells) this.setCells(state.cells);
    if (state.stats) this.data.stats = state.stats;
    if (state.problems) this.data.problems = state.problems;
    if (state.furniture) {
      state.furniture.forEach((meta) => {
        const item = this.data.furniture.find((f) => f.index === meta.index);
        if (!item) return;
        item.kind = meta.kind;
        item.height_m = meta.height_m;
        item.blocked_cells = meta.blocked_cells;
        if (meta.material !== undefined) item.material = meta.material;
        if (meta.material_label) item.material_label = meta.material_label;
      });
    }
  }

  getLayoutPayload() {
    const round = (value) => Math.round(value * 1000) / 1000;
    return {
      version: 2,
      room: {
        rows: this.room.rows,
        columns: this.room.cols,
        width_m: this.room.w,
        height_m: this.room.d,
      },
      clearance_m: this.data.clearance_m ?? 0.1,
      ap: this.data.ap.label,
      start: this.data.start.label,
      furniture: this.data.furniture.map((item) => ({
        name: item.name,
        material: item.material || '',
        x1: round(item.x1),
        y1: round(item.y1),
        x2: round(item.x2),
        y2: round(item.y2),
      })),
      manual_blocked: this.data.manual_blocked || [],
    };
  }

  /* ---------- 布局构建 ---------- */

  setLayout(data) {
    this.data = data;
    this._clearDynamic();
    this.floor = null;
    this.routeCurve = null;
    this.routeDots = [];
    this.apRings = [];
    this.apBulb = null;
    this.apRoot = null;
    this.apTag = null;
    this.heatMesh = null;
    this.heatTexture = null;
    this.heatCanvas = null;
    this.heatCtx = null;
    this.heatBlur = null;
    this.heatData = null;
    this.startRing = null;
    this.car = null;
    this.tags.forEach((tag) => tag.el.remove());
    this.tags = [];
    this.pickables = [];
    this.hoverIndex = -1;
    this.selectedIndex = -1;
    this.dragging = null;
    this.tagByIndex = new Map();
    this.hoverBox.visible = false;
    this.cellPlane.visible = false;
    this.cellEdges.visible = false;

    const room = data.room;
    const w = (this.room.w = room.width_m);
    const d = (this.room.d = room.height_m);
    this.room.cw = room.cell_width_m;
    this.room.cd = room.cell_height_m;
    this.room.rows = room.rows;
    this.room.cols = room.columns;

    this._buildFloor(w, d);
    this._buildWalls(w, d);
    this._buildGrid(w, d);
    this._buildBlocked(data.cells);
    this._buildFurniture(data.furniture);
    this._buildAp(data.ap);
    this._buildLabels();

    this.controls.target.set(0, 0.55, 0);
    this.setView('iso', 0);
    this.resize();
  }

  _clearGroup(group) {
    const dispose = (obj) => {
      obj.traverse((o) => {
        if (o.geometry) o.geometry.dispose();
        if (o.material) {
          const list = Array.isArray(o.material) ? o.material : [o.material];
          list.forEach((m) => {
            if (m.map && !m.map.userData.shared) m.map.dispose();
            m.dispose();
          });
        }
      });
    };
    group.children.slice().forEach((child) => {
      dispose(child);
      group.remove(child);
    });
  }

  _clearDynamic() {
    for (const group of [
      this.floorGroup, this.wallGroup, this.gridGroup, this.blockedGroup,
      this.stopGroup, this.routeGroup, this.furnitureGroup, this.apGroup, this.startGroup,
      this.heatGroup, this.aiGroup,
    ]) {
      this._clearGroup(group);
    }
  }

  _buildFloor(w, d) {
    const slab = new THREE.Mesh(
      new THREE.BoxGeometry(w + 0.48, 0.16, d + 0.48),
      standardMat(0x0f1a2a, { roughness: 0.92 })
    );
    slab.position.y = -0.09;
    slab.receiveShadow = true;
    this.floorGroup.add(slab);

    const wood = makeWoodTexture();
    wood.anisotropy = this.renderer.capabilities.getMaxAnisotropy();
    wood.repeat.set(Math.max(1, w / 2.1), Math.max(1, d / 2.1));
    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(w, d),
      standardMat(0xffffff, { map: wood, roughness: 0.86, metalness: 0.02 })
    );
    floor.rotation.x = -Math.PI / 2;
    floor.receiveShadow = true;
    floor.name = 'floor';
    floor.userData.isFloor = true;
    this.floor = floor;
    this.floorGroup.add(floor);

    // 房间外框亮边，游戏沙盘感
    const edgeMat = new THREE.LineBasicMaterial({ color: ACCENT, transparent: true, opacity: 0.5 });
    const pts = [
      [-w / 2, 0.005, -d / 2], [w / 2, 0.005, -d / 2],
      [w / 2, 0.005, d / 2], [-w / 2, 0.005, d / 2],
    ];
    const edge = new THREE.LineLoop(
      new THREE.BufferGeometry().setFromPoints(pts.map((p) => new THREE.Vector3(...p))),
      edgeMat
    );
    this.floorGroup.add(edge);
  }

  _buildWalls(w, d) {
    const H = 2.75;
    const wallMat = new THREE.MeshStandardMaterial({
      color: 0x233650, roughness: 0.94, metalness: 0.02,
      transparent: true, opacity: 0.5, side: THREE.DoubleSide,
    });
    const capMat = new THREE.MeshBasicMaterial({ color: ACCENT, transparent: true, opacity: 0.3 });
    const trimMat = new THREE.MeshStandardMaterial({ color: 0x2f4766, roughness: 0.8 });

    const back = new THREE.Mesh(new THREE.BoxGeometry(w + 0.1, H, 0.09), wallMat);
    back.position.set(0, H / 2, -d / 2 - 0.045);
    back.receiveShadow = true;
    this.wallGroup.add(back);

    const left = new THREE.Mesh(new THREE.BoxGeometry(0.09, H, d + 0.1), wallMat);
    left.position.set(-w / 2 - 0.045, H / 2, 0);
    left.receiveShadow = true;
    this.wallGroup.add(left);

    const capBack = new THREE.Mesh(new THREE.BoxGeometry(w + 0.1, 0.035, 0.11), capMat);
    capBack.position.set(0, H, -d / 2 - 0.045);
    this.wallGroup.add(capBack);
    const capLeft = new THREE.Mesh(new THREE.BoxGeometry(0.11, 0.035, d + 0.1), capMat);
    capLeft.position.set(-w / 2 - 0.045, H, 0);
    this.wallGroup.add(capLeft);

    const trimBack = new THREE.Mesh(new THREE.BoxGeometry(w + 0.1, 0.11, 0.05), trimMat);
    trimBack.position.set(0, 0.055, -d / 2 - 0.02);
    this.wallGroup.add(trimBack);
    const trimLeft = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.11, d + 0.1), trimMat);
    trimLeft.position.set(-w / 2 - 0.02, 0.055, 0);
    this.wallGroup.add(trimLeft);
  }

  _buildGrid(w, d) {
    const pts = [];
    for (let c = 0; c <= this.room.cols; c++) {
      const x = -w / 2 + c * this.room.cw;
      pts.push(new THREE.Vector3(x, 0.007, -d / 2), new THREE.Vector3(x, 0.007, d / 2));
    }
    for (let r = 0; r <= this.room.rows; r++) {
      const z = -d / 2 + r * this.room.cd;
      pts.push(new THREE.Vector3(-w / 2, 0.007, z), new THREE.Vector3(w / 2, 0.007, z));
    }
    const geo = new THREE.BufferGeometry().setFromPoints(pts);
    const mat = new THREE.LineBasicMaterial({ color: 0x86b6e8, transparent: true, opacity: 0.26 });
    this.gridGroup.add(new THREE.LineSegments(geo, mat));
  }

  _buildBlocked(cells) {
    const blocked = cells.filter((c) => c.blocked);
    if (!blocked.length) return;
    const tex = makeStripeTexture();
    const mat = new THREE.MeshBasicMaterial({
      map: tex, transparent: true, opacity: 0.78, depthWrite: false, side: THREE.DoubleSide,
    });
    const geo = new THREE.PlaneGeometry(1, 1);
    const inst = new THREE.InstancedMesh(geo, mat, blocked.length);
    const dummy = new THREE.Object3D();
    blocked.forEach((cell, i) => {
      dummy.position.set(cell.x_m - this.room.w / 2, 0.011, cell.y_m - this.room.d / 2);
      dummy.rotation.set(-Math.PI / 2, 0, 0);
      dummy.scale.set(this.room.cw * 0.92, this.room.cd * 0.92, 1);
      dummy.updateMatrix();
      inst.setMatrixAt(i, dummy.matrix);
    });
    inst.instanceMatrix.needsUpdate = true;
    this.blockedGroup.add(inst);
  }

  _buildStops(stops) {
    if (!stops.length) return;
    const mat = new THREE.MeshBasicMaterial({
      color: ACCENT, transparent: true, opacity: 0.85, depthWrite: false, side: THREE.DoubleSide,
    });
    const geo = new THREE.CircleGeometry(1, 16);
    const inst = new THREE.InstancedMesh(geo, mat, stops.length);
    const dummy = new THREE.Object3D();
    stops.forEach((stop, i) => {
      dummy.position.set(stop.x_m - this.room.w / 2, 0.021, stop.y_m - this.room.d / 2);
      dummy.rotation.set(-Math.PI / 2, 0, 0);
      dummy.scale.set(0.042, 0.042, 1);
      dummy.updateMatrix();
      inst.setMatrixAt(i, dummy.matrix);
    });
    inst.instanceMatrix.needsUpdate = true;
    this.stopGroup.add(inst);
  }

  _buildRoute(route) {
    const pts = (route.points || []).map(
      ([x, y]) => new THREE.Vector3(x - this.room.w / 2, 0.036, y - this.room.d / 2)
    );
    if (pts.length < 2) return;
    const curve = new THREE.CatmullRomCurve3(pts, false, 'centripetal', 0.5);
    this.routeCurve = curve;

    const glow = new THREE.Mesh(
      new THREE.TubeGeometry(curve, Math.max(80, pts.length * 5), 0.045, 8, false),
      new THREE.MeshBasicMaterial({
        color: ACCENT, transparent: true, opacity: 0.13, depthWrite: false,
      })
    );
    this.routeGroup.add(glow);

    const tube = new THREE.Mesh(
      new THREE.TubeGeometry(curve, Math.max(80, pts.length * 5), 0.017, 8, false),
      new THREE.MeshStandardMaterial({
        color: 0x4ef0dc, emissive: ACCENT_DIM, emissiveIntensity: 1.5,
        roughness: 0.35, metalness: 0.1,
      })
    );
    tube.castShadow = false;
    this.routeGroup.add(tube);

    this.routeDots = [];
    const dotMat = new THREE.MeshBasicMaterial({ color: 0xd6fff9 });
    for (let i = 0; i < 3; i++) {
      const dot = new THREE.Mesh(new THREE.SphereGeometry(0.052, 12, 12), dotMat);
      this.routeGroup.add(dot);
      this.routeDots.push(dot);
    }
  }

  _buildFurniture(items) {
    items.forEach((item) => {
      item.cx = (item.x1 + item.x2) / 2;
      item.cz = (item.y1 + item.y2) / 2;
      const group = buildFurniture(item, this.room.w, this.room.d);
      this.furnitureGroup.add(group);
      this.pickables.push(group);
    });
  }

  _buildAp(ap) {
    const x = ap.x_m - this.room.w / 2;
    const z = ap.y_m - this.room.d / 2;
    const root = new THREE.Group();
    root.position.set(x, 0, z);
    root.userData.ap = true;
    this.apGroup.add(root);
    this.apRoot = root;
    this.apRings = [];

    const base = new THREE.Mesh(
      new THREE.CylinderGeometry(0.105, 0.13, 0.035, 24),
      standardMat(0x243247, { roughness: 0.5, metalness: 0.4 })
    );
    base.position.set(0, 0.018, 0);
    base.receiveShadow = true;
    root.add(base);

    const pole = new THREE.Mesh(
      new THREE.CylinderGeometry(0.021, 0.026, 0.95, 14),
      standardMat(0x33465f, { roughness: 0.4, metalness: 0.55 })
    );
    pole.position.set(0, 0.5, 0);
    pole.castShadow = true;
    root.add(pole);

    const bulb = new THREE.Mesh(
      new THREE.SphereGeometry(0.062, 20, 20),
      new THREE.MeshStandardMaterial({
        color: 0x9ffff1, emissive: ACCENT, emissiveIntensity: 2.6, roughness: 0.25,
      })
    );
    bulb.position.set(0, 1.02, 0);
    root.add(bulb);
    this.apBulb = bulb;

    const glowTex = makeGlowTexture();
    const halo = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: glowTex, color: 0xffffff, transparent: true, opacity: 0.55,
        blending: THREE.AdditiveBlending, depthWrite: false,
      })
    );
    halo.scale.set(0.85, 0.85, 1);
    halo.position.set(0, 1.02, 0);
    root.add(halo);

    for (let i = 0; i < 3; i++) {
      const ring = new THREE.Mesh(
        new THREE.RingGeometry(0.16, 0.19, 48),
        new THREE.MeshBasicMaterial({
          color: ACCENT, transparent: true, opacity: 0.5,
          side: THREE.DoubleSide, depthWrite: false, blending: THREE.AdditiveBlending,
        })
      );
      ring.rotation.x = -Math.PI / 2;
      ring.position.set(0, 0.016, 0);
      root.add(ring);
      this.apRings.push(ring);
    }

    // 透明拾取体：让路由器更容易被抓到
    const hit = new THREE.Mesh(
      new THREE.CylinderGeometry(0.26, 0.26, 1.25, 12),
      new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false })
    );
    hit.position.set(0, 0.62, 0);
    hit.userData.ap = true;
    root.add(hit);
  }

  _buildStart(start) {
    const g = this.startGroup;
    const x = start.x_m - this.room.w / 2;
    const z = start.y_m - this.room.d / 2;

    const ring = new THREE.Mesh(
      new THREE.RingGeometry(0.24, 0.29, 40),
      new THREE.MeshBasicMaterial({
        color: BLUE, transparent: true, opacity: 0.75, side: THREE.DoubleSide, depthWrite: false,
      })
    );
    ring.rotation.x = -Math.PI / 2;
    ring.position.set(x, 0.016, z);
    g.add(ring);
    this.startRing = ring;

    const car = new THREE.Group();
    car.position.set(x, 0, z);
    const bodyMat = standardMat(0x2b6fd6, { roughness: 0.45, metalness: 0.25 });
    const deckMat = standardMat(0x1b2230, { roughness: 0.6 });
    const wheelMat = standardMat(0x141821, { roughness: 0.9 });
    addBox(car, 0.34, 0.075, 0.28, deckMat, 0, 0.09, 0);
    addBox(car, 0.3, 0.055, 0.24, bodyMat, 0, 0.155, 0);
    addBox(car, 0.09, 0.1, 0.09, standardMat(0xe9f2ff, { roughness: 0.5 }), 0, 0.23, 0);
    const wheelGeo = new THREE.CylinderGeometry(0.052, 0.052, 0.035, 16);
    for (const sx of [-1, 1]) for (const sz of [-1, 1]) {
      const wheel = new THREE.Mesh(wheelGeo, wheelMat);
      wheel.rotation.z = Math.PI / 2;
      wheel.position.set(sx * 0.155, 0.052, sz * 0.115);
      wheel.castShadow = true;
      car.add(wheel);
    }
    // 行进方向箭头
    const arrow = new THREE.Mesh(
      new THREE.ConeGeometry(0.05, 0.16, 4),
      new THREE.MeshBasicMaterial({ color: BLUE, transparent: true, opacity: 0.9 })
    );
    arrow.rotation.x = Math.PI / 2;
    arrow.position.set(0, 0.025, 0.26);
    car.add(arrow);
    g.add(car);
    car.scale.setScalar(1.18);
    this.car = car;
  }

  /* ---------- 标签 ---------- */

  _addTag(title, sub, position, cls) {
    const el = document.createElement('div');
    el.className = `tag ${cls || ''}`;
    el.innerHTML = `<span class="tag-title">${title}</span>${sub ? `<span class="tag-sub">${sub}</span>` : ''}`;
    this.labelLayer.appendChild(el);
    const tag = { el, pos: position.clone(), baseY: position.y };
    this.tags.push(tag);
    return tag;
  }

  _buildLabels() {
    this.data.furniture.forEach((item) => {
      const y = Math.min(item.height_m + 0.22, 2.1);
      const tag = this._addTag(
        item.name,
        '',
        new THREE.Vector3(item.cx - this.room.w / 2, y, item.cz - this.room.d / 2),
        'tag-furniture'
      );
      this.tagByIndex.set(item.index, tag);
    });
    this.apTag = this._addTag(
      '路由器',
      '',
      new THREE.Vector3(this.data.ap.x_m - this.room.w / 2, 1.2, this.data.ap.y_m - this.room.d / 2),
      'tag-ap'
    );
  }

  _updateLabels() {
    const width = this.container.clientWidth;
    const height = this.container.clientHeight;
    const v = new THREE.Vector3();
    const items = [];
    for (const tag of this.tags) {
      v.copy(tag.pos).project(this.camera);
      const x = (v.x * 0.5 + 0.5) * width;
      const y = (-v.y * 0.5 + 0.5) * height;
      const visible = v.z > -1 && v.z < 1 && x > -70 && x < width + 70 && y > -50 && y < height + 50;
      if (!tag.w) tag.w = tag.el.offsetWidth || 60;
      items.push({ tag, x, y, w: tag.w, visible });
    }
    // 简单的屏幕空间避让：同列重叠时向上错开，避免标签糊成一团
    items.sort((a, b) => a.y - b.y);
    const placed = [];
    for (const it of items) {
      let ty = it.y;
      if (it.visible) {
        for (let guard = 0; guard < 2; guard++) {
          const hit = placed.find(
            (p) => Math.abs(p.x - it.x) < (p.w + it.w) / 2 + 4 && Math.abs(p.y - ty) < 22
          );
          if (!hit) break;
          ty = hit.y - 22;
        }
        placed.push({ x: it.x, y: ty, w: it.w });
      }
      it.tag.el.style.opacity = it.visible ? '1' : '0';
      it.tag.el.style.transform = `translate(-50%, -100%) translate(${it.x}px, ${ty}px)`;
    }
  }

  /* ---------- 交互 ---------- */

  setLayer(name, visible) {
    const map = {
      grid: this.gridGroup,
      blocked: this.blockedGroup,
      route: this.routeGroup,
      stops: this.stopGroup,
      walls: this.wallGroup,
      heatmap: this.heatGroup,
      ai: this.aiGroup,
      labels: null,
    };
    if (name === 'labels') {
      this.labelLayer.style.display = visible ? '' : 'none';
      return;
    }
    const group = map[name];
    if (group) group.visible = visible;
  }

  setAutoRotate(enabled) {
    this.autoRotate = enabled;
    this.controls.autoRotate = enabled;
  }

  setView(name, duration = 620) {
    const presets = {
      iso: { theta: Math.PI * 0.25, phi: Math.PI * 0.30, zoom: 1 },
      top: { theta: 0, phi: 0.02, zoom: 1.02 },
      corner: { theta: Math.PI * 0.16, phi: Math.PI * 0.37, zoom: 1.2 },
      front: { theta: Math.PI * 0.42, phi: Math.PI * 0.40, zoom: 1.12 },
    };
    const preset = presets[name] || presets.iso;
    const radius = 30;
    const target = new THREE.Vector3(0, 0.55, 0);
    const sph = new THREE.Spherical(radius, preset.phi, preset.theta);

    if (!duration) {
      this.camera.position.setFromSpherical(sph).add(target);
      this.controls.target.copy(target);
      this.camera.zoom = preset.zoom;
      this.camera.updateProjectionMatrix();
      this.controls.update();
      return;
    }

    const from = new THREE.Spherical().setFromVector3(
      this.camera.position.clone().sub(this.controls.target)
    );
    this.anim = {
      t0: performance.now(),
      dur: duration,
      fromSph: from,
      toSph: sph,
      fromTarget: this.controls.target.clone(),
      toTarget: target,
      fromZoom: this.camera.zoom,
      toZoom: preset.zoom,
    };
  }

  _updateAnim(now) {
    if (!this.anim) return;
    const a = this.anim;
    const t = Math.min(1, (now - a.t0) / a.dur);
    const k = easeInOut(t);
    let dTheta = a.toSph.theta - a.fromSph.theta;
    while (dTheta > Math.PI) dTheta -= Math.PI * 2;
    while (dTheta < -Math.PI) dTheta += Math.PI * 2;
    const sph = new THREE.Spherical(
      lerp(a.fromSph.radius, a.toSph.radius, k),
      lerp(a.fromSph.phi, a.toSph.phi, k),
      a.fromSph.theta + dTheta * k
    );
    const target = a.fromTarget.clone().lerp(a.toTarget, k);
    this.camera.position.setFromSpherical(sph).add(target);
    this.controls.target.copy(target);
    this.camera.zoom = lerp(a.fromZoom, a.toZoom, k);
    this.camera.updateProjectionMatrix();
    if (t >= 1) this.anim = null;
  }

  focusFurniture(index) {
    const item = this.data.furniture.find((f) => f.index === index);
    if (!item) return;
    const target = new THREE.Vector3(
      item.cx - this.room.w / 2, Math.min(item.height_m * 0.6, 1), item.cz - this.room.d / 2
    );
    const sph = new THREE.Spherical().setFromVector3(
      this.camera.position.clone().sub(this.controls.target)
    );
    this.anim = {
      t0: performance.now(),
      dur: 520,
      fromSph: sph,
      toSph: new THREE.Spherical(sph.radius, Math.PI * 0.34, Math.PI * 0.24),
      fromTarget: this.controls.target.clone(),
      toTarget: target,
      fromZoom: this.camera.zoom,
      toZoom: Math.max(this.camera.zoom, 1.35),
    };
  }

  _pick() {
    if (!this.data || this.dragging) return;
    if (this.pointer.x < -1 && this.pointer.y < -1) {
      this._setHover(-1, null);
      this.onHover(null);
      return;
    }
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const targets = [this.floor, ...this.pickables].filter(Boolean);
    const hits = this.raycaster.intersectObjects(targets, true);

    if (hits.length) {
      const hit = hits[0];
      let obj = hit.object;
      let index = -1;
      while (obj) {
        if (obj.userData.furnitureIndex !== undefined) {
          index = obj.userData.furnitureIndex;
          break;
        }
        obj = obj.parent;
      }
      if (index >= 0) {
        this._setHover(index, null);
        const item = this.data.furniture.find((f) => f.index === index);
        this.onHover({ type: 'furniture', item, client: this.pointerClient });
        return;
      }
      if (hit.object.userData.isFloor) {
        const p = hit.point;
        const col = Math.min(this.room.cols, Math.max(1, Math.floor((p.x + this.room.w / 2) / this.room.cw) + 1));
        const row = Math.min(this.room.rows, Math.max(1, Math.floor((p.z + this.room.d / 2) / this.room.cd) + 1));
        const cell = this.data.cells.find((c) => c.row === row && c.column === col);
        const heat = this.heatData?.points?.find((p) => p.row === row && p.column === col);
        this._setHover(-1, cell);
        this.onHover({ type: 'cell', cell, heat, client: this.pointerClient });
        return;
      }
    }
    this._setHover(-1, null);
    this.onHover(null);
  }

  _setHover(index, cell) {
    if (index >= 0) {
      this._showBox(index);
      this.cellPlane.visible = false;
      this.cellEdges.visible = false;
    } else if (cell) {
      this.hoverBox.visible = false;
      const x = cell.x_m - this.room.w / 2;
      const z = cell.y_m - this.room.d / 2;
      const sx = this.room.cw * 0.94;
      const sz = this.room.cd * 0.94;
      this.cellPlane.scale.set(sx, sz, 1);
      this.cellPlane.position.set(x, 0.014, z);
      this.cellPlane.visible = true;
      this.cellEdges.scale.set(sx, sz, 1);
      this.cellEdges.position.set(x, 0.015, z);
      this.cellEdges.visible = true;
    } else {
      if (this.selectedIndex >= 0) this._showBox(this.selectedIndex);
      else this.hoverBox.visible = false;
      this.cellPlane.visible = false;
      this.cellEdges.visible = false;
    }
  }

  screenshot() {
    this.renderer.render(this.scene, this.camera);
    const url = this.renderer.domElement.toDataURL('image/png');
    const link = document.createElement('a');
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
    link.download = `miniauto-3d-${stamp}.png`;
    link.href = url;
    link.click();
  }

  resize() {
    const width = this.container.clientWidth;
    const height = this.container.clientHeight;
    if (!width || !height) return;
    this.renderer.setSize(width, height, false);
    const aspect = width / height;
    const w = this.room.w;
    const d = this.room.d;
    // 等轴测下房间的投影范围：宽度约 (w+d)*0.74，高度含墙体约 (w+d)*0.4+3
    const projW = (w + d) * 0.74;
    const projH = (w + d) * 0.4 + 3.0;
    const margin = 1.2;
    let viewH = projH * margin;
    let viewW = viewH * aspect;
    if (viewW < projW * margin) {
      viewW = projW * margin;
      viewH = viewW / aspect;
    }
    this.camera.left = -viewW / 2;
    this.camera.right = viewW / 2;
    this.camera.top = viewH / 2;
    this.camera.bottom = -viewH / 2;
    this.camera.updateProjectionMatrix();
  }

  /* ---------- 动画 ---------- */

  _tick(now) {
    this._raf = requestAnimationFrame(this._tick);
    this._updateAnim(now);
    this.controls.update();
    const t = now / 1000;

    if (this.apRings) {
      this.apRings.forEach((ring, i) => {
        const phase = (t * 0.5 + i / this.apRings.length) % 1;
        const scale = 1 + phase * 7.2;
        ring.scale.set(scale, scale, 1);
        ring.material.opacity = (1 - phase) * 0.5;
      });
    }
    if (this.apBulb) {
      this.apBulb.material.emissiveIntensity = 2.3 + Math.sin(t * 3.2) * 0.55;
    }
    if (this.startRing) {
      this.startRing.material.opacity = 0.55 + Math.sin(t * 2.2) * 0.2;
    }
    if (this.car) {
      this.car.position.y = Math.sin(t * 2.4) * 0.006;
    }
    if (this.routeDots && this.routeCurve) {
      this.routeDots.forEach((dot, i) => {
        const u = (t * 0.035 + i / this.routeDots.length) % 1;
        this.routeCurve.getPointAt(u, dot.position);
        dot.position.y = 0.045;
      });
    }
    if (this.pointerDirty) {
      this.pointerDirty = false;
      this._pick();
    }
    this._updateLabels();
    this.renderer.render(this.scene, this.camera);
  }

  dispose() {
    cancelAnimationFrame(this._raf);
    window.removeEventListener('resize', this._onResize);
    this.renderer.domElement.removeEventListener('pointermove', this._onMove);
    this.renderer.domElement.removeEventListener('pointerleave', this._onLeave);
    this.renderer.domElement.removeEventListener('pointerdown', this._onDown, true);
    this.renderer.domElement.removeEventListener('pointermove', this._onDragMove);
    this.renderer.domElement.removeEventListener('pointerup', this._onUp);
    this.renderer.domElement.removeEventListener('pointercancel', this._onUp);
    this._clearDynamic();
    this.tags.forEach((t) => t.el.remove());
    this.renderer.dispose();
  }
}
