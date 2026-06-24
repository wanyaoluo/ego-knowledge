// views/promotion-river/index.js · 升格河流（note→concept→decision→dossier 成长路径可视化）
// 横向 4 阶段泳道，每列堆叠该 kind 节点卡片；SVG overlay 画 derived_from 升格连线：
//   - cross（跨阶段）：source 派生自低阶段 target → 从左(源头/低)流向右(新/高)，樱粉+箭头+流动
//   - same（同阶段派生）：同列内弧线，灰紫低透，hover 高亮
// 派生语义：edge.source=派生方(新) / edge.target=源头(旧)，与 detail promotionTrack 一致。
// decision 数据为 0：空阶段列 + "暂无决策 · 可从 concept 升格"引导。
// 点卡片跳 detail；折叠超出；ResizeObserver 驱动连线重绘。CSS 在 index.html <link> 引入。
import {
  KIND_COLORS, KIND_LABELS,
  loadGraph, buildIndex, getNodeById, authPct, trunc,
} from '../../lib/data-layer.js';
import { escapeHtml } from '../../lib/escape.js';
import * as actions from '../../lib/actions.js';

// 升格链（与 detail 视图 PROMO_CHAIN 一致，知识成长路径真源）
const PROMO_CHAIN = ['note', 'concept', 'decision', 'dossier'];
const COLLAPSE_LIMIT = 6;     // 每列默认显示前 N 条，超出折叠

// 阶段元数据：标签 + 叙事文案（让"阶段"不只是一个 kind 名，而是成长路径上的位置）
const STAGE_META = {
  note:     { hint: '草记 · 灵感原石',     verb: '提炼为概念' },
  concept:  { hint: '提炼 · 抽象成形',     verb: '拍板成决策' },
  decision: { hint: '拍板 · 已采纳方向',   verb: '沉淀为档案' },
  dossier:  { hint: '沉淀 · 长效档案',     verb: '终点' },
};

let root = null;
let graph = null;
let index = null;
let resizeObs = null;
let rafId = 0;
const expanded = new Set();   // 已展开全部的阶段 kind

/** 阶段位置（0=note 最左 … 3=dossier 最右）；非升格链返回 -1 */
const stagePos = (kind) => PROMO_CHAIN.indexOf(kind);

// ===== 数据派生 =====

/** 4 阶段节点分组（组内按 authority 降序，重要的在前） */
function buildLanes() {
  const lanes = {};
  PROMO_CHAIN.forEach((k) => { lanes[k] = []; });
  graph.nodes.forEach((n) => {
    if (stagePos(n.kind) >= 0) lanes[n.kind].push(n);
  });
  Object.keys(lanes).forEach((k) => {
    lanes[k].sort((a, b) => (Number(b.authority) || 0) - (Number(a.authority) || 0));
  });
  return lanes;
}

/**
 * 升格连线：两端都在升格链的 derived_from 边。
 * @returns {{sourceId:string, targetId:string, diff:number, type:'cross'|'same'}[]}
 *   diff = source阶段 - target阶段；>0 跨阶段升格（左低→右高），=0 同阶段派生
 */
function buildLinks() {
  const links = [];
  graph.edges.forEach((e) => {
    if (e.type !== 'derived_from') return;
    const s = getNodeById(graph, e.source);
    const t = getNodeById(graph, e.target);
    if (!s || !t) return;
    const sp = stagePos(s.kind), tp = stagePos(t.kind);
    if (sp < 0 || tp < 0) return;
    const diff = sp - tp;
    links.push({ sourceId: e.source, targetId: e.target, diff, type: diff > 0 ? 'cross' : 'same' });
  });
  return links;
}

/** 节点派生计数：asSource=派生自几个源头，asTarget=被几个作为源头 */
function deriveCount(nodeId) {
  const edges = index.edgeIndex.get(nodeId) || [];
  let asSource = 0, asTarget = 0;
  edges.forEach((e) => {
    if (e.type !== 'derived_from') return;
    if (e.source === nodeId) asSource++;
    else if (e.target === nodeId) asTarget++;
  });
  return { asSource, asTarget };
}

// ===== HTML =====

function headerHTML(lanes, links) {
  const crossN = links.filter((l) => l.type === 'cross').length;
  const sameN = links.filter((l) => l.type === 'same').length;
  const total = PROMO_CHAIN.reduce((s, k) => s + lanes[k].length, 0);
  return `
    <header class="river-header panel">
      <div class="river-header-main">
        <h1 class="river-title">升格河流</h1>
        <p class="river-sub">知识成长路径 · <b>${total}</b> 条知识在 <b>4</b> 阶段间流动 ·
          <span class="river-sub-cross">跨阶段升格 ${crossN}</span> ·
          <span class="river-sub-same">同阶段派生 ${sameN}</span></p>
      </div>
      <div class="river-header-legend">
        <span class="river-legend-item"><span class="river-legend-line river-legend-cross"></span>跨阶段升格</span>
        <span class="river-legend-item"><span class="river-legend-line river-legend-same"></span>同阶段派生</span>
      </div>
    </header>`;
}

function cardHTML(node) {
  const kindColor = KIND_COLORS[node.kind] || '#897989';
  const pct = authPct(Number(node.authority) || 0).toFixed(0);
  const { asSource, asTarget } = deriveCount(node.id);
  const deriv = [];
  if (asSource > 0) deriv.push(`<span class="river-deriv" title="派生自 ${asSource} 个源头">←${asSource}</span>`);
  if (asTarget > 0) deriv.push(`<span class="river-deriv is-target" title="被 ${asTarget} 个节点作为源头">→${asTarget}</span>`);
  return `
    <button class="river-card" type="button" data-id="${escapeHtml(node.id)}" data-stage="${node.kind}"
            style="--kind-color:${kindColor}" role="button" tabindex="0"
            aria-label="查看详情：${escapeHtml(node.title)}">
      <span class="river-card-bar" aria-hidden="true"></span>
      <span class="river-card-title">${escapeHtml(trunc(node.title, 26))}</span>
      <span class="river-card-foot">
        <span class="river-card-auth">权威 ${pct}</span>
        ${deriv.length ? `<span class="river-card-derivs">${deriv.join('')}</span>` : ''}
      </span>
    </button>`;
}

function laneHTML(kind, nodes) {
  const color = KIND_COLORS[kind] || '#897989';
  const isOpen = expanded.has(kind);
  const overflow = nodes.length - COLLAPSE_LIMIT;
  const visible = isOpen ? nodes : nodes.slice(0, COLLAPSE_LIMIT);

  // 空阶段（如 decision=0）：空态卡 + 升格引导
  if (!nodes.length) {
    const prev = PROMO_CHAIN[Math.max(0, stagePos(kind) - 1)];
    return `
      <section class="river-lane is-empty" data-stage="${kind}" style="--kind-color:${color}">
        <header class="river-lane-head">
          <span class="river-lane-dot"></span>
          <div class="river-lane-text">
            <h2 class="river-lane-name">${KIND_LABELS[kind]}</h2>
            <span class="river-lane-hint">${STAGE_META[kind].hint}</span>
          </div>
          <span class="river-lane-count">0</span>
        </header>
        <div class="river-lane-empty">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="12" cy="12" r="8" fill="none" stroke-dasharray="3 3"/>
            <line x1="9" y1="12" x2="15" y2="12"/>
            <line x1="12" y1="9" x2="12" y2="15"/>
          </svg>
          <p class="river-empty-title">暂无决策</p>
          <p class="river-empty-hint muted">该阶段空缺 · 可从 <b>${KIND_LABELS[prev]}</b> 升格产生</p>
        </div>
      </section>`;
  }

  const cards = visible.map((n) => cardHTML(n)).join('');
  const moreBtn = overflow > 0
    ? `<button class="river-more" data-more="${kind}" type="button">
         ${isOpen
           ? `<span class="river-more-label">收起</span>`
           : `<span class="river-more-num">+${overflow}</span><span class="river-more-label">展开</span>`}
       </button>`
    : '';

  return `
    <section class="river-lane" data-stage="${kind}" style="--kind-color:${color}">
      <header class="river-lane-head">
        <span class="river-lane-dot"></span>
        <div class="river-lane-text">
          <h2 class="river-lane-name">${KIND_LABELS[kind]}</h2>
          <span class="river-lane-hint">${STAGE_META[kind].hint}</span>
        </div>
        <span class="river-lane-count">${nodes.length}</span>
      </header>
      <div class="river-lane-list" role="list">${cards}</div>
      ${moreBtn}
    </section>`;
}

/** 列间流向标记（成长方向 note→concept→decision→dossier） */
function flowMarkerHTML() {
  return PROMO_CHAIN.slice(0, -1).map((_, i) => {
    // 4 列间 3 个间隙，流向箭头用 CSS grid 定位（见 css .river-flow）
    return `<span class="river-flow-arrow" data-flow="${i}" aria-hidden="true">›</span>`;
  }).join('');
}

function renderRiver(lanes, links) {
  const box = root.querySelector('[data-river]');
  const lanesHTML = PROMO_CHAIN.map((k) => laneHTML(k, lanes[k])).join('');
  box.innerHTML = `
    <svg class="river-svg" data-svg aria-hidden="true"></svg>
    ${flowMarkerHTML()}
    ${lanesHTML}`;
  // 连线坐标依赖卡片 DOM，渲染后绘制
  drawLinks(links);
}

// ===== SVG 连线层 =====

/** 卡片元素相对河流容器的中心坐标（找不到返回 null，如折叠不可见） */
function cardCenter(id, riverRect) {
  const el = root.querySelector(`[data-id="${CSS.escape(id)}"]`);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { x: r.left - riverRect.left + r.width / 2, y: r.top - riverRect.top + r.height / 2 };
}

/**
 * 节点在河流中的坐标：卡片可见用卡片中心；折叠不可见回退到「列代表点」
 * （列水平中心 + 列头下方）。专为 cross 连线设计——跨阶段升格是核心叙事，
 * 即使端点折叠也应可见（连线落在该阶段列的示意位置，hover 可见端点仍可高亮）。
 * @returns {{x:number,y:number}|null}
 */
function pointFor(id, riverRect) {
  const el = root.querySelector(`[data-id="${CSS.escape(id)}"]`);
  if (el) {
    const r = el.getBoundingClientRect();
    return { x: r.left - riverRect.left + r.width / 2, y: r.top - riverRect.top + r.height / 2 };
  }
  const node = getNodeById(graph, id);
  if (!node) return null;
  const lane = root.querySelector(`.river-lane[data-stage="${node.kind}"]`);
  if (!lane) return null;
  const laneRect = lane.getBoundingClientRect();
  const x = laneRect.left - riverRect.left + laneRect.width / 2;
  const head = lane.querySelector('.river-lane-head');
  const hr = head ? head.getBoundingClientRect() : null;
  const y = hr ? (hr.bottom - riverRect.top + 28) : (laneRect.top - riverRect.top + 44);
  return { x, y };
}

/**
 * 绘制升格连线。cross 用樱粉贝塞尔+箭头+流动；same 用灰紫同列弧线低透。
 * - cross（跨阶段升格 · 核心叙事）：始终画，端点折叠回退列代表点（pointFor）
 * - same（同阶段派生 · 背景纹理）：仅两端卡片可见才画（cardCenter），避免折叠态重叠
 */
function drawLinks(links) {
  const svg = root.querySelector('[data-svg]');
  const river = root.querySelector('[data-river]');
  if (!svg || !river) return;
  const riverRect = river.getBoundingClientRect();

  // svg 尺寸跟随河流内容区（含横向滚动）
  svg.setAttribute('width', river.scrollWidth);
  svg.setAttribute('height', river.scrollHeight);
  svg.setAttribute('viewBox', `0 0 ${river.scrollWidth} ${river.scrollHeight}`);

  const defs = [];
  const paths = [];
  links.forEach((l, i) => {
    const lookup = l.type === 'cross' ? pointFor : cardCenter;
    const t = lookup(l.targetId, riverRect);   // 源头 / 低阶段
    const s = lookup(l.sourceId, riverRect);   // 派生 / 高阶段
    if (!t || !s) return;

    let d;
    if (l.type === 'cross') {
      // 跨阶段：左(低)→右(高) 贝塞尔，控制点水平偏移制造流向弧
      const dx = Math.max(40, Math.abs(s.x - t.x) * 0.45);
      d = `M ${t.x.toFixed(1)} ${t.y.toFixed(1)} C ${(t.x + dx).toFixed(1)} ${t.y.toFixed(1)}, ${(s.x - dx).toFixed(1)} ${s.y.toFixed(1)}, ${s.x.toFixed(1)} ${s.y.toFixed(1)}`;
    } else {
      // 同阶段：同列内竖向弧，控制点向列右侧凸出
      const midY = (t.y + s.y) / 2;
      const ctrlX = Math.max(t.x, s.x) + 26;
      d = `M ${t.x.toFixed(1)} ${t.y.toFixed(1)} Q ${ctrlX.toFixed(1)} ${midY.toFixed(1)}, ${s.x.toFixed(1)} ${s.y.toFixed(1)}`;
    }
    const cls = l.type === 'cross' ? 'river-link-cross' : 'river-link-same';
    // 箭头放终点（source/派生端），指向升格目标，直观表达"流向高阶段"
    const marker = l.type === 'cross' ? ' marker-end="url(#river-arrow)"' : '';
    paths.push(
      `<path class="${cls}" d="${d}"${marker}
         data-source-id="${escapeHtml(l.sourceId)}" data-target-id="${escapeHtml(l.targetId)}"/>`
    );
  });

  // 箭头 marker（樱粉，仅 cross 用；refX=9 让箭尖对齐路径终点）
  defs.push(
    `<marker id="river-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
       <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--accent-primary)"/>
     </marker>`
  );

  svg.innerHTML = `<defs>${defs.join('')}</defs>${paths.join('')}`;
}

/** rAF 节流重绘（resize / 折叠触发） */
function scheduleRedraw(links) {
  if (rafId) cancelAnimationFrame(rafId);
  rafId = requestAnimationFrame(() => { rafId = 0; drawLinks(links); });
}

// ===== 交互 =====

function bind(links) {
  const river = root.querySelector('[data-river]');

  river.addEventListener('click', (e) => {
    const more = e.target.closest('[data-more]');
    if (more) {
      const k = more.dataset.more;
      if (expanded.has(k)) expanded.delete(k); else expanded.add(k);
      renderRiver(buildLanes(), links);   // 重渲卡片 → 重绘连线
      return;
    }
    const card = e.target.closest('[data-id]');
    if (card) actions.openDetail(card.dataset.id);
  });

  river.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const card = e.target.closest('[data-id]');
    if (card) { e.preventDefault(); actions.openDetail(card.dataset.id); }
  });

  // hover 卡片 → 高亮其连线（同 source/target）
  const highlight = (id, on) => {
    const sel = `path[data-source-id="${CSS.escape(id)}"], path[data-target-id="${CSS.escape(id)}"]`;
    river.querySelectorAll(sel).forEach((p) => p.classList.toggle('is-hl', on));
  };
  river.addEventListener('mouseover', (e) => {
    const card = e.target.closest('[data-id]');
    if (card) highlight(card.dataset.id, true);
  });
  river.addEventListener('mouseout', (e) => {
    const card = e.target.closest('[data-id]');
    if (card) highlight(card.dataset.id, false);
  });

  // resize（视口宽变 → 列宽变 → 卡片坐标变）+ 折叠后高度变，统一重绘连线
  resizeObs = new ResizeObserver(() => scheduleRedraw(links));
  resizeObs.observe(river);
}

const ERROR_HTML = `
  <div class="placeholder">
    <div class="panel placeholder-card">
      <h1 class="placeholder-title">升格河流</h1>
      <div class="placeholder-sub">数据加载失败</div>
      <div class="placeholder-hint">无法读取 <code>window.__EK_GRAPH</code>，请通过 <code>http://localhost:5188</code> 访问。</div>
    </div>
  </div>`;

export function mount(container) {
  root = container;
  root.classList.add('view-promotion-river');

  graph = loadGraph();
  if (!graph) { root.innerHTML = ERROR_HTML; return; }
  index = buildIndex(graph);

  const lanes = buildLanes();
  const links = buildLinks();

  root.innerHTML = `
    <div class="river-shell">
      ${headerHTML(lanes, links)}
      <div class="river-scroll">
        <div class="river" data-river=""></div>
      </div>
    </div>`;
  renderRiver(lanes, links);
  bind(links);
}

export function unmount() {
  if (rafId) { cancelAnimationFrame(rafId); rafId = 0; }
  if (resizeObs) { resizeObs.disconnect(); resizeObs = null; }
  if (root) { root.innerHTML = ''; root.classList.remove('view-promotion-river'); }
  root = null;
  graph = null;
  index = null;
  expanded.clear();
}
