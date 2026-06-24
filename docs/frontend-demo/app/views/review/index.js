// views/review/index.js · 复核队列（Phase 4 · 待办清单视图）
// 数据缺 review_due / maintenance_queue → 代理划分三段队列：
//   ① 待沉淀（status=draft）——草稿未成型，琥珀 · 需补全
//   ② 待复核（freshness=volatile）——高频变动，樱粉 · 紧迫焦点
//   ③ 待观察（freshness=watch）——维护巡检，紫 · 量大默认折叠
// 同一节点可同时落入多队列（按不同维度划分），hint 已注明。
// 卡片式列表，点跳详情。复用 lib/data/* + lib/store + lib/actions。
// CSS 在 index.html 用 <link> 引入。
import {
  KIND_COLORS, KIND_LABELS, FRESHNESS_LABELS, FRESHNESS_COLORS,
  STATUS_LABELS,
  loadGraph, buildIndex, authPct,
} from '../../lib/data-layer.js';
import { escapeHtml } from '../../lib/escape.js';
import * as actions from '../../lib/actions.js';

// 三段队列定义：id / 标题 / 副标题 / 主色 / 取数谓词 / 折叠阈值
const QUEUES = [
  {
    id: 'draft',
    title: '待沉淀',
    hint: '草稿未成型 · 需补全正文与关系',
    color: '#e6b885',          // 琥珀 · attention
    pick: (n) => n.status === 'draft',
    collapse: 999,             // draft 量小，全展
  },
  {
    id: 'volatile',
    title: '待复核',
    hint: '高频变动 · 内容可能已过时，需复核',
    color: '#e89bbb',          // 樱粉 · 焦点/紧迫
    pick: (n) => n.freshness === 'volatile',
    collapse: 999,
  },
  {
    id: 'watch',
    title: '待观察',
    hint: '观察期维护队列 · 量大，建议定期巡检',
    color: '#b59ee6',          // 紫 · 中性维护
    pick: (n) => n.freshness === 'watch',
    collapse: 16,
  },
];

let root = null;
let graph = null;
let index = null;
const expanded = new Set();    // 已展开的队列 id

const domainLabel = (d) => (!d || d === '_unsorted' ? '未归类' : d.replace(/-/g, ' '));

/** 组内排序：authority 降序（重要的排前），title 升序兜底。 */
function sortQueue(nodes) {
  return [...nodes].sort((a, b) => {
    const da = (Number(b.authority) || 0) - (Number(a.authority) || 0);
    if (da !== 0) return da;
    return (a.title || '').localeCompare(b.title || '', 'zh');
  });
}

function buildQueues() {
  return QUEUES.map((q) => ({
    ...q,
    nodes: sortQueue(graph.nodes.filter(q.pick)),
  })).filter((q) => q.nodes.length);
}

function headerHTML() {
  const total = graph.nodes.length;
  const pending = QUEUES.reduce((sum, q) => sum + graph.nodes.filter(q.pick).length, 0);
  return `
    <header class="rv-header panel">
      <div class="rv-header-main">
        <h1 class="rv-title">复核队列</h1>
        <p class="rv-sub">按治理维度排队 · 共 <b>${pending}</b> 条待办（全库 <b>${total}</b> 条 · 同一节点可入多队）</p>
      </div>
      <div class="rv-header-legend">
        ${QUEUES.map((q) => `
          <span class="rv-legend-item" style="--q-color:${q.color}">
            <span class="rv-legend-dot"></span>
            <span class="rv-legend-name">${q.title}</span>
            <b class="rv-legend-count">${graph.nodes.filter(q.pick).length}</b>
          </span>`).join('')}
      </div>
    </header>`;
}

function cardHTML(node) {
  const kindColor = KIND_COLORS[node.kind] || '#897989';
  const pctVal = authPct(Number(node.authority) || 0).toFixed(0);
  const statusLabel = STATUS_LABELS[node.status] || node.status || '—';
  const freshLabel = FRESHNESS_LABELS[node.freshness] || node.freshness || '—';
  const freshColor = FRESHNESS_COLORS[node.freshness] || '#897989';
  return `
    <article class="rv-card" data-id="${escapeHtml(node.id)}" role="button" tabindex="0"
             style="--kind-color:${kindColor};--fresh-color:${freshColor}"
             aria-label="查看详情：${escapeHtml(node.title)}">
      <div class="rv-card-stripe"></div>
      <div class="rv-card-body">
        <div class="rv-card-head">
          <span class="rv-kind">${KIND_LABELS[node.kind] || node.kind}</span>
          <span class="rv-tag rv-tag-status">${escapeHtml(statusLabel)}</span>
          <span class="rv-tag rv-tag-fresh">${escapeHtml(freshLabel)}</span>
        </div>
        <h3 class="rv-card-title">${escapeHtml(node.title)}</h3>
        <div class="rv-card-foot">
          <span class="rv-card-domain">${escapeHtml(domainLabel(node.domain))}</span>
          <span class="rv-auth">
            <span class="rv-auth-bar"><span class="rv-auth-fill" style="width:${pctVal}%"></span></span>
            <span class="rv-auth-num">${pctVal}</span>
          </span>
        </div>
      </div>
    </article>`;
}

function queueHTML(q) {
  const isOpen = expanded.has(q.id);
  const limit = q.collapse;
  const overflow = q.nodes.length - limit;
  const visible = overflow > 0 && !isOpen ? q.nodes.slice(0, limit) : q.nodes;
  const cards = visible.map(cardHTML).join('');
  const moreBtn = overflow > 0
    ? `<button class="rv-more" data-more="${q.id}" type="button">
         ${isOpen
           ? `<span class="rv-more-label">收起</span>`
           : `<span class="rv-more-num">+${overflow}</span><span class="rv-more-label">展开剩余</span>`}
       </button>`
    : '';
  return `
    <section class="rv-queue" style="--q-color:${q.color}">
      <header class="rv-queue-head">
        <span class="rv-queue-dot"></span>
        <div class="rv-queue-text">
          <h2 class="rv-queue-name">${q.title}</h2>
          <span class="rv-queue-hint">${q.hint}</span>
        </div>
        <span class="rv-queue-count">${q.nodes.length}</span>
      </header>
      <div class="rv-grid">
        ${cards}
        ${moreBtn}
      </div>
    </section>`;
}

function renderQueues() {
  const box = root.querySelector('[data-queues]');
  const queues = buildQueues();
  if (!queues.length) {
    box.innerHTML = `<div class="rv-empty muted">队列清空 · 暂无待办</div>`;
    return;
  }
  box.innerHTML = queues.map(queueHTML).join('');
}

function bind() {
  const box = root.querySelector('[data-queues]');
  box.addEventListener('click', (e) => {
    const more = e.target.closest('[data-more]');
    if (more) {
      const id = more.dataset.more;
      if (expanded.has(id)) expanded.delete(id);
      else expanded.add(id);
      renderQueues();
      return;
    }
    const card = e.target.closest('[data-id]');
    if (card) actions.openDetail(card.dataset.id);
  });
  box.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const card = e.target.closest('[data-id]');
    if (card) { e.preventDefault(); actions.openDetail(card.dataset.id); }
  });
}

const ERROR_HTML = `
  <div class="placeholder">
    <div class="panel placeholder-card">
      <h1 class="placeholder-title">复核队列</h1>
      <div class="placeholder-sub">数据加载失败</div>
      <div class="placeholder-hint">无法读取 <code>window.__EK_GRAPH</code>，请通过 <code>http://localhost:5188</code> 访问。</div>
    </div>
  </div>`;

export function mount(container) {
  root = container;
  root.classList.add('view-review');

  graph = loadGraph();
  if (!graph) {
    root.innerHTML = ERROR_HTML;
    return;
  }
  index = buildIndex(graph);

  root.innerHTML = `
    <div class="rv-shell">
      ${headerHTML()}
      <div class="rv-queues" data-queues></div>
    </div>`;
  renderQueues();
  bind();
}

export function unmount() {
  if (root) {
    root.innerHTML = '';
    root.classList.remove('view-review');
  }
  root = null;
  graph = null;
  index = null;
  expanded.clear();
}
