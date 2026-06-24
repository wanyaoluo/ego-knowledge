// app.js · 入口编排：路由分发 + 主视图挂载 + detail 弹窗编排
// 通过 ES module 静态导入所有视图；主视图按路由切换挂载/卸载，避免 cytoscape 实例残留。
// detail 自 v2.8 起从顶栏 tab / 独立路由视图改为 modal overlay：任意视图点节点唤起，
// × 关闭回主视图（store.currentView），后退走浏览器历史。主视图在弹窗期间不被卸载。
import * as graphView from './views/graph/index.js';
import * as searchView from './views/search/index.js';
import * as detailView from './views/detail/index.js';
import * as dashboardView from './views/dashboard/index.js';
import * as structureMapView from './views/structure-map/index.js';
import * as galleryView from './views/gallery/index.js';
import * as readerView from './views/reader/index.js';
import * as timelineView from './views/timeline/index.js';
import * as reviewView from './views/review/index.js';
import * as exploreView from './views/explore/index.js';
import * as promotionRiverView from './views/promotion-river/index.js';
import * as router from './router.js';
import * as store from './lib/store.js';
import { loadGraph, buildIndex } from './lib/data-layer.js';

// 主视图表（不含 detail：detail 走弹窗分支，不作为主视图挂载）
const MAIN_VIEWS = {
  graph: graphView,
  search: searchView,
  dashboard: dashboardView,
  'structure-map': structureMapView,
  gallery: galleryView,
  reader: readerView,
  timeline: timelineView,
  review: reviewView,
  explore: exploreView,
  'promotion-river': promotionRiverView,
};

let viewRoot = null;            // 主视图挂载点（#view-root）
let modalRoot = null;           // detail 弹窗挂载点（#detail-modal）
let tabsEl = null;              // 顶栏 tab 容器
let currentView = null;         // 当前激活【主视图】名（弹窗期间保持，不切到 detail）
let currentModule = null;       // 当前主视图模块（用于 unmount）
let detailOpen = false;         // detail 弹窗是否打开

// 顶栏统计 DOM 引用缓存（模块私有；store.stats 变化时由订阅写入，不再走 window 全局耦合）
const statsEls = { node: null, edge: null, iso: null };

function cacheStatsEls() {
  statsEls.node = document.getElementById('stats-node');
  statsEls.edge = document.getElementById('stats-edge');
  statsEls.iso = document.getElementById('stats-iso');
}

function renderStats(stats) {
  if (!stats) return;
  if (statsEls.node) statsEls.node.textContent = String(stats.nodeCount ?? 0);
  if (statsEls.edge) statsEls.edge.textContent = String(stats.edgeCount ?? 0);
  if (statsEls.iso) statsEls.iso.textContent = String(stats.isolated ?? 0);
}

function initTopStats() {
  // 预先填全量统计到 store（graph 视图 mount 后会按可见集合覆写）
  const graph = loadGraph();
  if (!graph) return;
  const index = buildIndex(graph);
  store.setStats({
    nodeCount: graph.nodes.length,
    edgeCount: graph.edges.length,
    isolated: index.isolatedCount,
  });
}

function highlightTab(view) {
  if (!tabsEl) return;
  tabsEl.querySelectorAll('.nav-tab').forEach((tab) => {
    const isActive = tab.dataset.view === view;
    // a11y：除视觉 active class 外，加 aria-current="page" 告知 AT 当前视图
    // （WCAG 2.4.8 Location / G63；屏幕阅读器会朗读"当前页面"）
    tab.classList.toggle('active', isActive);
    if (isActive) {
      tab.setAttribute('aria-current', 'page');
    } else {
      tab.removeAttribute('aria-current');
    }
  });
}

// ===== detail 弹窗编排 =====
// openDetail(id) → navigate(#/detail/:id) → router notify → switchView('detail', {id})
// → openDetailModal：挂载 detail 模块到 #detail-modal，主视图保持不变。
function openDetailModal(params) {
  if (!modalRoot) return;
  // detail→detail（弹窗内点关系换节点）：先卸载旧实例（清 cy/timers），再 mount 新内容
  if (detailOpen) {
    try { detailView.unmount(); } catch (err) { console.error('[app] detail unmount error', err); }
  }
  try {
    detailView.mount(modalRoot, params || {});
    modalRoot.hidden = false;
    detailOpen = true;
  } catch (err) {
    console.error('[app] detail modal mount 失败', err);
    modalRoot.hidden = true;
    detailOpen = false;
  }
}

function closeDetailModal() {
  if (!detailOpen) return;
  try { detailView.unmount(); } catch (err) { console.error('[app] detail unmount error', err); }
  if (modalRoot) {
    modalRoot.innerHTML = '';
    modalRoot.hidden = true;
  }
  detailOpen = false;
}

async function switchView(view, params) {
  // detail 走弹窗分支：不卸载主视图，只挂载/更新弹窗
  if (view === 'detail') {
    openDetailModal(params);
    return;
  }
  // 非 detail 视图：先关弹窗（若开着），再切主视图
  if (detailOpen) closeDetailModal();

  if (!view || !MAIN_VIEWS[view]) {
    console.warn('[app] 未知视图，回退 dashboard', view);
    view = 'dashboard';
  }
  if (currentView === view) {
    // 同主视图重复进入：不重挂载（主视图内部状态自管）
    return;
  }
  // 卸载旧主视图
  if (currentModule && typeof currentModule.unmount === 'function') {
    try { currentModule.unmount(viewRoot); } catch (err) { console.error('[app] unmount error', err); }
  }
  // 挂载新主视图
  const mod = MAIN_VIEWS[view];
  try {
    mod.mount(viewRoot, params || {});
    currentModule = mod;
    currentView = view;
    highlightTab(view);
    store.setState({ currentView: view });
  } catch (err) {
    console.error(`[app] mount ${view} 失败`, err);
  }
}

function init() {
  viewRoot = document.getElementById('view-root');
  modalRoot = document.getElementById('detail-modal');
  tabsEl = document.querySelector('.nav-tabs');
  if (!viewRoot) {
    console.error('[app] #view-root 未找到');
    return;
  }
  if (!modalRoot) {
    console.error('[app] #detail-modal 未找到');
    return;
  }
  cacheStatsEls();
  // 订阅 store.stats → 顶栏 DOM（graph 视图回写可见集合时驱动顶栏更新）
  store.subscribe((next) => {
    if (next.stats) renderStats(next.stats);
  });
  initTopStats();

  // 订阅路由变化
  router.subscribe((parsed) => {
    switchView(parsed.view, parsed.params);
  });

  // 首次按当前 hash 挂载
  const initial = router.parse();
  switchView(initial.view, initial.params);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
