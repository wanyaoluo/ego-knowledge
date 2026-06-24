// views/explore/index.js · 知识漫步（Phase 4 · StumbleUpon 式随机探索）
// 关系驱动随机：候选池 = 有关系（degree>0）的节点，随机抽一个展示大卡片。
//   孤立节点无关系网络，探索体验差，排除出候选池；候选池为空（全孤立）时回退全库。
// 大卡片：title / kind / domain / authority / 关系摘要（入出边按 type 分组）/ 正文片段。
// "换一个"按钮随机下一个（排除上一个，避免连抽重复）；卡片点正文区跳 detail。
// 复用 lib/data/* + lib/store + lib/actions。CSS 在 index.html 用 <link> 引入。
import {
  KIND_COLORS, KIND_LABELS, EDGE_COLORS, EDGE_STYLE, EDGE_LABELS,
  FRESHNESS_LABELS, FRESHNESS_COLORS, STATUS_LABELS,
  loadGraph, buildIndex, getNodeById, authPct, trunc,
} from '../../lib/data-layer.js';
import { escapeHtml } from '../../lib/escape.js';
import * as actions from '../../lib/actions.js';

let root = null;
let graph = null;
let index = null;
let candidates = [];        // 候选池（degree>0 节点）
let currentId = null;       // 当前展示节点 id

const EXCERPT_LEN = 220;    // 正文片段截取长度
const REL_PER_TYPE = 2;     // 每类关系最多展示几个邻居

const domainLabel = (d) => (!d || d === '_unsorted' ? '未归类' : d.replace(/-/g, ' '));

/** 剥离 markdown 标记，取纯文本片段（用于卡片预览，不做完整渲染）。 */
function excerpt(md, len = EXCERPT_LEN) {
  if (!md) return '';
  const text = String(md)
    .replace(/```[\s\S]*?```/g, '〔代码块〕')       // 代码块占位
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')           // 图片
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')        // 链接保留文字
    .replace(/^#{1,6}\s+/gm, '')                    // 标题
    .replace(/^\s*[-*+]\s+/gm, '')                  // 无序列表符
    .replace(/^\s*\d+\.\s+/gm, '')                  // 有序列表符
    .replace(/\*\*([^*]+)\*\*/g, '$1')              // 加粗
    .replace(/`([^`]+)`/g, '$1')                    // 行内 code
    .replace(/>\s+/gm, '')                           // 引用
    .replace(/\n{2,}/g, '\n')
    .replace(/\n/g, ' ')
    .trim();
  return text.length > len ? text.slice(0, len).trimEnd() + '…' : text;
}

/** 取关系摘要：入边/出边按 type 分组，每类取前 N 邻居标题。 */
function buildRelSummary(node) {
  const all = index.edgeIndex.get(node.id) || [];
  const inEdges = all.filter((e) => e.target === node.id);
  const outEdges = all.filter((e) => e.source === node.id);
  const groupBy = (edges, dir) => {
    const groups = {};
    edges.forEach((e) => { (groups[e.type] = groups[e.type] || []).push(e); });
    return Object.keys(groups).map((type) => {
      const color = EDGE_COLORS[type] || '#897989';
      const lineStyle = EDGE_STYLE[type] || 'solid';
      const neighbors = groups[type].slice(0, REL_PER_TYPE).map((e) => {
        const otherId = e.source === node.id ? e.target : e.source;
        const other = getNodeById(graph, otherId);
        const arrow = dir === 'out' ? '→' : '←';
        return { arrow, id: otherId, title: other ? other.title : otherId, kind: other ? other.kind : '' };
      });
      return { type, color, lineStyle, total: groups[type].length, neighbors };
    });
  };
  return { inGroups: groupBy(inEdges, 'in'), outGroups: groupBy(outEdges, 'out'),
           inCount: inEdges.length, outCount: outEdges.length };
}

function relSectionHTML(groups, dirLabel, total) {
  if (!groups.length) {
    return `
      <div class="ex-rel-section">
        <div class="ex-rel-title">${dirLabel} <span class="muted">0</span></div>
        <div class="ex-rel-empty muted">无</div>
      </div>`;
  }
  const items = groups.map((g) => {
    const neighborHTML = g.neighbors.map((nb) => `
      <li class="ex-rel-item" data-rel="${escapeHtml(nb.id)}" role="button" tabindex="0">
        <span class="ex-rel-arrow">${nb.arrow}</span>
        <span class="ex-rel-dot" style="background:${KIND_COLORS[nb.kind] || '#897989'}"></span>
        <span class="ex-rel-name">${escapeHtml(trunc(nb.title, 24))}</span>
      </li>`).join('');
    const more = g.total > g.neighbors.length ? `<span class="ex-rel-more muted">+${g.total - g.neighbors.length}</span>` : '';
    return `
      <div class="ex-rel-group">
        <div class="ex-rel-head">
          <span class="ex-rel-line ${g.lineStyle}" style="border-color:${g.color}"></span>
          <span class="ex-rel-type" style="color:${g.color}">${EDGE_LABELS[g.type] || g.type}</span>
          <span class="ex-rel-count muted">${g.total}</span>
        </div>
        <ul class="ex-rel-items">${neighborHTML}${more}</ul>
      </div>`;
  }).join('');
  return `
    <div class="ex-rel-section">
      <div class="ex-rel-title">${dirLabel} <span class="muted">${total}</span></div>
      ${items}
    </div>`;
}

function renderCard(node) {
  const kindColor = KIND_COLORS[node.kind] || '#897989';
  const freshColor = FRESHNESS_COLORS[node.freshness] || '#897989';
  const pctVal = authPct(Number(node.authority) || 0).toFixed(0);
  const kindLabel = KIND_LABELS[node.kind] || node.kind;
  const statusLabel = STATUS_LABELS[node.status] || node.status || '—';
  const freshLabel = FRESHNESS_LABELS[node.freshness] || node.freshness || '—';
  const rel = buildRelSummary(node);
  const preview = excerpt(node.body);
  const stage = root.querySelector('[data-stage]');

  stage.innerHTML = `
    <article class="ex-card" style="--kind-color:${kindColor};--fresh-color:${freshColor}"
             data-id="${escapeHtml(node.id)}" role="button" tabindex="0"
             aria-label="查看详情：${escapeHtml(node.title)}">
      <header class="ex-card-head">
        <div class="ex-card-tags">
          <span class="ex-kind">${kindLabel}</span>
          ${node.domain && node.domain !== '_unsorted'
            ? `<span class="tag">${escapeHtml(domainLabel(node.domain))}</span>`
            : `<span class="tag tag-muted">未归类</span>`}
          <span class="tag">${escapeHtml(statusLabel)}</span>
          <span class="tag ex-tag-fresh">${escapeHtml(freshLabel)}</span>
        </div>
        <div class="ex-card-actions">
          <button class="btn btn-ghost ex-reroll" data-reroll type="button" title="随机换一个">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M3 8a9 9 0 0 1 14.5-3M21 4v4h-4"/>
              <path d="M21 16a9 9 0 0 1-14.5 3M3 20v-4h4"/>
            </svg>
            <span>换一个</span>
          </button>
          <button class="btn ex-deep" data-deep type="button" title="深读完整内容">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 17L17 7M17 7H9M17 7v8"/></svg>
            <span>深读</span>
          </button>
        </div>
      </header>
      <h2 class="ex-card-title">${escapeHtml(node.title)}</h2>
      <div class="ex-card-auth">
        <span class="ex-auth-label">权威度</span>
        <span class="ex-auth-bar"><span class="ex-auth-fill" style="width:${pctVal}%"></span></span>
        <span class="ex-auth-num">${pctVal}</span>
      </div>
      <div class="ex-card-excerpt">${preview ? escapeHtml(preview) : '<span class="muted">该条目暂无正文片段。</span>'}</div>
      <section class="ex-card-rel" data-rel-box>
        ${relSectionHTML(rel.inGroups, '反链 · 谁引用了它', rel.inCount)}
        ${relSectionHTML(rel.outGroups, '外链 · 它引用了谁', rel.outCount)}
      </section>
    </article>`;
}

/** 随机抽一个候选，排除 excludeId（避免连续重复）。候选空回退 null。 */
function pickRandom(excludeId) {
  if (!candidates.length) return null;
  if (candidates.length === 1) return candidates[0];
  let pick;
  let guard = 0;
  do {
    pick = candidates[Math.floor(Math.random() * candidates.length)];
    guard++;
  } while (pick.id === excludeId && guard < 8);
  return pick;
}

/** 换一个：淡出 → 换内容 → 淡入。reduced-motion 下跳过淡出同步换内容。 */
function reroll() {
  const next = pickRandom(currentId);
  if (!next) return;
  const stage = root.querySelector('[data-stage]');
  const swap = () => {
    if (!root) return;
    currentId = next.id;
    renderCard(next);
    stage.classList.remove('is-rolling');
    // 卡片重新挂载后重新聚焦"换一个"，方便连续点击探索
    const btn = root.querySelector('[data-reroll]');
    if (btn) btn.focus({ preventScroll: true });
  };
  // reduced-motion：跳过淡出动效，直接换（避免 90ms 空窗卡顿）
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    swap();
    return;
  }
  stage.classList.add('is-rolling');
  // 等淡出半程再换内容（dur-base 160ms，取 90ms）
  setTimeout(swap, 90);
}

function bind() {
  root.querySelector('[data-stage]').addEventListener('click', (e) => {
    const rerollBtn = e.target.closest('[data-reroll]');
    if (rerollBtn) { e.stopPropagation(); reroll(); return; }
    const deepBtn = e.target.closest('[data-deep]');
    if (deepBtn && currentId) { e.stopPropagation(); actions.openDetail(currentId); return; }
    const rel = e.target.closest('[data-rel]');
    if (rel) { e.stopPropagation(); actions.openDetail(rel.dataset.rel); return; }
    // 点卡片空白区 → 当前节点详情
    const card = e.target.closest('[data-id]');
    if (card && card.dataset.id === currentId) actions.openDetail(currentId);
  });
  root.querySelector('[data-stage]').addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const rel = e.target.closest('[data-rel]');
    if (rel) { e.preventDefault(); actions.openDetail(rel.dataset.rel); return; }
    const card = e.target.closest('[data-id]');
    if (card) { e.preventDefault(); actions.openDetail(card.dataset.id); }
  });
  // 顶栏"换一个"按钮
  root.querySelector('[data-reroll-top]').addEventListener('click', reroll);
}

const ERROR_HTML = `
  <div class="placeholder">
    <div class="panel placeholder-card">
      <h1 class="placeholder-title">知识漫步</h1>
      <div class="placeholder-sub">数据加载失败</div>
      <div class="placeholder-hint">无法读取 <code>window.__EK_GRAPH</code>，请通过 <code>http://localhost:5188</code> 访问。</div>
    </div>
  </div>`;

export function mount(container) {
  root = container;
  root.classList.add('view-explore');

  graph = loadGraph();
  if (!graph) {
    root.innerHTML = ERROR_HTML;
    return;
  }
  index = buildIndex(graph);

  // 候选池：degree>0 节点（关系驱动，孤立节点无探索价值）。全孤立则回退全库。
  candidates = graph.nodes.filter((n) => {
    const edges = index.edgeIndex.get(n.id);
    return edges && edges.length > 0;
  });
  if (!candidates.length) candidates = [...graph.nodes];

  root.innerHTML = `
    <div class="ex-shell">
      <header class="ex-header panel">
        <div class="ex-header-main">
          <h1 class="ex-title">知识漫步</h1>
          <p class="ex-sub">关系驱动随机探索 · 从 <b>${candidates.length}</b> 个有连接的节点里随机相遇</p>
        </div>
        <button class="btn ex-reroll-top" data-reroll-top type="button">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M3 8a9 9 0 0 1 14.5-3M21 4v4h-4"/>
            <path d="M21 16a9 9 0 0 1-14.5 3M3 20v-4h4"/>
          </svg>
          <span>换一个</span>
        </button>
      </header>
      <div class="ex-stage" data-stage></div>
    </div>`;

  const first = pickRandom(null);
  if (first) {
    currentId = first.id;
    renderCard(first);
  } else {
    root.querySelector('[data-stage]').innerHTML = `<div class="ex-empty muted">候选池为空</div>`;
  }
  bind();
}

export function unmount() {
  if (root) {
    root.innerHTML = '';
    root.classList.remove('view-explore');
  }
  root = null;
  graph = null;
  index = null;
  candidates = [];
  currentId = null;
}
