// views/gallery/index.js · 卡片画廊（Phase 3 · 全库构成可视化）
// kind 分组卡片墙：concept/dossier/note/source 各一组（数据驱动，存在哪些 kind 就生成哪些组）。
// 每张卡片：title + kind badge + domain + authority bar；点击 → actions.openDetail。
// 复用 lib/data/* + lib/store + lib/actions。CSS 在 index.html 用 <link> 引入。
import {
  KIND_COLORS, KIND_LABELS,
  loadGraph, buildIndex, authPct,
} from '../../lib/data-layer.js';
import { escapeHtml } from '../../lib/escape.js';
import * as actions from '../../lib/actions.js';

// kind 语义顺序（知识层级：概念→档案→笔记→来源），数据驱动的其他 kind 排末位
const KIND_ORDER = ['concept', 'dossier', 'note', 'source', 'decision', 'view'];
const COLLAPSE_LIMIT = 16;   // 每组默认显示前 N 张，超出折叠

let root = null;
let graph = null;
let index = null;
const showAllKinds = new Set();   // 已展开全部的 kind 集合

const domainLabel = (d) => (!d || d === '_unsorted' ? '未归类' : d.replace(/-/g, ' '));

/** 按 kind 分组 + 语义排序；组内按 authority 降序。 */
function buildKindGroups() {
  const map = {};
  graph.nodes.forEach((n) => { (map[n.kind] = map[n.kind] || []).push(n); });
  Object.keys(map).forEach((k) => {
    map[k].sort((a, b) => (Number(b.authority) || 0) - (Number(a.authority) || 0));
  });
  const known = KIND_ORDER.filter((k) => map[k]);
  const extra = Object.keys(map).filter((k) => !KIND_ORDER.includes(k));
  return [...known, ...extra].map((kind) => ({ kind, nodes: map[kind] }));
}

function headerHTML() {
  const totalKinds = Object.keys(KIND_COLORS).filter((k) => index.kindCounts[k] > 0).length;
  return `
    <header class="gal-header panel">
      <div class="gal-header-main">
        <h1 class="gal-title">卡片画廊</h1>
        <p class="gal-sub">按类型分组 · 一眼看全库 <b>${graph.nodes.length}</b> 条知识的构成</p>
      </div>
      <div class="gal-header-legend">
        ${Object.keys(KIND_COLORS)
          .filter((k) => index.kindCounts[k] > 0)
          .map((k) => `
            <span class="gal-legend-item">
              <span class="gal-legend-dot" style="background:${KIND_COLORS[k]}"></span>
              <span class="gal-legend-name">${KIND_LABELS[k]}</span>
              <b class="gal-legend-count">${index.kindCounts[k]}</b>
            </span>`).join('')}
      </div>
    </header>`;
}

function cardHTML(node) {
  const color = KIND_COLORS[node.kind] || '#897989';
  const kindLabel = KIND_LABELS[node.kind] || node.kind;
  const pctVal = authPct(Number(node.authority) || 0).toFixed(0);
  return `
    <article class="gal-card" data-id="${escapeHtml(node.id)}" role="button" tabindex="0"
             style="--kind-color:${color}" aria-label="查看详情：${escapeHtml(node.title)}">
      <div class="gal-card-stripe"></div>
      <div class="gal-card-body">
        <div class="gal-card-head">
          <span class="gal-kind">${kindLabel}</span>
          <span class="gal-auth-num">${pctVal}</span>
        </div>
        <h3 class="gal-card-title">${escapeHtml(node.title)}</h3>
        <div class="gal-card-domain">${escapeHtml(domainLabel(node.domain))}</div>
        <div class="gal-auth-bar"><span class="gal-auth-fill" style="width:${pctVal}%"></span></div>
      </div>
    </article>`;
}

function groupHTML(g) {
  const color = KIND_COLORS[g.kind] || '#897989';
  const overflow = g.nodes.length - COLLAPSE_LIMIT;
  const showAll = showAllKinds.has(g.kind);
  const cards = g.nodes.map((n, i) => {
    const hidden = !showAll && i >= COLLAPSE_LIMIT;
    return cardHTML(n).replace('<article ', hidden ? '<article hidden ' : '<article ');
  }).join('');
  const moreBtn = overflow > 0
    ? `<button class="gal-more" data-more="${g.kind}" type="button" ${showAll ? 'hidden' : ''}>
         <span class="gal-more-num">+${overflow}</span>
         <span class="gal-more-label">展开剩余</span>
       </button>`
    : '';
  return `
    <section class="gal-group" style="--group-color:${color}">
      <header class="gal-group-head">
        <span class="gal-group-dot"></span>
        <h2 class="gal-group-name">${KIND_LABELS[g.kind] || g.kind}</h2>
        <span class="gal-group-count">${g.nodes.length}</span>
      </header>
      <div class="gal-grid">
        ${cards}
        ${moreBtn}
      </div>
    </section>`;
}

function renderGroups() {
  const groups = buildKindGroups();
  const box = root.querySelector('[data-groups]');
  box.innerHTML = groups.map(groupHTML).join('');
}

function bindGrid() {
  const box = root.querySelector('[data-groups]');
  box.addEventListener('click', (e) => {
    // 展开剩余
    const more = e.target.closest('[data-more]');
    if (more) {
      const kind = more.dataset.more;
      showAllKinds.add(kind);
      renderGroups();
      return;
    }
    // 卡片点击 → 详情
    const card = e.target.closest('[data-id]');
    if (card) actions.openDetail(card.dataset.id);
  });
  // 键盘可达
  box.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const card = e.target.closest('[data-id]');
    if (card) { e.preventDefault(); actions.openDetail(card.dataset.id); }
  });
}

const ERROR_HTML = `
  <div class="placeholder">
    <div class="panel placeholder-card">
      <h1 class="placeholder-title">卡片画廊</h1>
      <div class="placeholder-sub">数据加载失败</div>
      <div class="placeholder-hint">无法读取 <code>window.__EK_GRAPH</code>，请通过 <code>http://localhost:5188</code> 访问。</div>
    </div>
  </div>`;

export function mount(container) {
  root = container;
  root.classList.add('view-gallery');

  graph = loadGraph();
  if (!graph) {
    root.innerHTML = ERROR_HTML;
    return;
  }
  index = buildIndex(graph);

  root.innerHTML = `
    <div class="gal-shell">
      ${headerHTML()}
      <div class="gal-groups" data-groups></div>
    </div>`;
  renderGroups();
  bindGrid();
}

export function unmount() {
  if (root) {
    root.innerHTML = '';
    root.classList.remove('view-gallery');
  }
  root = null;
  graph = null;
  index = null;
  showAllKinds.clear();
}
