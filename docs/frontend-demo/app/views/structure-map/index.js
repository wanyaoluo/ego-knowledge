// views/structure-map/index.js · 结构地图（Phase 3 · 全库组织总览）
// domain 层级树（像 wiki 目录）：每个 domain 一分组，可展开/折叠，展开见该域节点列表。
// 数据驱动（不写死 domain 数）：按 domainCounts 降序，_unsorted 永远末位标"未归类"。
// 点节点 → actions.openDetail 跳详情。复用 lib/data/* + lib/store + lib/actions。
// CSS 在 index.html 用 <link> 引入（无 build 环境 ES module 不支持 import css）。
import {
  KIND_COLORS, KIND_LABELS,
  loadGraph, buildIndex,
} from '../../lib/data-layer.js';
import { escapeHtml } from '../../lib/escape.js';
import * as actions from '../../lib/actions.js';

let root = null;
let graph = null;
let index = null;
let groups = [];                 // buildDomainGroups 产物
const expanded = new Set();      // 展开的 domain 名集合

const domainLabel = (d) => (d === '_unsorted' ? '未归类' : d.replace(/-/g, ' '));

/** 按 domain 分组节点，组内按 authority 降序；_unsorted 永远末位。 */
function buildDomainGroups() {
  const map = {};
  graph.nodes.forEach((n) => {
    const d = n.domain || '_unsorted';
    (map[d] = map[d] || []).push(n);
  });
  return Object.entries(map)
    .map(([domain, nodes]) => ({
      domain,
      nodes: nodes.sort((a, b) => (Number(b.authority) || 0) - (Number(a.authority) || 0)),
    }))
    .sort((a, b) => {
      if (a.domain === '_unsorted') return 1;
      if (b.domain === '_unsorted') return -1;
      return b.nodes.length - a.nodes.length;
    });
}

/** 某分组内 kind 构成迷你光谱条（一眼看出该域是什么类型为主）。 */
function groupKindBandHTML(nodes) {
  const counts = {};
  nodes.forEach((n) => { counts[n.kind] = (counts[n.kind] || 0) + 1; });
  const total = nodes.length;
  const segs = Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => {
      const w = ((v / total) * 100).toFixed(2);
      const color = KIND_COLORS[k] || 'var(--kind-view)';
      const label = KIND_LABELS[k] || k;
      return `<span class="sm-band-seg" style="flex:0 0 ${w}%;background:${color}" title="${escapeHtml(label)} · ${v}"></span>`;
    }).join('');
  return `<span class="sm-band" aria-hidden="true">${segs}</span>`;
}

function headerHTML() {
  return `
    <header class="sm-header panel">
      <div class="sm-header-main">
        <h1 class="sm-title">结构地图</h1>
        <p class="sm-sub">按领域分层 · 全库 <b>${graph.nodes.length}</b> 条知识 · <b>${groups.length}</b> 个领域</p>
      </div>
      <div class="sm-header-actions">
        <button class="btn btn-ghost sm-expand-all" data-expand-all type="button">全部展开</button>
        <button class="btn btn-ghost sm-collapse-all" data-collapse-all type="button">全部折叠</button>
      </div>
    </header>`;
}

function groupHTML(g) {
  const isOpen = expanded.has(g.domain);
  const band = groupKindBandHTML(g.nodes);
  const nodes = g.nodes.map((n) => {
    const color = KIND_COLORS[n.kind] || '#897989';
    const label = KIND_LABELS[n.kind] || n.kind;
    return `
      <li class="sm-node" data-id="${escapeHtml(n.id)}" role="button" tabindex="0">
        <span class="sm-node-dot" style="background:${color}"></span>
        <span class="sm-node-title">${escapeHtml(n.title)}</span>
        <span class="sm-node-kind" style="color:${color};border-color:${color}">${label}</span>
      </li>`;
  }).join('');
  return `
    <section class="sm-group${isOpen ? ' open' : ''}${g.domain === '_unsorted' ? ' is-unsorted' : ''}" data-domain="${escapeHtml(g.domain)}">
      <button class="sm-group-head" data-toggle type="button" aria-expanded="${isOpen}">
        <span class="sm-caret" aria-hidden="true">▸</span>
        <span class="sm-group-name">${escapeHtml(domainLabel(g.domain))}</span>
        ${band}
        <span class="sm-group-count">${g.nodes.length}</span>
      </button>
      <div class="sm-group-body">
        <ul class="sm-nodes">${nodes}</ul>
      </div>
    </section>`;
}

function renderTree() {
  const tree = root.querySelector('[data-tree]');
  tree.innerHTML = groups.map(groupHTML).join('');
}

/** 刷新所有分组的展开态（toggle 后调用，不重渲列表，仅切 class）。 */
function syncExpanded() {
  root.querySelectorAll('.sm-group').forEach((el) => {
    const d = el.dataset.domain;
    const open = expanded.has(d);
    el.classList.toggle('open', open);
    const head = el.querySelector('[data-toggle]');
    if (head) head.setAttribute('aria-expanded', String(open));
  });
}

function bindTree() {
  // 分组头部 toggle
  root.querySelector('[data-tree]').addEventListener('click', (e) => {
    const head = e.target.closest('[data-toggle]');
    if (head) {
      const groupEl = head.closest('.sm-group');
      const d = groupEl && groupEl.dataset.domain;
      if (d) {
        if (expanded.has(d)) expanded.delete(d);
        else expanded.add(d);
        syncExpanded();
      }
      return;
    }
    // 节点点击 → 跳详情
    const node = e.target.closest('[data-id]');
    if (node) {
      actions.openDetail(node.dataset.id);
    }
  });
  // 键盘：节点 Enter/Space 触发
  root.querySelector('[data-tree]').addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const node = e.target.closest('[data-id]');
    if (node) {
      e.preventDefault();
      actions.openDetail(node.dataset.id);
    }
  });

  root.querySelector('[data-expand-all]').addEventListener('click', () => {
    groups.forEach((g) => expanded.add(g.domain));
    syncExpanded();
  });
  root.querySelector('[data-collapse-all]').addEventListener('click', () => {
    expanded.clear();
    syncExpanded();
  });
}

const ERROR_HTML = `
  <div class="placeholder">
    <div class="panel placeholder-card">
      <h1 class="placeholder-title">结构地图</h1>
      <div class="placeholder-sub">数据加载失败</div>
      <div class="placeholder-hint">无法读取 <code>window.__EK_GRAPH</code>，请通过 <code>http://localhost:5188</code> 访问。</div>
    </div>
  </div>`;

export function mount(container) {
  root = container;
  root.classList.add('view-structure-map');

  graph = loadGraph();
  if (!graph) {
    root.innerHTML = ERROR_HTML;
    return;
  }
  index = buildIndex(graph);
  groups = buildDomainGroups();

  // 默认展开非 _unsorted 的前 3 个（让用户一进来就看到结构化内容，_unsorted 体量大默认折叠）
  groups.slice(0, 3).forEach((g) => { if (g.domain !== '_unsorted') expanded.add(g.domain); });

  root.innerHTML = `
    <div class="sm-shell">
      ${headerHTML()}
      <div class="sm-tree" data-tree role="tree" aria-label="领域结构树"></div>
    </div>`;
  renderTree();
  bindTree();
}

export function unmount() {
  if (root) {
    root.innerHTML = '';
    root.classList.remove('view-structure-map');
  }
  root = null;
  graph = null;
  index = null;
  groups = [];
  expanded.clear();
}
