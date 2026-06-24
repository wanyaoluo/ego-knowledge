// views/detail/index.js · 单条深读（Phase 2 ek-detail · v2.8 起为 modal 弹窗）
// 双栏：左=关系列表（入/出，按类型分组）+ 同域相关；右=正文+元指标+关系子图
// 容器：#detail-modal（由 app.js switchView detail 分支挂载，覆盖主视图带遮罩）
// 唤起：任意视图点节点 → actions.openDetail(id) → navigate(#/detail/:id) → 弹窗 mount
// 关闭：右上角 × / 点遮罩 / ESC → actions.closeDetail() → navigate 回主视图
// 后退：浏览器原生 history.back()；无可后退历史（history.length<=1）时按钮 disabled 暗色
// 关系子图为独立 cytoscape 实例，unmount 时 destroy，不污染主图谱 store
// CSS 在 index.html 用 <link> 引入（无 build 环境 ES module 不支持 import css）
import * as store from '../../lib/store.js';
import * as actions from '../../lib/actions.js';
import {
  KIND_COLORS, KIND_LABELS, EDGE_COLORS, EDGE_STYLE, EDGE_LABELS,
  STATUS_LABELS, FRESHNESS_LABELS,
  loadGraph, buildIndex, getNodeById, authNorm, trunc,
} from '../../lib/data-layer.js';
import { escapeHtml } from '../../lib/escape.js';
import { simpleMarkdownToHtml, loadMarked } from '../../lib/markdown.js';

// v2.2 升格链：note → concept → decision → dossier（知识从草记到沉淀档案的成长路径）
const PROMO_CHAIN = ['note', 'concept', 'decision', 'dossier'];

// v2.2 生命力雷达图：4 维归一化映射（值越大越"健康"）
// freshness：stable 最健康（已稳定）/ watch 中（待观察）/ volatile 低（易变 = 衰变风险）
// status：authoritative 最高（已权威）/ active 高（活跃）/ draft 低（未沉淀）/ legacy/archived/deprecated 衰减
const VITALITY_FRESH = { stable: 0.9, watch: 0.5, volatile: 0.25 };
const VITALITY_STATUS = {
  authoritative: 0.95, active: 0.75, draft: 0.4,
  legacy: 0.35, archived: 0.25, deprecated: 0.15,
};

// confidence 词 → 归一化值 + 中文标签。数据中 confidence 可能是：
//   null/空（未评级）/ 词（high·medium·low）/ 纯数字（已归一化）。
// W3 bug 修复：原 Number("high")=NaN 导致雷达图顶点 NaN（SVG error）。
// 抽公共解析：词走映射、数字走 clamp、其余兜底中值 0.5，永不返回 NaN。
const CONF_WORD = {
  high: { score: 0.85, label: '高' },
  medium: { score: 0.5, label: '中' },
  low: { score: 0.25, label: '低' },
};
const CONF_UNRATED_SCORE = 0.15;
const CONF_FALLBACK_SCORE = 0.5;

/**
 * @param {number|string|null|undefined} raw node.confidence 原始值
 * @returns {{score:number, label:string}} score 0–1（永非 NaN）；label 供图例显示
 */
function parseConfidence(raw) {
  if (raw == null || raw === '') return { score: CONF_UNRATED_SCORE, label: '未评级' };
  if (CONF_WORD[raw]) return CONF_WORD[raw];
  const num = Number(raw);
  if (!Number.isNaN(num)) return { score: Math.max(0, Math.min(1, num)), label: num.toFixed(3) };
  return { score: CONF_FALLBACK_SCORE, label: String(raw) };
}

/**
 * 计算节点生命力 4 维归一化值（0–1）。
 * @param {{authority:number, freshness:string, confidence:number|string|null, status:string}} node
 * @returns {{auth:number, fresh:number, conf:number, stat:number}}
 */
function vitalityDims(node) {
  const auth = authNorm(Number(node.authority) || 0);
  const fresh = VITALITY_FRESH[node.freshness] != null ? VITALITY_FRESH[node.freshness] : 0.3;
  const conf = parseConfidence(node.confidence).score;
  const stat = VITALITY_STATUS[node.status] != null ? VITALITY_STATUS[node.status] : 0.3;
  return { auth, fresh, conf, stat };
}

// ----- 模块级状态（mount 期间有效，unmount 清空）-----
let root = null;
let graph = null;
let index = null;
let cy = null;              // 关系子图独立实例
let currentLayout = null;   // 子图布局引用（unmount 时 stop，避免 RAF 残留）
const timers = new Set();   // mount 期间 timer，unmount 统一清
let keydownHandler = null;  // ESC 关闭弹窗监听（unmount 移除）

// markdown 解析真源已抽到 lib/markdown.js（simpleMarkdownToHtml + loadMarked 导出复用）。
// mountSession 仍属本视图生命周期：防 loadMarked 异步回调跨 mount 会话误执行（切走后回调凭此判停）。
let mountSession = 0;

// ----- 子图参数 -----
const SUBGRAPH_HOPS = 2;
const SUBGRAPH_MAX_NODES = 24;

// ----- 子图 cytoscape 样式（中心节点樱粉描边，其余 kind 色）-----
const SUBGRAPH_STYLE = [
  {
    selector: 'node',
    style: {
      'background-color': 'data(color)',
      width: 'data(size)',
      height: 'data(size)',
      label: 'data(label)',
      'text-valign': 'bottom',
      'text-halign': 'center',
      'text-margin-y': 5,
      color: '#c1b3c0',
      'font-size': '10px',
      'font-family': 'inherit',
      'text-wrap': 'ellipsis',
      'text-max-width': '80px',
      'text-outline-color': '#120818',
      'text-outline-width': 2,
      'border-width': 0,
    },
  },
  {
    selector: 'node.center',
    style: {
      'border-color': '#e89bbb',
      'border-width': 3,
      'border-opacity': 1,
    },
  },
  {
    selector: 'edge',
    style: {
      width: 1.2,
      'line-color': 'data(color)',
      'line-style': 'data(lineStyle)',
      'curve-style': 'bezier',
      'target-arrow-color': 'data(color)',
      'target-arrow-shape': 'triangle',
      'arrow-scale': 0.7,
      opacity: 0.55,
    },
  },
];

// 与 graph 视图一致：animate:false 避免 cytoscape 3.30.2 cose 的 RAF 残留 bug
const COSE_LAYOUT = {
  name: 'cose',
  animate: false,
  nodeRepulsion: 6000,
  idealEdgeLength: 80,
  edgeElasticity: 0.45,
  gravity: 0.35,
  numIter: 1000,
  fit: true,
  padding: 24,
  randomize: true,
};

// ===== HTML 骨架 =====
// v2.8：弹窗结构 = 遮罩层（点关闭）+ 居中弹窗（× 浮右上角 + 原双栏内容全保留）。
// 遮罩与 shell 同为 #detail-modal 直接子元素，shell 内部布局不变（header + 双栏）。
function shellHTML() {
  return `
    <div class="detail-backdrop" data-backdrop></div>
    <div class="detail-shell">
      <button class="detail-close" data-close type="button" aria-label="关闭深读弹窗" title="关闭 (ESC)">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <line x1="6" y1="6" x2="18" y2="18"/>
          <line x1="18" y1="6" x2="6" y2="18"/>
        </svg>
      </button>
      <header class="detail-header panel" data-header></header>
      <div class="detail-cols">
        <aside class="panel detail-aside" data-aside></aside>
        <section class="panel detail-main" data-main></section>
      </div>
    </div>`;
}

// A3：无选中态引导卡片 —— 用户语言 + 明确入口按钮，替代原 #/graph 技术化文案。
// 用户原话痛点："想看详情不知道怎么跳转" → 这里把"怎么开始"做成两个明显的 CTA。
function noSelectionCard() {
  return `
    <div class="placeholder">
      <div class="panel placeholder-card detail-guide">
        <svg class="placeholder-icon" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M5 4h11a2 2 0 0 1 2 2v14H7a2 2 0 0 1-2-2V4z"/>
          <path d="M18 20v-2a2 2 0 0 0-2-2H5"/>
          <line x1="9" y1="8" x2="14" y2="8"/>
          <line x1="9" y1="11" x2="14" y2="11"/>
        </svg>
        <h1 class="placeholder-title">选一条知识来深读</h1>
        <div class="placeholder-sub">完整正文 · 关系网络 · 生命力 · 升格轨迹</div>
        <div class="placeholder-hint">还没有选定内容。从下面任一处选一条，点开后用"深读"就能看到它的完整信息。</div>
        <div class="detail-guide-actions">
          <button class="btn btn-guide-primary" data-guide-graph type="button">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="12" cy="18" r="2.5"/>
              <line x1="7.8" y1="7.5" x2="10.5" y2="16"/><line x1="16.2" y1="7.5" x2="13.5" y2="16"/><line x1="8.5" y1="6" x2="15.5" y2="6"/>
            </svg>
            <span>从图谱选节点</span>
          </button>
          <button class="btn btn-guide-primary" data-guide-search type="button">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <circle cx="10.5" cy="10.5" r="6.5"/><line x1="15.5" y1="15.5" x2="20" y2="20"/>
            </svg>
            <span>从搜索查找</span>
          </button>
        </div>
      </div>
    </div>`;
}

/** A3：节点不存在的引导卡片（也带返回入口，不让用户卡在死胡同）。 */
function notFoundCard(id) {
  return `
    <div class="placeholder">
      <div class="panel placeholder-card detail-guide">
        <svg class="placeholder-icon" viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="12" cy="12" r="9"/>
          <line x1="9" y1="9" x2="15" y2="15"/>
          <line x1="15" y1="9" x2="9" y2="15"/>
        </svg>
        <h1 class="placeholder-title">找不到这条知识</h1>
        <div class="placeholder-sub">节点 <code>${escapeHtml(id)}</code> 不存在或已失效</div>
        <div class="placeholder-hint">可能是链接过期或数据已更新。从图谱或搜索重新选一条吧。</div>
        <div class="detail-guide-actions">
          <button class="btn btn-guide-primary" data-guide-graph type="button">回图谱看看</button>
          <button class="btn btn-guide-primary" data-guide-search type="button">去搜索查找</button>
        </div>
      </div>
    </div>`;
}

/** A3：绑定引导卡片上的入口按钮（mount 里 root.innerHTML 写入后调用）。 */
function bindGuideActions() {
  if (!root) return;
  const g = root.querySelector('[data-guide-graph]');
  if (g) g.addEventListener('click', () => actions.openGraph());
  const s = root.querySelector('[data-guide-search]');
  if (s) s.addEventListener('click', () => actions.openSearch());
}

function loadErrorHTML() {
  return `
    <div class="overlay overlay-error">
      <span>数据加载失败</span>
      <span class="muted">window.__EK_GRAPH 缺失，请通过 <code>http://localhost:5188</code> 访问</span>
    </div>`;
}

// ===== 顶部信息条 =====
function renderHeader(node) {
  const el = root.querySelector('[data-header]');
  const kindColor = KIND_COLORS[node.kind] || '#897989';
  // v2.8：后退按钮 disabled 判断——无可后退历史（刚打开 demo / 直接 URL 进弹窗）时暗色不可点。
  // history.length<=1 表示浏览器栈无前页，history.back() 会退出页面，无意义。
  const canBack = window.history.length > 1;
  el.innerHTML = `
    <div class="dh-toolbar">
      <div class="dh-toolbar-actions">
        <button class="btn btn-back" data-back type="button"
          ${canBack ? '' : 'disabled aria-disabled="true"'}
          aria-label="后退到上一页">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M15 18l-6-6 6-6"/>
          </svg>
          <span>后退</span>
        </button>
        <button class="btn btn-copy" data-d-copy type="button" title="复制 slug 到剪贴板">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <rect x="9" y="9" width="11" height="11" rx="2"/>
            <path d="M5 15V5a2 2 0 0 1 2-2h8"/>
          </svg>
          <span class="btn-copy-text">复制路径</span>
        </button>
      </div>
      <span class="dh-kind" style="color:${kindColor};border-color:${kindColor}">
        ${KIND_LABELS[node.kind] || node.kind}
      </span>
    </div>
    <h1 class="dh-title">${escapeHtml(node.title)}</h1>
    <div class="dh-tags">
      ${node.domain && node.domain !== '_unsorted'
        ? `<span class="tag">${escapeHtml(node.domain)}</span>`
        : `<span class="tag tag-muted">无域归属</span>`}
      <span class="tag">${STATUS_LABELS[node.status] || node.status || '—'}</span>
      <span class="tag">${FRESHNESS_LABELS[node.freshness] || node.freshness || '—'}</span>
    </div>`;

  // v2.8："后退"按钮：浏览器原生 history.back()（disabled 态不可点已由 disabled 属性保证）。
  // hashchange 200ms 兜底 closeDetail：防 history.back() 无效（边缘情况）时弹窗卡住。
  // 弹窗模式下 × / 遮罩 / ESC 走 actions.closeDetail()（确定关闭回主视图），与后退语义分离。
  const back = el.querySelector('[data-back]');
  if (back && !back.disabled) {
    back.addEventListener('click', () => {
      let backed = false;
      const cleanup = () => {
        window.removeEventListener('hashchange', onHash);
        window.clearTimeout(fallback);
      };
      const onHash = () => { backed = true; cleanup(); };
      const fallback = window.setTimeout(() => {
        cleanup();
        if (!backed) actions.closeDetail();
      }, 200);
      window.addEventListener('hashchange', onHash);
      window.history.back();
    });
  }

  const copyBtn = el.querySelector('[data-d-copy]');
  copyBtn.addEventListener('click', () => {
    const text = node.slug || node.id;
    if (!navigator.clipboard) return;
    navigator.clipboard.writeText(text).then(() => {
      const txt = copyBtn.querySelector('.btn-copy-text');
      const orig = txt.textContent;
      txt.textContent = '已复制 ✓';
      const t = window.setTimeout(() => { txt.textContent = orig; }, 1400);
      timers.add(t);
    }).catch(() => { /* 剪贴板被拒，静默 */ });
  });
}

// ===== 左栏：关系列表 + 同域相关 =====
function renderAside(node) {
  const el = root.querySelector('[data-aside]');
  const inEdges = [];
  const outEdges = [];
  (index.edgeIndex.get(node.id) || []).forEach((e) => {
    if (e.source === node.id) outEdges.push(e);
    else inEdges.push(e);
  });

  // 同域相关：仅当本节点有有效域时才计算
  const hasDomain = node.domain && node.domain !== '_unsorted';
  const sameDomain = hasDomain
    ? graph.nodes.filter((n) => n.domain === node.domain && n.id !== node.id).slice(0, 8)
    : [];

  el.innerHTML = `
    <section class="panel-section">
      <div class="detail-section-head"><span>入边关系</span><span class="muted">${inEdges.length}</span></div>
      <div class="rel-list" data-in-edges></div>
    </section>
    <section class="panel-section">
      <div class="detail-section-head"><span>出边关系</span><span class="muted">${outEdges.length}</span></div>
      <div class="rel-list" data-out-edges></div>
    </section>
    <section class="panel-section">
      <div class="detail-section-head"><span>同域相关</span><span class="muted">${sameDomain.length}</span></div>
      <div class="rel-list" data-same-domain></div>
    </section>`;

  drawRelList(root.querySelector('[data-in-edges]'), inEdges, node.id, 'in');
  drawRelList(root.querySelector('[data-out-edges]'), outEdges, node.id, 'out');
  drawSameDomain(root.querySelector('[data-same-domain]'), sameDomain);
}

/** 关系列表按 type 分组渲染 */
function drawRelList(box, edges, currentId, dir) {
  if (!box) return;
  if (!edges.length) {
    box.innerHTML = `<div class="rel-empty muted">无</div>`;
    return;
  }
  const groups = {};
  edges.forEach((e) => { (groups[e.type] = groups[e.type] || []).push(e); });

  box.innerHTML = '';
  Object.keys(groups).forEach((type) => {
    const color = EDGE_COLORS[type] || '#897989';
    const lineStyle = EDGE_STYLE[type] || 'solid';
    const groupEl = document.createElement('div');
    groupEl.className = 'rel-group';
    groupEl.innerHTML = `
      <div class="rel-group-head">
        <span class="rel-group-line ${lineStyle}" style="border-color:${color}"></span>
        <span class="rel-group-label">${EDGE_LABELS[type] || type}</span>
        <span class="rel-group-count muted">${groups[type].length}</span>
      </div>`;
    groups[type].forEach((e) => {
      const otherId = e.source === currentId ? e.target : e.source;
      const other = getNodeById(graph, otherId);
      const arrow = dir === 'out' ? '→' : '←';
      const item = document.createElement('button');
      item.className = 'rel-item';
      item.type = 'button';
      item.innerHTML = `
        <span class="rel-item-arrow">${arrow}</span>
        <span class="rel-item-kind" style="background:${KIND_COLORS[other ? other.kind : ''] || '#897989'}"></span>
        <span class="rel-item-title">${other ? escapeHtml(trunc(other.title, 18)) : escapeHtml(otherId)}</span>`;
      item.addEventListener('click', () => actions.openDetail(otherId));
      groupEl.appendChild(item);
    });
    box.appendChild(groupEl);
  });
}

function drawSameDomain(box, nodes) {
  if (!box) return;
  if (!nodes.length) {
    box.innerHTML = `<div class="rel-empty muted">无同域节点</div>`;
    return;
  }
  box.innerHTML = '';
  nodes.forEach((n) => {
    const item = document.createElement('button');
    item.className = 'rel-item';
    item.type = 'button';
    item.innerHTML = `
      <span class="rel-item-arrow">·</span>
      <span class="rel-item-kind" style="background:${KIND_COLORS[n.kind] || '#897989'}"></span>
      <span class="rel-item-title">${escapeHtml(trunc(n.title, 18))}</span>`;
    item.addEventListener('click', () => actions.openDetail(n.id));
    box.appendChild(item);
  });
}

// ===== v2.2 知识生命力雷达图（detail · 4 维综合 → 一眼可读的健康分布）=====
// 信息设计加工：把 authority/freshness/confidence/status 四个孤立数值/状态
// 综合成一个雷达多边形——形状本身即"脾性"（饱满 = 健康知识 / 偏瘪 = 有短板）
const VITALITY_AXES = [
  { key: 'auth', label: '权威', angle: -90 },   // 上
  { key: 'fresh', label: '新鲜', angle: 0 },    // 右
  { key: 'conf', label: '置信', angle: 90 },    // 下
  { key: 'stat', label: '状态', angle: 180 },   // 左
];

function vitalityRadarSVG(node) {
  const d = vitalityDims(node);
  const cx = 72, cy = 72, R = 50;
  const values = { auth: d.auth, fresh: d.fresh, conf: d.conf, stat: d.stat };

  const ptOnAxis = (axis, scale) => {
    const rad = axis.angle * Math.PI / 180;
    return {
      x: cx + R * scale * Math.cos(rad),
      y: cy + R * scale * Math.sin(rad),
    };
  };

  // 背景同心四边形（0.25/0.5/0.75/1.0 网格）
  const grids = [0.25, 0.5, 0.75, 1.0].map((g) => {
    const gp = VITALITY_AXES.map((a) => {
      const p = ptOnAxis(a, g);
      return `${p.x.toFixed(1)},${p.y.toFixed(1)}`;
    }).join(' ');
    return `<polygon points="${gp}" fill="none" stroke="var(--vitality-grid)" stroke-width="0.8"/>`;
  }).join('');

  // 4 条轴线
  const axisLines = VITALITY_AXES.map((a) => {
    const p = ptOnAxis(a, 1);
    return `<line x1="${cx}" y1="${cy}" x2="${p.x.toFixed(1)}" y2="${p.y.toFixed(1)}" stroke="var(--vitality-axis)" stroke-width="0.8"/>`;
  }).join('');

  // 数据多边形（樱粉半透填充 + 紫描边）
  const dataPts = VITALITY_AXES.map((a) => {
    const p = ptOnAxis(a, values[a.key]);
    return `${p.x.toFixed(1)},${p.y.toFixed(1)}`;
  }).join(' ');
  const dataPoly = `<polygon points="${dataPts}" fill="var(--vitality-fill)" stroke="var(--vitality-stroke)" stroke-width="1.8" stroke-linejoin="round"/>`;

  // 4 个顶点亮点（带 glow filter）
  const vertices = VITALITY_AXES.map((a) => {
    const p = ptOnAxis(a, values[a.key]);
    return `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="3" fill="var(--vitality-vertex)" filter="url(#vitality-glow)"/>`;
  }).join('');

  // 轴标签（外侧）
  const labels = VITALITY_AXES.map((a) => {
    const rad = a.angle * Math.PI / 180;
    const lx = cx + (R + 14) * Math.cos(rad);
    const ly = cy + (R + 14) * Math.sin(rad);
    const cosA = Math.cos(rad);
    const anchor = Math.abs(cosA) < 0.1 ? 'middle' : (cosA > 0 ? 'start' : 'end');
    return `<text x="${lx.toFixed(1)}" y="${(ly + 3).toFixed(1)}" text-anchor="${anchor}" class="vitality-axis-label">${a.label}</text>`;
  }).join('');

  const filter = `<defs><filter id="vitality-glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="2.2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>`;

  return `<svg viewBox="0 0 144 144" class="vitality-radar" aria-hidden="true">${filter}${grids}${axisLines}${dataPoly}${vertices}${labels}</svg>`;
}

/** 生命力图例：紧凑数值列表（雷达图主视觉 + 数值作脚注，不喧宾夺主） */
function vitalityLegendHTML(node) {
  const d = vitalityDims(node);
  const authRaw = (Number(node.authority) || 0).toFixed(4);
  const freshRaw = FRESHNESS_LABELS[node.freshness] || node.freshness || '—';
  const confRaw = parseConfidence(node.confidence).label;
  const statRaw = STATUS_LABELS[node.status] || node.status || '—';
  // raw 统一 escapeHtml：auth/conf 是 toFixed 数字（安全），fresh/stat 可能 fallback 到原始字段需防御
  const row = (label, raw, score) => `
    <li class="vit-row">
      <span class="vit-name">${label}</span>
      <span class="vit-raw">${escapeHtml(String(raw))}</span>
      <span class="vit-bar"><span class="vit-bar-fill" style="width:${Math.round(score * 100)}%"></span></span>
      <span class="vit-score">${Math.round(score * 100)}</span>
    </li>`;
  return `
    <ul class="vitality-legend">
      ${row('权威', authRaw, d.auth)}
      ${row('新鲜', freshRaw, d.fresh)}
      ${row('置信', confRaw, d.conf)}
      ${row('状态', statRaw, d.stat)}
    </ul>`;
}

// ===== v2.2 升格轨迹（detail · note→concept→decision→dossier 成长路径可视化）=====
// 信息设计加工：把孤立的 kind 标签还原为"知识在成长路径上的位置"——
// 已沉淀几阶段 / 当前在哪 / 可去向哪。让"概念/笔记/档案"不再是平铺的标签。
function promotionTrackHTML(node) {
  const idx = PROMO_CHAIN.indexOf(node.kind);

  // 非升格链节点（source/view 等）：4 阶段灰显 + 提示
  if (idx === -1) {
    const stages = PROMO_CHAIN.map((k, i) => {
      const x = 30 + i * 73;
      const line = i < PROMO_CHAIN.length - 1
        ? `<line x1="${x + 7}" y1="30" x2="${x + 66}" y2="30" stroke="var(--promo-line)" stroke-width="1" stroke-dasharray="2 3"/>`
        : '';
      return `
        <circle cx="${x}" cy="30" r="7" fill="none" stroke="var(--promo-future)" stroke-width="1.2" stroke-dasharray="2 2.5"/>
        <text x="${x}" y="50" text-anchor="middle" class="promo-stage-label promo-future">${KIND_LABELS[k]}</text>
        ${line}`;
    }).join('');
    return `
      <div class="promo-track promo-offchain">
        <div class="promo-narrative muted">该节点为 <strong>${escapeHtml(KIND_LABELS[node.kind] || node.kind)}</strong> · 不在 note→dossier 升格链上</div>
        <svg viewBox="0 0 296 60" class="promo-svg" aria-hidden="true">${stages}</svg>
      </div>`;
  }

  // 在升格链：高亮当前位置，紫显已达到，灰显未到
  const reachedCount = idx + 1;
  const totalCount = PROMO_CHAIN.length;
  const stages = PROMO_CHAIN.map((k, i) => {
    const x = 30 + i * 73;
    const reached = i < idx;
    const current = i === idx;
    const fill = current ? 'var(--promo-current)' : (reached ? 'var(--promo-reached)' : 'var(--promo-future)');
    const r = current ? 11 : (reached ? 7 : 6);
    const filter = current ? ' filter="url(#promo-glow)"' : '';
    const labelCls = current ? 'promo-current' : (reached ? 'promo-reached' : 'promo-future');
    // 连线到下一阶段（i → i+1）
    const nextCurrent = (i + 1) === idx;
    const nextReached = (i + 1) < idx;
    const lineEnd = x + 73 - (nextCurrent ? 11 : (nextReached ? 7 : 6));
    const lineStart = x + r;
    const line = i < PROMO_CHAIN.length - 1
      ? `<line x1="${lineStart}" y1="30" x2="${lineEnd}" y2="30"
          stroke="${reached ? 'var(--promo-reached)' : 'var(--promo-line)'}"
          stroke-width="${reached ? 1.6 : 1.2}"
          ${reached ? '' : 'stroke-dasharray="3 3"'}/>`
      : '';
    return `
      <circle cx="${x}" cy="30" r="${r}" fill="${fill}" stroke="${fill}" stroke-width="${current ? 2 : 1}"${filter}/>
      <text x="${x}" y="52" text-anchor="middle" class="promo-stage-label ${labelCls}">${KIND_LABELS[k]}</text>
      ${line}`;
  }).join('');

  const fromLabel = idx > 0 ? `来自 <strong>${KIND_LABELS[PROMO_CHAIN[idx - 1]]}</strong>` : '<span class="muted">起点 · 草记阶段</span>';
  const toLabel = idx < PROMO_CHAIN.length - 1
    ? `可升格至 <strong>${KIND_LABELS[PROMO_CHAIN[idx + 1]]}</strong>`
    : '<span class="muted">终点 · 已沉淀为档案</span>';

  return `
    <div class="promo-track">
      <div class="promo-narrative">${fromLabel} <span class="promo-sep">→</span> ${toLabel}</div>
      <svg viewBox="0 0 296 64" class="promo-svg" aria-hidden="true">
        <defs>
          <filter id="promo-glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="3" result="b"/>
            <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
          </filter>
        </defs>
        ${stages}
      </svg>
      <div class="promo-progress muted">已沉淀 <strong>${reachedCount}/${totalCount}</strong> 阶段 · ${Math.round((reachedCount / totalCount) * 100)}%</div>
    </div>`;
}

// ===== 右栏：生命力 + 升格轨迹 + 正文 + 关系子图 =====
function renderMain(node) {
  const el = root.querySelector('[data-main]');
  el.innerHTML = `
    <section class="panel-section">
      <div class="panel-title">知识生命力</div>
      <div class="vitality-wrap">
        ${vitalityRadarSVG(node)}
        ${vitalityLegendHTML(node)}
      </div>
      <div class="vitality-foot muted">ID · <span class="vit-id">${escapeHtml(node.id)}</span></div>
    </section>
    <section class="panel-section">
      <div class="panel-title">升格轨迹</div>
      ${promotionTrackHTML(node)}
    </section>
    <section class="panel-section">
      <div class="panel-title">正文</div>
      <article class="prose" data-prose></article>
    </section>
    <section class="panel-section">
      <div class="detail-section-head">
        <span>关系子图 · ${SUBGRAPH_HOPS} 跳邻域</span>
        <span class="muted" data-subgraph-count></span>
      </div>
      <div class="subgraph-wrap">
        <div class="subgraph" data-subgraph></div>
        <div class="subgraph-empty muted" data-subgraph-empty hidden>孤立节点 · 无关系</div>
      </div>
    </section>`;

  renderProse(node);
  renderSubgraph(node);
}

/**
 * v2 正文渲染：data.js body 字段 → markdown → HTML。
 * - body 存在：同步先用简易解析出内容（立即可见），异步加载 marked 成功后增强重渲
 * - body 缺失：回退原 demo 拼装（保持兼容）
 * @param {{title:string, body?:string, domain?:string, slug?:string, search_terms?:string}} node
 */
function renderProse(node) {
  const el = root.querySelector('[data-prose]');
  if (!el) return;

  if (node.body && node.body.trim()) {
    // 1. 同步：简易解析立即出内容（渐进增强，避免空白等待 CDN）
    el.innerHTML = simpleMarkdownToHtml(node.body);
    // 2. 异步：marked 加载成功后用更完整 GFM 解析重渲
    const session = mountSession;
    loadMarked().then((ok) => {
      if (!ok) return;
      if (session !== mountSession || !root) return;   // 已切走
      const el2 = root.querySelector('[data-prose]');
      if (el2 && window.marked) {
        try {
          el2.innerHTML = window.marked.parse(node.body);
        } catch (e) {
          console.warn('[detail] marked.parse 异常，保留简易解析', e);
        }
      }
    });
  } else {
    // 无 body：原 demo 拼装兜底（保留以兼容旧数据）
    const terms = node.search_terms || node.slug || '';
    const domain = node.domain && node.domain !== '_unsorted' ? node.domain : '未归类';
    el.innerHTML = `
      <p class="prose-lead">${escapeHtml(node.title)}</p>
      <p>所属域：<code>${escapeHtml(domain)}</code></p>
      ${terms ? `<p>检索词：<code>${escapeHtml(terms)}</code></p>` : ''}
      <p class="prose-hint muted">该条目暂无正文 body 字段。</p>`;
  }
}

/** 关系子图：以当前节点为中心 N 跳邻域，独立 cy 实例 */
function renderSubgraph(node) {
  const container = root.querySelector('[data-subgraph]');
  const emptyEl = root.querySelector('[data-subgraph-empty]');
  const countEl = root.querySelector('[data-subgraph-count]');

  const rels = index.edgeIndex.get(node.id) || [];
  if (!rels.length) {
    container.hidden = true;
    if (emptyEl) emptyEl.hidden = false;
    if (countEl) countEl.textContent = '0 节点';
    return;
  }

  const idSet = new Set(bfsHops(node.id, SUBGRAPH_HOPS, SUBGRAPH_MAX_NODES));

  const cyNodes = [];
  idSet.forEach((id) => {
    const n = getNodeById(graph, id);
    if (!n) return;
    const isCenter = id === node.id;
    cyNodes.push({
      data: {
        id: n.id,
        label: trunc(n.title, 12),
        kind: n.kind,
        color: KIND_COLORS[n.kind] || '#897989',
        size: isCenter ? 34 : 22,
      },
      classes: isCenter ? 'center' : '',
    });
  });
  const cyEdges = graph.edges
    .filter((e) => idSet.has(e.source) && idSet.has(e.target))
    .map((e) => ({
      data: {
        id: `sg-${e.source}->${e.target}:${e.type}`,
        source: e.source,
        target: e.target,
        color: EDGE_COLORS[e.type] || '#897989',
        lineStyle: EDGE_STYLE[e.type] || 'solid',
      },
    }));

  if (countEl) countEl.textContent = `${cyNodes.length} 节点 · ${cyEdges.length} 边`;

  cy = window.cytoscape({
    container,
    elements: cyNodes.concat(cyEdges),
    style: SUBGRAPH_STYLE,
    wheelSensitivity: 0.2,
    minZoom: 0.3,
    maxZoom: 2.5,
  });
  // 点子图节点 → 跳目标详情（中心节点除外）
  cy.on('tap', 'node', (evt) => {
    const id = evt.target.id();
    if (id !== node.id) actions.openDetail(id);
  });
  currentLayout = cy.layout(COSE_LAYOUT);
  currentLayout.run();
}

/** BFS N 跳邻域：返回含 startId 的 id 数组（受 maxNodes 截断） */
function bfsHops(startId, maxHops, maxNodes) {
  const visited = new Set([startId]);
  let frontier = [startId];
  for (let h = 0; h < maxHops; h++) {
    const next = [];
    for (const id of frontier) {
      const rels = index.edgeIndex.get(id) || [];
      for (const r of rels) {
        const other = r.source === id ? r.target : r.source;
        if (!visited.has(other)) {
          visited.add(other);
          next.push(other);
          if (visited.size >= maxNodes) return [...visited];
        }
      }
    }
    if (!next.length) break;
    frontier = next;
  }
  return [...visited];
}

// ===== 视图生命周期 =====
export function mount(container, params = {}) {
  root = container;
  root.innerHTML = '';
  root.classList.add('view-detail');
  mountSession++;   // 新会话：loadMarked 异步回调凭此判断是否仍有效

  graph = loadGraph();
  if (!graph) {
    root.innerHTML = loadErrorHTML();
    return;
  }
  index = buildIndex(graph);

  // 取节点 id：路由 param 优先，否则 store.selectedNodeId
  const id = params.id || store.getState().selectedNodeId;
  const node = id ? getNodeById(graph, id) : null;

  if (!node) {
    root.innerHTML = id ? notFoundCard(id) : noSelectionCard();
    bindGuideActions();
    return;
  }

  root.innerHTML = shellHTML();
  renderHeader(node);
  renderAside(node);
  renderMain(node);

  // v2.8：弹窗关闭交互——× 按钮 / 点遮罩 / ESC 三路都走 actions.closeDetail()。
  // closeDetail 内部 navigate 回主视图路由（store.currentView），router notify →
  // app.js switchView 非 detail 分支 → closeDetailModal 卸载弹窗 + 显示主视图。
  const closeBtn = root.querySelector('[data-close]');
  if (closeBtn) closeBtn.addEventListener('click', () => actions.closeDetail());
  const backdrop = root.querySelector('[data-backdrop]');
  if (backdrop) backdrop.addEventListener('click', () => actions.closeDetail());
  // ESC 键关闭（a11y：模态对话框标准交互）。监听挂 window，unmount 移除。
  keydownHandler = (e) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      actions.closeDetail();
    }
  };
  window.addEventListener('keydown', keydownHandler);

  // 同步 store：切回 graph 视图时可高亮当前节点（currentView 由 app.js switchView 设置）
  actions.focusNode(node.id);
}

export function unmount() {
  timers.forEach((t) => window.clearTimeout(t));
  timers.clear();
  // v2.8：移除 ESC 监听（防止卸载后仍拦截键盘）
  if (keydownHandler) {
    window.removeEventListener('keydown', keydownHandler);
    keydownHandler = null;
  }
  // 与 graph 视图一致：先停布局 + 静默 cy，避免 destroy 后 RAF 残留报错
  if (currentLayout && typeof currentLayout.stop === 'function') {
    try { currentLayout.stop(); } catch (e) { /* layout 可能已结束 */ }
  }
  currentLayout = null;
  if (cy) {
    try { cy.silent(true); } catch (e) { /* ignore */ }
    try { cy.remove(cy.elements()); } catch (e) { /* ignore */ }
    try { cy.destroy(); } catch (e) { /* ignore */ }
    cy = null;
  }
  if (root) { root.innerHTML = ''; root.classList.remove('view-detail'); }
  root = null;
  graph = null;
  index = null;
}
