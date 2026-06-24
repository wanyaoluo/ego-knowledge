// views/timeline/index.js · 时间线（Phase 4 · 节点演化时间轴）
// 数据缺 created_at/updated_at → 用 freshness 代理时间排序：
//   volatile=近期易变（最新）→ watch=观察中 → stable=稳定沉淀（最陈旧）。
//   组内次级排序：status draft>active>authoritative（草稿最新），再 authority 降序。
// 纵向时间轴：左 freshness 色 rail + 圆点，右节点卡片；三段分组，超出折叠。
// 点卡片 → actions.openDetail。复用 lib/data/* + lib/store + lib/actions。
// CSS 在 index.html 用 <link> 引入（无 build 环境不支持 import css）。
import {
  KIND_COLORS, KIND_LABELS, FRESHNESS_LABELS, FRESHNESS_COLORS,
  STATUS_LABELS,
  loadGraph, buildIndex, authPct,
} from '../../lib/data-layer.js';
import { escapeHtml } from '../../lib/escape.js';
import * as actions from '../../lib/actions.js';

// 时间代理排序键：freshness 主序（值小=越新），status 次序（draft 最新）
const FRESHNESS_ORDER = { volatile: 0, watch: 1, stable: 2 };
const STATUS_ORDER = { draft: 0, active: 1, authoritative: 2, legacy: 3, deprecated: 4, archived: 5 };
const GROUP_SEQUENCE = ['volatile', 'watch', 'stable'];
const GROUP_TONE = {
  volatile: { label: '近期 · 易变', hint: '高频变动 · 需关注' },
  watch: { label: '观察中', hint: '待巡检确认' },
  stable: { label: '稳定沉淀', hint: '已归档成熟' },
};
const COLLAPSE_LIMIT = 12;   // 每组默认显示前 N 条，超出折叠

let root = null;
let graph = null;
let index = null;
const expanded = new Set();   // 已展开全部的 freshness 组

const domainLabel = (d) => (!d || d === '_unsorted' ? '未归类' : d.replace(/-/g, ' '));

/** 按 freshness 分组 + 组内代理排序（freshness 主 / status 次 / authority 末）。 */
function buildGroups() {
  const map = {};
  GROUP_SEQUENCE.forEach((f) => { map[f] = []; });
  graph.nodes.forEach((n) => {
    const f = FRESHNESS_ORDER[n.freshness] !== undefined ? n.freshness : 'stable';
    if (!map[f]) map[f] = [];
    map[f].push(n);
  });
  Object.keys(map).forEach((f) => {
    map[f].sort((a, b) => {
      const sa = STATUS_ORDER[a.status] ?? 9;
      const sb = STATUS_ORDER[b.status] ?? 9;
      if (sa !== sb) return sa - sb;
      return (Number(b.authority) || 0) - (Number(a.authority) || 0);
    });
  });
  return GROUP_SEQUENCE
    .filter((f) => map[f].length)
    .map((f) => ({ freshness: f, nodes: map[f] }));
}

function headerHTML() {
  const legend = GROUP_SEQUENCE.map((f) => {
    const c = FRESHNESS_COLORS[f];
    const n = index.freshnessCounts[f] || 0;
    return `
      <span class="tl-legend-item">
        <span class="tl-legend-dot" style="background:${c}"></span>
        <span class="tl-legend-name">${GROUP_TONE[f].label}</span>
        <b class="tl-legend-count">${n}</b>
      </span>`;
  }).join('');
  return `
    <header class="tl-header panel">
      <div class="tl-header-main">
        <h1 class="tl-title">时间线</h1>
        <p class="tl-sub">按新鲜度排列 · 数据无时间戳，以 <b>freshness</b> 代理演化（易变→观察→稳定）</p>
      </div>
      <div class="tl-header-legend">${legend}</div>
    </header>`;
}

function itemHTML(node, freshness) {
  const freshColor = FRESHNESS_COLORS[freshness] || '#897989';
  const kindColor = KIND_COLORS[node.kind] || '#897989';
  const pctVal = authPct(Number(node.authority) || 0).toFixed(0);
  const statusLabel = STATUS_LABELS[node.status] || node.status || '—';
  return `
    <article class="tl-item" data-id="${escapeHtml(node.id)}" role="button" tabindex="0"
             style="--fresh-color:${freshColor}" aria-label="查看详情：${escapeHtml(node.title)}">
      <div class="tl-dot-wrap"><span class="tl-dot"></span></div>
      <div class="tl-card" style="--kind-color:${kindColor}">
        <div class="tl-card-head">
          <span class="tl-kind">${KIND_LABELS[node.kind] || node.kind}</span>
          <span class="tl-status">${escapeHtml(statusLabel)}</span>
          <span class="tl-auth-num">${pctVal}</span>
        </div>
        <h3 class="tl-card-title">${escapeHtml(node.title)}</h3>
        <div class="tl-card-meta">
          <span class="tl-card-domain">${escapeHtml(domainLabel(node.domain))}</span>
          <span class="tl-auth-bar"><span class="tl-auth-fill" style="width:${pctVal}%"></span></span>
        </div>
      </div>
    </article>`;
}

function groupHTML(g) {
  const color = FRESHNESS_COLORS[g.freshness];
  const isOpen = expanded.has(g.freshness);
  const overflow = g.nodes.length - COLLAPSE_LIMIT;
  const visible = isOpen ? g.nodes : g.nodes.slice(0, COLLAPSE_LIMIT);
  const items = visible.map((n) => itemHTML(n, g.freshness)).join('');
  const moreBtn = overflow > 0
    ? `<button class="tl-more" data-more="${g.freshness}" type="button">
         ${isOpen
           ? `<span class="tl-more-label">收起</span>`
           : `<span class="tl-more-num">+${overflow}</span><span class="tl-more-label">展开剩余</span>`}
       </button>`
    : '';
  return `
    <section class="tl-group" style="--fresh-color:${color}">
      <header class="tl-group-head">
        <span class="tl-group-dot"></span>
        <div class="tl-group-text">
          <h2 class="tl-group-name">${GROUP_TONE[g.freshness].label}</h2>
          <span class="tl-group-hint">${GROUP_TONE[g.freshness].hint}</span>
        </div>
        <span class="tl-group-count">${g.nodes.length}</span>
      </header>
      <div class="tl-list" role="list">
        ${items}
        ${moreBtn}
      </div>
    </section>`;
}

function renderGroups() {
  const box = root.querySelector('[data-groups]');
  const groups = buildGroups();
  if (!groups.length) {
    box.innerHTML = `<div class="tl-empty muted">暂无节点</div>`;
    return;
  }
  box.innerHTML = groups.map(groupHTML).join('');
}

function bind() {
  const box = root.querySelector('[data-groups]');
  box.addEventListener('click', (e) => {
    const more = e.target.closest('[data-more]');
    if (more) {
      const f = more.dataset.more;
      if (expanded.has(f)) expanded.delete(f);
      else expanded.add(f);
      renderGroups();
      return;
    }
    const item = e.target.closest('[data-id]');
    if (item) actions.openDetail(item.dataset.id);
  });
  box.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const item = e.target.closest('[data-id]');
    if (item) { e.preventDefault(); actions.openDetail(item.dataset.id); }
  });
}

const ERROR_HTML = `
  <div class="placeholder">
    <div class="panel placeholder-card">
      <h1 class="placeholder-title">时间线</h1>
      <div class="placeholder-sub">数据加载失败</div>
      <div class="placeholder-hint">无法读取 <code>window.__EK_GRAPH</code>，请通过 <code>http://localhost:5188</code> 访问。</div>
    </div>
  </div>`;

export function mount(container) {
  root = container;
  root.classList.add('view-timeline');

  graph = loadGraph();
  if (!graph) {
    root.innerHTML = ERROR_HTML;
    return;
  }
  index = buildIndex(graph);

  root.innerHTML = `
    <div class="tl-shell">
      ${headerHTML()}
      <div class="tl-groups" data-groups></div>
    </div>`;
  renderGroups();
  bind();
}

export function unmount() {
  if (root) {
    root.innerHTML = '';
    root.classList.remove('view-timeline');
  }
  root = null;
  graph = null;
  index = null;
  expanded.clear();
}
