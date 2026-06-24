// lib/store.js · 共享状态（当前视图 / 选中节点 / 筛选 / 顶栏统计）
// 视图订阅 store 实现联动：图谱点节点 → store.setSelected → 详情/搜索视图响应；
// 图谱可见集合变化 → store.setStats → app.js 订阅回写顶栏 DOM（不再走 window 全局耦合）。
// 极简 pub-sub；不引入框架依赖，符合 vanilla ES module 约束。

const listeners = new Set();
let state = {
  // 当前激活视图名（graph / search / detail / dashboard），与路由保持同步
  currentView: null,
  // 当前选中节点 id（图谱点击 / 搜索结果点击 / 详情关系点击都写这里）
  selectedNodeId: null,
  // 图谱筛选态（kind 启用表 / 隐藏孤立 / 显示标签），仅图谱视图消费
  filters: {
    kinds: {},          // { concept: true, dossier: true, ... }
    hideIsolated: true,
    showLabels: true,
  },
  // 最近一次搜索词（供图谱高亮联动用，本轮搜索视图占位）
  lastQuery: null,
  // 顶栏可见统计（graph 视图按可见集合回写，app.js 订阅驱动顶栏 DOM）
  stats: {
    nodeCount: 0,
    edgeCount: 0,
    isolated: 0,
  },
};

export function getState() {
  return state;
}

export function setState(patch) {
  if (!patch || typeof patch !== 'object') return;
  const next = { ...state };
  for (const key of Object.keys(patch)) {
    if (key === 'filters' && patch.filters && typeof patch.filters === 'object') {
      next.filters = { ...state.filters, ...patch.filters };
    } else if (key === 'stats' && patch.stats && typeof patch.stats === 'object') {
      next.stats = { ...state.stats, ...patch.stats };
    } else {
      next[key] = patch[key];
    }
  }
  const prev = state;
  state = next;
  if (prev !== next) {
    listeners.forEach((fn) => {
      try { fn(state, prev); } catch (err) { console.error('[store] listener error', err); }
    });
  }
}

export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function setSelected(nodeId, { view } = {}) {
  setState({ selectedNodeId: nodeId ?? null, ...(view ? { currentView: view } : {}) });
}

export function setFilters(patch) {
  setState({ filters: { ...getState().filters, ...patch } });
}

export function setStats(patch) {
  setState({ stats: { ...getState().stats, ...patch } });
}
