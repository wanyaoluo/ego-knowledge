// views/search/index.js · 搜索中心（用户路径收敛版 · B 系列）
// 面向普通用户（非开发者）：隐藏 5 路检索算法，自动融合检索，用户无感；
// 结果给一句话摘要 + 相关度指示；筛选用用户语言（类型/状态/更新度/领域）；
// 排序给用户维度（相关度/权威度/活跃度）；空状态引导 + 热门领域快捷入口。
//
// 检索内核仍走多路融合（exact/bm25/graph/dense → RRF），但不再暴露给用户切换。
// CSS 在 index.html 用 <link> 引入（无 build 环境 ES module 不支持 import css）
import {
  KIND_COLORS, KIND_LABELS, STATUS_LABELS, FRESHNESS_LABELS,
  loadGraph, buildIndex, buildSearchIndex, search as graphSearch, authPct,
} from '../../lib/data-layer.js';
import { escapeHtml } from '../../lib/escape.js';
import * as store from '../../lib/store.js';
import * as actions from '../../lib/actions.js';

let root = null;
let graph = null;
let index = null;
let searchIndex = [];          // buildSearchIndex 派生项
let idToNode = new Map();      // id → 原始节点（取 body 派生摘要用）
let debounceT = null;          // 输入防抖 timer

// 排序维度（用户语言）；freshness 活跃度：volatile 最活跃 > watch > stable 最稳
const FRESH_RANK = { volatile: 3, watch: 2, stable: 1 };
const SORT_OPTIONS = [
  { id: 'relevance', label: '相关度' },
  { id: 'authority', label: '权威度' },
  { id: 'activity', label: '活跃度' },
];

// 本地视图态（仅 search 视图消费）
const local = {
  query: '',
  sortBy: 'relevance',         // 默认按相关度（融合分数）
  filtersCollapsed: true,      // 筛选区默认收起（B 重做：搜了再筛更合理）
  filters: {
    kinds: {},                 // {} 空对象 = 全开
    statuses: {},
    freshnesses: {},
    domains: {},
  },
};

// ===== 工具：分词 / bigram / 相似度（原 5 路算法内核，保留但不暴露切换）=====

const TOKEN_SPLIT = /[\s,，。、;；:：!！?？()（）[\]【】"'`'/\\|]+/;

function tokenize(q) {
  return (q || '')
    .toLowerCase()
    .split(TOKEN_SPLIT)
    .filter(Boolean);
}

function bigrams(s) {
  const set = new Set();
  const str = (s || '').toLowerCase().replace(/\s+/g, '');
  for (let i = 0; i < str.length - 1; i++) set.add(str.slice(i, i + 2));
  return set;
}

function jaccard(a, b) {
  if (!a.size || !b.size) return 0;
  let inter = 0;
  for (const x of a) if (b.has(x)) inter++;
  const union = a.size + b.size - inter;
  return union > 0 ? inter / union : 0;
}

function countOccurrences(hay, needle) {
  if (!needle) return 0;
  let count = 0;
  let i = 0;
  while ((i = hay.indexOf(needle, i)) !== -1) { count++; i += needle.length; }
  return count;
}

// 空对象视为"全开"（未做任何勾选 = 不限制）
function isAllOn(map) {
  return !map || Object.keys(map).length === 0;
}

// ===== B1：自动融合检索（用户无感，背后跑 exact+bm25+graph+dense 四路 + RRF）=====
// 不再暴露 backend 切换；返回 [{item, score}]，score = RRF 融合分数（越高越相关）。

function runExact(q, ql) {
  return searchIndex
    .filter((it) => {
      const t = (it.title || '').toLowerCase();
      const s = (it.slug || '').toLowerCase();
      return t === ql || s === ql || t.includes(ql) || s.includes(ql);
    })
    .map((it) => ({ item: it }));
}

function runBm25(q) {
  const terms = tokenize(q);
  if (!terms.length) return [];
  return searchIndex
    .map((it) => {
      const t = (it.title || '').toLowerCase();
      let score = 0;
      let hits = 0;
      terms.forEach((term) => {
        const tCount = countOccurrences(t, term);
        const hCount = Math.max(0, countOccurrences(it.haystack, term) - tCount);
        if (tCount + hCount > 0) {
          hits++;
          score += 1 + Math.log(1 + tCount * 3 + hCount);
        }
      });
      if (hits === 0) return null;
      score += it.authority * 0.5;
      return { item: it, score };
    })
    .filter(Boolean);
}

function runGraph(q) {
  return graphSearch(searchIndex, q, 200).map((it) => ({ item: it }));
}

function runDense(q) {
  const qBigr = bigrams(q);
  if (!qBigr.size) return [];
  return searchIndex
    .map((it) => ({ item: it, score: jaccard(qBigr, it.bigrams) }))
    .filter((x) => x.score > 0.04);
}

/**
 * 多路融合（RRF）。把 exact/bm25/graph/dense 四路的倒数排名融合成统一相关度分数。
 * 返回 [{item, score}]，按 score 降序。
 */
function runFusion(q) {
  const ql = q.toLowerCase();
  const lists = [
    runExact(q, ql),
    runBm25(q),
    runGraph(q),
    runDense(q),
  ];
  const K = 60;   // RRF 常数
  const scores = new Map();
  lists.forEach((list) => {
    list.forEach((entry, rank) => {
      const id = entry.item.id;
      scores.set(id, (scores.get(id) || 0) + 1 / (K + rank + 1));
    });
  });
  const byId = new Map(searchIndex.map((it) => [it.id, it]));
  return [...scores.entries()]
    .map(([id, score]) => ({ item: byId.get(id), score }))
    .filter((x) => x.item)
    .sort((a, b) => b.score - a.score);
}

// ===== B2：摘要提取（从 body 第一段可读纯文本）=====
/**
 * 从节点 body 提取一句话摘要：跳过标题/表格/列表/引用行，去 markdown 标记，截断 ~88 字。
 * 无 body 返回空串（卡片渲染时回退到域/slug 兜底）。
 */
function extractSummary(node) {
  if (!node || !node.body || !node.body.trim()) return '';
  const lines = node.body.split('\n');
  for (const raw of lines) {
    const t = raw.trim();
    if (!t) continue;
    if (t.startsWith('#')) continue;        // 标题
    if (t.startsWith('|')) continue;        // 表格行
    if (t.startsWith('---')) continue;      // 分隔线
    if (/^[-*+]\s/.test(t)) continue;       // 无序列表
    if (/^\d+\.\s/.test(t)) continue;       // 有序列表
    if (t.startsWith('>')) continue;        // 引用
    if (t.startsWith('```')) continue;      // 代码围栏
    // 去 markdown 行内标记（加粗/代码/链接），保留可读文本
    const plain = t
      .replace(/\*\*([^*]+)\*\*/g, '$1')
      .replace(/`([^`]+)`/g, '$1')
      .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1');
    if (plain.length < 4) continue;         // 太短大概率是分隔符残留
    return plain.length > 88 ? plain.slice(0, 88) + '…' : plain;
  }
  return '';
}

function applyFilters(scored) {
  const f = local.filters;
  return scored.filter(({ item }) => {
    if (!isAllOn(f.kinds) && !f.kinds[item.kind]) return false;
    if (!isAllOn(f.statuses) && !f.statuses[item.status]) return false;
    if (!isAllOn(f.freshnesses) && !f.freshnesses[item.freshness]) return false;
    const dom = item.domain || '_unsorted';
    if (!isAllOn(f.domains) && !f.domains[dom]) return false;
    return true;
  });
}

/** B4：按用户选择的维度排序（相关度/权威度/活跃度）。 */
function applySort(scored) {
  const list = scored.slice();
  switch (local.sortBy) {
    case 'authority':
      return list.sort((a, b) => (Number(b.item.authority) || 0) - (Number(a.item.authority) || 0));
    case 'activity':
      return list.sort((a, b) => {
        const fa = FRESH_RANK[a.item.freshness] || 0;
        const fb = FRESH_RANK[b.item.freshness] || 0;
        if (fa !== fb) return fb - fa;
        return (Number(b.item.authority) || 0) - (Number(a.item.authority) || 0);
      });
    case 'relevance':
    default:
      return list.sort((a, b) => b.score - a.score);
  }
}

// ===== HTML 骨架（B1 删 backend / B3 筛选用户化 / B4 加排序）=====

const SEARCH_HTML = `
<div class="ek-search">
  <header class="search-header panel">
    <div class="search-top">
      <div class="search-bar">
        <svg class="search-icon" viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="10.5" cy="10.5" r="6.5"/>
          <line x1="15.5" y1="15.5" x2="20" y2="20"/>
        </svg>
        <input class="search-input" data-search-input type="search"
               placeholder="搜索知识 · 试试 hook、决策、novel、自动化"
               autocomplete="off" spellcheck="false" aria-label="搜索关键词">
        <span class="search-count" data-result-count aria-live="polite"></span>
      </div>
      <button class="filter-toggle" data-toggle-filters type="button"
              aria-expanded="false" aria-controls="search-filters" hidden>
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M3 6h18M6 12h12M10 18h4"/>
        </svg>
        <span>筛选</span>
        <span class="filter-badge" data-filter-count hidden></span>
      </button>
    </div>
    <div class="search-sub" hidden>
      <span class="search-sub-label">排序</span>
      <div class="sort-switcher" role="radiogroup" aria-label="结果排序" data-sort-group></div>
    </div>
  </header>

  <aside class="panel search-filters collapsed" id="search-filters">
    <div class="panel-section">
      <div class="panel-title">类型</div>
      <div class="filter-chips" data-filter="kinds"></div>
    </div>
    <div class="panel-section">
      <div class="panel-title">状态</div>
      <div class="filter-chips" data-filter="statuses"></div>
    </div>
    <div class="panel-section">
      <div class="panel-title">更新度</div>
      <div class="filter-chips" data-filter="freshnesses"></div>
    </div>
    <div class="panel-section">
      <div class="panel-title">领域</div>
      <select class="domain-select" data-filter="domains" multiple size="6"
              aria-label="按领域筛选（按住 Ctrl/Cmd 多选）"></select>
    </div>
    <div class="panel-section panel-actions">
      <button class="btn btn-ghost" data-reset-filters type="button">清空筛选</button>
    </div>
  </aside>

  <section class="search-results" data-results aria-live="polite"></section>
</div>
`;

// ===== 渲染 =====

/** B4：排序 segmented control */
function renderSortControl() {
  const group = root.querySelector('[data-sort-group]');
  if (!group) return;
  group.innerHTML = '';
  SORT_OPTIONS.forEach((opt) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'sort-btn' + (local.sortBy === opt.id ? ' active' : '');
    b.setAttribute('role', 'radio');
    b.setAttribute('aria-checked', String(local.sortBy === opt.id));
    b.dataset.sort = opt.id;
    b.textContent = opt.label;
    b.addEventListener('click', () => {
      if (local.sortBy === opt.id) return;
      local.sortBy = opt.id;
      renderSortControl();
      rerun();
    });
    group.appendChild(b);
  });
}

/** 渲染类型/状态/更新度 chip + 领域 select 选项 */
function renderFilterOptions() {
  // 类型：按 KIND_LABELS 用户语言标签
  const kindsBox = root.querySelector('[data-filter="kinds"]');
  kindsBox.innerHTML = '';
  Object.keys(KIND_COLORS).forEach((k) => {
    if (!index.kindCounts[k]) return;
    kindsBox.appendChild(buildChip('kinds', k, KIND_LABELS[k] || k, KIND_COLORS[k]));
  });

  // 状态：用户语言（草稿/活跃/权威/...）
  const statusBox = root.querySelector('[data-filter="statuses"]');
  statusBox.innerHTML = '';
  Object.keys(index.statusCounts).forEach((s) => {
    statusBox.appendChild(buildChip('statuses', s, STATUS_LABELS[s] || s, null));
  });

  // 更新度：原 freshness，用户语言（稳定/观察/易变）
  const freshBox = root.querySelector('[data-filter="freshnesses"]');
  freshBox.innerHTML = '';
  Object.keys(index.freshnessCounts).forEach((fr) => {
    freshBox.appendChild(buildChip('freshnesses', fr, FRESHNESS_LABELS[fr] || fr, null));
  });

  // 领域：multiple select
  const sel = root.querySelector('[data-filter="domains"]');
  sel.innerHTML = '';
  Object.keys(index.domainCounts)
    .sort((a, b) => index.domainCounts[b] - index.domainCounts[a])
    .forEach((dom) => {
      const opt = document.createElement('option');
      opt.value = dom;
      opt.textContent = dom === '_unsorted' ? '未归类' : `${dom} (${index.domainCounts[dom]})`;
      sel.appendChild(opt);
    });
  sel.addEventListener('change', () => {
    const picked = {};
    Array.from(sel.selectedOptions).forEach((o) => { picked[o.value] = true; });
    local.filters.domains = picked;
    rerun();
  });
}

function buildChip(filterKey, value, label, color) {
  const chip = document.createElement('div');
  const active = !!local.filters[filterKey][value];
  chip.className = 'filter-chip' + (active ? ' active' : '');
  chip.setAttribute('role', 'checkbox');
  chip.setAttribute('aria-checked', String(active));
  chip.setAttribute('tabindex', '0');
  if (color) {
    chip.style.setProperty('--chip-color', color);
  } else {
    chip.style.setProperty('--chip-color', 'var(--text-tertiary)');
  }
  chip.innerHTML = `<span class="filter-chip-dot"></span><span class="filter-chip-label">${label}</span>`;
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
    rerun();
  };
  chip.addEventListener('click', toggle);
  chip.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
  });
  return chip;
}

function renderResults(scored, filteredCount) {
  const box = root.querySelector('[data-results]');
  const countEl = root.querySelector('[data-result-count]');

  const total = scored.length;
  const filtered = filteredCount - total;
  if (countEl) {
    countEl.textContent = total === 0
      ? '0'
      : (filtered > 0 ? `${total} / ${filteredCount}` : `${total}`);
  }

  if (!local.query.trim()) {
    box.innerHTML = emptyState();
    bindEmptyActions();
    return;
  }

  if (total === 0) {
    box.innerHTML = noResultState(filtered, filteredCount);
    bindEmptyActions();
    return;
  }

  // 相关度归一化基准（首项 score = 100%）
  const maxScore = scored[0] && scored[0].score > 0 ? scored[0].score : 1;

  // 结果卡片网格
  const grid = document.createElement('div');
  grid.className = 'result-grid';
  scored.forEach((entry, idx) => grid.appendChild(buildResultCard(entry, idx, maxScore)));
  box.innerHTML = '';
  box.appendChild(grid);
}

/** B2：结果卡片 —— title / 类型 / 状态 / 领域 / 一句话摘要 / 相关度指示 */
function buildResultCard(entry, idx, maxScore) {
  const it = entry.item;
  const card = document.createElement('button');
  card.type = 'button';
  card.className = 'result-card';
  card.dataset.id = it.id;
  card.style.setProperty('--kind-color', KIND_COLORS[it.kind] || 'var(--text-tertiary)');
  card.setAttribute('aria-label', `查看详情：${it.title}`);

  const kindLabel = KIND_LABELS[it.kind] || it.kind;
  const domainLabel = !it.domain || it.domain === '_unsorted' ? '未归类' : it.domain;
  const statusLabel = STATUS_LABELS[it.status] || '';
  const summary = extractSummary(idToNode.get(it.id));

  // 相关度指示：归一化到 0-100%（仅"相关度"排序时强调，其他排序弱化但仍显示）
  const relPct = Math.max(4, Math.round((entry.score / maxScore) * 100));
  const relDim = local.sortBy !== 'relevance';

  card.innerHTML = `
    <div class="card-rank">${String(idx + 1).padStart(2, '0')}</div>
    <div class="card-main">
      <div class="card-head">
        <span class="card-kind">${kindLabel}</span>
        ${statusLabel ? `<span class="card-tag">${statusLabel}</span>` : ''}
      </div>
      <h3 class="card-title">${escapeHtml(it.title)}</h3>
      ${summary ? `<p class="card-summary">${escapeHtml(summary)}</p>` : ''}
      <div class="card-meta">
        <span class="card-domain">${escapeHtml(domainLabel)}</span>
      </div>
      <div class="card-relevance${relDim ? ' relevance-dim' : ''}">
        <span class="card-relevance-label">${local.sortBy === 'authority' ? '权威' : (local.sortBy === 'activity' ? '活跃' : '相关')}</span>
        <div class="card-relevance-bar"><div class="card-relevance-fill" style="width:${relPct}%"></div></div>
      </div>
    </div>
    <div class="card-arrow" aria-hidden="true">→</div>
  `;

  card.addEventListener('click', () => actions.openDetail(it.id));
  return card;
}

/** B5：初始空状态 —— 用户语言引导 + 热门领域快捷入口 */
function emptyState() {
  const hotDomains = Object.keys(index.domainCounts)
    .filter((d) => d !== '_unsorted' && index.domainCounts[d] >= 2)
    .sort((a, b) => index.domainCounts[b] - index.domainCounts[a])
    .slice(0, 6);
  const domainChips = hotDomains
    .map((d) => `<button class="quick-chip" data-quick-domain="${escapeHtml(d)}" type="button">${escapeHtml(d)}</button>`)
    .join('');
  return `
    <div class="search-empty">
      <svg class="empty-icon" viewBox="0 0 24 24" aria-hidden="true">
        <circle cx="10.5" cy="10.5" r="6.5"/>
        <line x1="15.5" y1="15.5" x2="20" y2="20"/>
      </svg>
      <div class="empty-title">输入关键词找知识</div>
      <div class="empty-sub">自动融合检索 · 不用挑算法，直接搜就行</div>
      <div class="empty-hint">试试这些词：hook · 决策 · novel · 自动化 · 知识管理</div>
      ${domainChips ? `
        <div class="quick-group">
          <div class="quick-label">或者从热门领域开始</div>
          <div class="quick-chips">${domainChips}</div>
        </div>` : ''}
    </div>`;
}

/** B5：无结果空状态 —— 引导放宽筛选 / 换词 */
function noResultState(filtered, filteredCount) {
  const hasFilter = filtered > 0 || ['kinds', 'statuses', 'freshnesses', 'domains']
    .some((k) => !isAllOn(local.filters[k]));
  return `
    <div class="search-empty">
      <svg class="empty-icon" viewBox="0 0 24 24" aria-hidden="true">
        <circle cx="10.5" cy="10.5" r="6.5"/>
        <line x1="15.5" y1="15.5" x2="20" y2="20"/>
      </svg>
      <div class="empty-title">没找到「${escapeHtml(local.query)}」</div>
      <div class="empty-sub">${hasFilter
        ? `检索到 ${filteredCount} 条，但被筛选挡住了`
        : '换个说法再试试'}</div>
      <div class="empty-hint">${hasFilter
        ? '试试清空左侧筛选，或换个更宽的关键词'
        : '试试更短的词、近义词，或者从下面的热门领域入手'}</div>
      ${hasFilter
        ? `<button class="quick-chip quick-reset" data-clear-filters type="button">清空所有筛选</button>`
        : `<div class="quick-chips">${hotDomainChips()}</div>`}
    </div>`;
}

function hotDomainChips() {
  return Object.keys(index.domainCounts)
    .filter((d) => d !== '_unsorted' && index.domainCounts[d] >= 2)
    .sort((a, b) => index.domainCounts[b] - index.domainCounts[a])
    .slice(0, 6)
    .map((d) => `<button class="quick-chip" data-quick-domain="${escapeHtml(d)}" type="button">${escapeHtml(d)}</button>`)
    .join('');
}

/** B5：绑定空状态里的快捷入口（热门领域点击 / 清空筛选） */
function bindEmptyActions() {
  if (!root) return;
  root.querySelectorAll('[data-quick-domain]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const d = btn.dataset.quickDomain;
      const input = root.querySelector('[data-search-input]');
      if (input && d) {
        input.value = d;
        local.query = d;
        store.setState({ lastQuery: d });
        rerun();
      }
    });
  });
  const clear = root.querySelector('[data-clear-filters]');
  if (clear) {
    clear.addEventListener('click', () => {
      local.filters = { kinds: {}, statuses: {}, freshnesses: {}, domains: {} };
      renderFilterOptions();
      rerun();
    });
  }
}

// ===== 主流程：融合检索 + 筛选 + 排序 + 渲染 =====

function rerun() {
  const q = local.query.trim();
  let scored = q ? runFusion(q) : [];
  const rawCount = scored.length;
  scored = applyFilters(scored);
  scored = applySort(scored);
  renderResults(scored, rawCount);
  syncChrome();
}

/**
 * 同步搜索 chrome 显隐（B 重做：初始只显示大搜索框，搜了再展开筛选/排序）。
 * - 无 query：筛选按钮、排序栏、筛选区全隐藏 → 搜索框独占视觉焦点
 * - 有 query：排序栏显示；筛选按钮显示；筛选区按 filtersCollapsed 决定
 * - 筛选按钮徽章：实时反映已激活的筛选条件数
 */
function syncChrome() {
  if (!root) return;
  const hasQuery = !!local.query.trim();
  const filterCount = countActiveFilters();

  const filtersEl = root.querySelector('.search-filters');
  const filterBtn = root.querySelector('[data-toggle-filters]');
  const sortWrap = root.querySelector('.search-sub');
  const badge = root.querySelector('[data-filter-count]');

  // 排序栏：无 query 隐藏（没结果排序无意义）
  if (sortWrap) sortWrap.hidden = !hasQuery;

  // 筛选按钮 + 筛选区：无 query 强制隐藏（避免"进去先看到一堆筛选"的反直觉）
  const expanded = hasQuery && !local.filtersCollapsed;
  if (filterBtn) {
    filterBtn.hidden = !hasQuery;
    filterBtn.setAttribute('aria-expanded', String(expanded));
    filterBtn.classList.toggle('active', expanded);
  }
  if (filtersEl) {
    filtersEl.classList.toggle('collapsed', !expanded);
  }
  root.classList.toggle('filters-open', expanded);

  // 徽章：激活筛选数 > 0 时显示
  if (badge) {
    badge.textContent = filterCount > 0 ? String(filterCount) : '';
    badge.hidden = filterCount === 0;
  }
}

/** 统计已激活的筛选条件总数（4 类合计）。 */
function countActiveFilters() {
  const f = local.filters;
  return Object.keys(f.kinds).length
    + Object.keys(f.statuses).length
    + Object.keys(f.freshnesses).length
    + Object.keys(f.domains).length;
}

// ===== 事件绑定 =====

function bindControls() {
  const input = root.querySelector('[data-search-input]');

  // input：防抖 120ms + Enter 立即触发
  input.addEventListener('input', (e) => {
    const v = e.target.value;
    window.clearTimeout(debounceT);
    debounceT = window.setTimeout(() => {
      local.query = v;
      store.setState({ lastQuery: v });
      rerun();
    }, 120);
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      window.clearTimeout(debounceT);
      local.query = input.value;
      store.setState({ lastQuery: input.value });
      rerun();
    } else if (e.key === 'Escape' && input.value) {
      input.value = '';
      local.query = '';
      store.setState({ lastQuery: '' });
      rerun();
    }
  });

  // 清空筛选
  root.querySelector('[data-reset-filters]').addEventListener('click', () => {
    local.filters = { kinds: {}, statuses: {}, freshnesses: {}, domains: {} };
    renderFilterOptions();
    rerun();
  });

  // 筛选 toggle：展开/收起筛选区（B 重做：初始收起，避免突兀）
  const filterToggle = root.querySelector('[data-toggle-filters]');
  if (filterToggle) {
    filterToggle.addEventListener('click', () => {
      local.filtersCollapsed = !local.filtersCollapsed;
      syncChrome();
    });
  }
}

// ===== 挂载 / 卸载 =====

export function mount(container) {
  root = container;
  root.innerHTML = SEARCH_HTML;
  root.classList.add('view-search');

  graph = loadGraph();
  if (!graph || !graph.nodes.length) {
    root.innerHTML = `
      <div class="search-empty">
        <div class="empty-title">数据加载失败</div>
        <div class="empty-sub">不要双击 index.html（file:// 会禁止读取数据）</div>
        <div class="empty-hint">请在浏览器地址栏访问 http://localhost:5188</div>
      </div>`;
    console.error('[search] window.__EK_GRAPH 缺失', graph);
    return;
  }

  index = buildIndex(graph);
  searchIndex = buildSearchIndex(graph);
  idToNode = new Map(graph.nodes.map((n) => [n.id, n]));

  // 从 store 恢复上次 query（视图切换回来时）
  const last = store.getState().lastQuery;
  if (last) {
    local.query = last;
    const input = root.querySelector('[data-search-input]');
    if (input) input.value = last;
  }

  renderSortControl();
  renderFilterOptions();
  bindControls();
  rerun();

  // 自动聚焦输入框，方便立即键入
  const input = root.querySelector('[data-search-input]');
  if (input) input.focus();
}

export function unmount() {
  window.clearTimeout(debounceT);
  debounceT = null;
  if (root) {
    root.innerHTML = '';
    root.classList.remove('view-search');
  }
  root = null;
  graph = null;
  index = null;
  searchIndex = [];
  idToNode = new Map();
}
