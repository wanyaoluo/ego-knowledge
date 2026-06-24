// views/reader/index.js · 双栏阅读（Phase 3 · Obsidian 式沉浸阅读）
// 左栏：节点列表（kind/domain 筛选 + 排序）+ 选中高亮
// 右栏：选中节点正文（markdown 渲染，复用 lib/markdown.js）+ 反链（引用该节点的边/关系）
// 反链点击 → reader 内切换选中（不跳走，保持阅读流）；右栏 head 提供"深读打开"入口跳 detail。
// 复用 lib/data/* + lib/markdown + lib/store + lib/actions。
// CSS 在 index.html 用 <link> 引入（无 build 环境不支持 import css）。
import {
  KIND_COLORS, KIND_LABELS, EDGE_COLORS, EDGE_STYLE, EDGE_LABELS,
  STATUS_LABELS, FRESHNESS_LABELS,
  loadGraph, buildIndex, getNodeById, authPct, trunc,
} from '../../lib/data-layer.js';
import { escapeHtml } from '../../lib/escape.js';
import { simpleMarkdownToHtml, loadMarked } from '../../lib/markdown.js';
import * as actions from '../../lib/actions.js';

let root = null;
let graph = null;
let index = null;
let idToNode = new Map();
let selectedId = null;
let mountSession = 0;            // 防 loadMarked 异步回调跨 mount 误执行

// 视图本地态
const local = {
  filters: { kinds: {}, domains: {} },
  sortBy: 'authority',           // authority | title
};

const SORT_OPTIONS = [
  { id: 'authority', label: '权威度' },
  { id: 'title', label: '标题' },
];

const domainLabel = (d) => (!d || d === '_unsorted' ? '未归类' : d.replace(/-/g, ' '));
const isAllOn = (m) => !m || Object.keys(m).length === 0;

// ===== 筛选 + 排序 =====
function getVisibleNodes() {
  const f = local.filters;
  return graph.nodes
    .filter((n) => {
      if (!isAllOn(f.kinds) && !f.kinds[n.kind]) return false;
      const dom = n.domain || '_unsorted';
      if (!isAllOn(f.domains) && !f.domains[dom]) return false;
      return true;
    })
    .sort((a, b) => {
      if (local.sortBy === 'title') return (a.title || '').localeCompare(b.title || '', 'zh');
      return (Number(b.authority) || 0) - (Number(a.authority) || 0);
    });
}

// ===== 骨架 =====
const SHELL_HTML = `
  <div class="reader-shell">
    <aside class="panel reader-aside">
      <div class="reader-controls">
        <div class="reader-ctrl-group">
          <span class="reader-ctrl-label">类型</span>
          <div class="reader-chips" data-filter="kinds"></div>
        </div>
        <div class="reader-ctrl-group">
          <span class="reader-ctrl-label">领域</span>
          <select class="reader-domain-select" data-filter="domains" multiple size="5"
                  aria-label="按领域筛选（按住 Ctrl/Cmd 多选）"></select>
        </div>
        <div class="reader-ctrl-group">
          <span class="reader-ctrl-label">排序</span>
          <div class="reader-sort" role="radiogroup" aria-label="列表排序" data-sort-group></div>
        </div>
        <button class="btn btn-ghost reader-reset" data-reset type="button">清空筛选</button>
      </div>
      <div class="reader-list" data-list role="listbox" aria-label="节点列表"></div>
    </aside>
    <section class="panel reader-main" data-main></section>
  </div>`;

const ERROR_HTML = `
  <div class="placeholder">
    <div class="panel placeholder-card">
      <h1 class="placeholder-title">双栏阅读</h1>
      <div class="placeholder-sub">数据加载失败</div>
      <div class="placeholder-hint">无法读取 <code>window.__EK_GRAPH</code>，请通过 <code>http://localhost:5188</code> 访问。</div>
    </div>
  </div>`;

// ===== 渲染：筛选控件 =====
function buildChip(filterKey, value, label, color) {
  const chip = document.createElement('div');
  const active = !!local.filters[filterKey][value];
  chip.className = 'reader-chip' + (active ? ' active' : '');
  chip.setAttribute('role', 'checkbox');
  chip.setAttribute('aria-checked', String(active));
  chip.setAttribute('tabindex', '0');
  chip.style.setProperty('--chip-color', color || 'var(--text-tertiary)');
  chip.innerHTML = `<span class="reader-chip-dot"></span><span>${escapeHtml(label)}</span>`;
  const toggle = () => {
    const cur = local.filters[filterKey];
    if (cur[value]) {
      const next = { ...cur };
      delete next[value];
      local.filters[filterKey] = next;
    } else {
      local.filters[filterKey] = { ...cur, [value]: true };
    }
    renderFilterOptions();
    renderList();
  };
  chip.addEventListener('click', toggle);
  chip.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
  });
  return chip;
}

function renderFilterOptions() {
  const kindsBox = root.querySelector('[data-filter="kinds"]');
  kindsBox.innerHTML = '';
  Object.keys(KIND_COLORS).forEach((k) => {
    if (!index.kindCounts[k]) return;
    kindsBox.appendChild(buildChip('kinds', k, KIND_LABELS[k] || k, KIND_COLORS[k]));
  });

  const sel = root.querySelector('[data-filter="domains"]');
  // 领域 select 选项不随选中态重渲（避免丢失焦点）；仅在首次或数据变化时填
  if (!sel.options.length) {
    Object.keys(index.domainCounts)
      .sort((a, b) => index.domainCounts[b] - index.domainCounts[a])
      .forEach((dom) => {
        const opt = document.createElement('option');
        opt.value = dom;
        opt.textContent = dom === '_unsorted' ? '未归类' : `${domainLabel(dom)} (${index.domainCounts[dom]})`;
        sel.appendChild(opt);
      });
  }
}

function renderSort() {
  const group = root.querySelector('[data-sort-group]');
  group.innerHTML = '';
  SORT_OPTIONS.forEach((opt) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'reader-sort-btn' + (local.sortBy === opt.id ? ' active' : '');
    b.setAttribute('role', 'radio');
    b.setAttribute('aria-checked', String(local.sortBy === opt.id));
    b.textContent = opt.label;
    b.addEventListener('click', () => {
      if (local.sortBy === opt.id) return;
      local.sortBy = opt.id;
      renderSort();
      renderList();
    });
    group.appendChild(b);
  });
}

// ===== 渲染：左栏列表 =====
function renderList() {
  const box = root.querySelector('[data-list]');
  const nodes = getVisibleNodes();
  if (!nodes.length) {
    box.innerHTML = `<div class="reader-list-empty muted">当前筛选无结果</div>`;
    return;
  }
  box.innerHTML = nodes.map((n) => {
    const color = KIND_COLORS[n.kind] || '#897989';
    const active = n.id === selectedId;
    const pctVal = authPct(Number(n.authority) || 0).toFixed(0);
    return `
      <button class="reader-item${active ? ' active' : ''}" data-id="${escapeHtml(n.id)}"
              type="button" role="option" aria-selected="${active}">
        <span class="reader-item-dot" style="background:${color}"></span>
        <span class="reader-item-main">
          <span class="reader-item-title">${escapeHtml(trunc(n.title, 26))}</span>
          <span class="reader-item-meta">
            <span class="reader-item-domain">${escapeHtml(domainLabel(n.domain))}</span>
            <span class="reader-item-auth">${pctVal}</span>
          </span>
        </span>
      </button>`;
  }).join('');
}

/** 切换选中：仅同步高亮（不重渲列表，保留滚动位置）；若选中项不在列表则不高亮。 */
function syncHighlight() {
  root.querySelectorAll('.reader-item').forEach((el) => {
    const active = el.dataset.id === selectedId;
    el.classList.toggle('active', active);
    el.setAttribute('aria-selected', String(active));
  });
}

// ===== 渲染：右栏正文 =====
function renderProse(node) {
  const el = root.querySelector('[data-prose]');
  if (!el) return;
  if (node.body && node.body.trim()) {
    el.innerHTML = simpleMarkdownToHtml(node.body);
    const session = mountSession;
    loadMarked().then((ok) => {
      if (!ok || session !== mountSession || !root) return;
      const el2 = root.querySelector('[data-prose]');
      if (el2 && window.marked) {
        try { el2.innerHTML = window.marked.parse(node.body); }
        catch (e) { console.warn('[reader] marked.parse 异常，保留简易解析', e); }
      }
    });
  } else {
    const terms = node.search_terms || node.slug || '';
    const dom = node.domain && node.domain !== '_unsorted' ? node.domain : '未归类';
    el.innerHTML = `
      <p class="prose-lead">${escapeHtml(node.title)}</p>
      <p>所属域：<code>${escapeHtml(dom)}</code></p>
      ${terms ? `<p>检索词：<code>${escapeHtml(terms)}</code></p>` : ''}
      <p class="prose-hint muted">该条目暂无正文 body 字段。</p>`;
  }
}

/** 反链 + 外链：入边（谁指向我）/ 出边（我指向谁），按 type 分组，点击 reader 内切换。 */
function renderBacklinks(node) {
  const el = root.querySelector('[data-backlinks]');
  const all = index.edgeIndex.get(node.id) || [];
  const inEdges = all.filter((e) => e.target === node.id);
  const outEdges = all.filter((e) => e.source === node.id);

  const groupList = (edges, dir) => {
    if (!edges.length) return `<div class="reader-bl-empty muted">无</div>`;
    const groups = {};
    edges.forEach((e) => { (groups[e.type] = groups[e.type] || []).push(e); });
    return Object.keys(groups).map((type) => {
      const color = EDGE_COLORS[type] || '#897989';
      const lineStyle = EDGE_STYLE[type] || 'solid';
      const items = groups[type].map((e) => {
        const otherId = e.source === node.id ? e.target : e.source;
        const other = getNodeById(graph, otherId);
        const arrow = dir === 'out' ? '→' : '←';
        return `
          <li class="reader-bl-item" data-bl="${escapeHtml(otherId)}" role="button" tabindex="0">
            <span class="reader-bl-arrow">${arrow}</span>
            <span class="reader-bl-dot" style="background:${KIND_COLORS[other ? other.kind : ''] || '#897989'}"></span>
            <span class="reader-bl-title">${other ? escapeHtml(trunc(other.title, 22)) : escapeHtml(otherId)}</span>
          </li>`;
      }).join('');
      return `
        <div class="reader-bl-group">
          <div class="reader-bl-head">
            <span class="reader-bl-line ${lineStyle}" style="border-color:${color}"></span>
            <span class="reader-bl-type">${EDGE_LABELS[type] || type}</span>
            <span class="reader-bl-count muted">${groups[type].length}</span>
          </div>
          <ul class="reader-bl-items">${items}</ul>
        </div>`;
    }).join('');
  };

  el.innerHTML = `
    <div class="reader-bl-section">
      <div class="reader-bl-section-title">反链 · 谁引用了它 <span class="muted">${inEdges.length}</span></div>
      ${groupList(inEdges, 'in')}
    </div>
    <div class="reader-bl-section">
      <div class="reader-bl-section-title">外链 · 它引用了谁 <span class="muted">${outEdges.length}</span></div>
      ${groupList(outEdges, 'out')}
    </div>`;
}

function renderMain(node) {
  const el = root.querySelector('[data-main]');
  const kindColor = KIND_COLORS[node.kind] || '#897989';
  el.innerHTML = `
    <header class="reader-head">
      <div class="reader-head-tags">
        <span class="reader-kind" style="color:${kindColor};border-color:${kindColor}">${KIND_LABELS[node.kind] || node.kind}</span>
        ${node.domain && node.domain !== '_unsorted'
          ? `<span class="tag">${escapeHtml(domainLabel(node.domain))}</span>`
          : `<span class="tag tag-muted">未归类</span>`}
        <span class="tag">${STATUS_LABELS[node.status] || node.status || '—'}</span>
        <span class="tag">${FRESHNESS_LABELS[node.freshness] || node.freshness || '—'}</span>
        <button class="btn btn-ghost reader-deep" data-deep type="button" title="在深读视图中打开（完整正文+关系子图+生命力）">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 17L17 7M17 7H9M17 7v8"/></svg>
          <span>深读</span>
        </button>
      </div>
      <h2 class="reader-head-title">${escapeHtml(node.title)}</h2>
    </header>
    <article class="prose" data-prose></article>
    <section class="reader-backlinks" data-backlinks></section>`;
  renderProse(node);
  renderBacklinks(node);
}

// ===== 选中切换（reader 内，保持沉浸） =====
function selectAndRender(id) {
  const node = id ? getNodeById(graph, id) : null;
  if (!node) return;
  selectedId = id;
  renderMain(node);
  syncHighlight();
  // 选中项滚入视野（若在列表中）
  const item = root.querySelector(`.reader-item[data-id="${CSS.escape(id)}"]`);
  if (item) item.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

// ===== 事件绑定 =====
function bind() {
  // 列表点击
  root.querySelector('[data-list]').addEventListener('click', (e) => {
    const item = e.target.closest('[data-id]');
    if (item) selectAndRender(item.dataset.id);
  });
  // 反链点击 → reader 内切换
  root.querySelector('[data-main]').addEventListener('click', (e) => {
    const deep = e.target.closest('[data-deep]');
    if (deep && selectedId) { actions.openDetail(selectedId); return; }
    const bl = e.target.closest('[data-bl]');
    if (bl) selectAndRender(bl.dataset.bl);
  });
  // 键盘：反链 Enter/Space
  root.querySelector('[data-main]').addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const bl = e.target.closest('[data-bl]');
    if (bl) { e.preventDefault(); selectAndRender(bl.dataset.bl); }
  });
  // 领域 select
  const sel = root.querySelector('[data-filter="domains"]');
  sel.addEventListener('change', () => {
    const picked = {};
    Array.from(sel.selectedOptions).forEach((o) => { picked[o.value] = true; });
    local.filters.domains = picked;
    renderList();
  });
  // 清空筛选
  root.querySelector('[data-reset]').addEventListener('click', () => {
    local.filters = { kinds: {}, domains: {} };
    Array.from(sel.options).forEach((o) => { o.selected = false; });
    renderFilterOptions();
    renderList();
  });
}

// ===== 挂载 / 卸载 =====
export function mount(container) {
  root = container;
  root.classList.add('view-reader');
  mountSession++;

  graph = loadGraph();
  if (!graph) {
    root.innerHTML = ERROR_HTML;
    return;
  }
  index = buildIndex(graph);
  idToNode = new Map(graph.nodes.map((n) => [n.id, n]));

  root.innerHTML = SHELL_HTML;
  renderFilterOptions();
  renderSort();
  bind();
  renderList();

  // 默认选中 authority 最高的节点
  const top = [...graph.nodes].sort((a, b) => (Number(b.authority) || 0) - (Number(a.authority) || 0))[0];
  if (top) selectAndRender(top.id);
}

export function unmount() {
  if (root) {
    root.innerHTML = '';
    root.classList.remove('view-reader');
  }
  root = null;
  graph = null;
  index = null;
  idToNode = new Map();
  selectedId = null;
  local.filters = { kinds: {}, domains: {} };
  local.sortBy = 'authority';
}
