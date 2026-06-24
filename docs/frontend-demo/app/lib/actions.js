// lib/actions.js · 视图跳转与选中状态的统一入口（Phase 6 演进② · 架构 P0 解耦）
// 收敛视图对 router/store 的直接依赖，依赖方向：views → actions → router/store
// 收益：路由策略切换 / 选中语义变更只在单点修改；埋点、动画过渡、日志等横切能力可在此层注入。
// v2.8：detail 从独立路由视图改为 modal 弹窗。openDetail 仍 navigate(#/detail/:id) 触发路由，
//       app.js switchView 识别 detail 走弹窗分支（不卸载主视图）；closeDetail navigate 回主视图。
//       selectedNodeId 同步保留（切回 graph 可高亮），但不再写 currentView='detail'
//       （currentView 始终是主视图名，供 closeDetail 回退）。

import * as router from '../router.js';
import * as store from './store.js';

/**
 * 打开详情弹窗（路由 navigate + 同步 selectedNodeId）。
 * selectedNodeId 同步后，切回 graph 视图时可高亮当前节点；
 * 路由变化触发 app.js switchView detail 分支 → 唤起弹窗（主视图不卸载）。
 * @param {string} id 节点 id（falsy 直接返回，无操作）
 */
export function openDetail(id) {
  if (!id) return;
  store.setSelected(id);   // 仅同步选中节点；不写 currentView（保持主视图名供 closeDetail 回退）
  router.navigate(`#/detail/${encodeURIComponent(id)}`);
}

/**
 * 关闭详情弹窗：navigate 回当前主视图路由。
 * 主视图名取自 store.currentView（openDetail 期间不被改写）；缺失兜底 dashboard
 * （如用户直接刷新在 #/detail/:id 时 currentView 为空）。
 */
export function closeDetail() {
  const main = store.getState().currentView || 'dashboard';
  router.navigate(`#/${main}`);
}

/**
 * 聚焦节点（仅同步 selectedNodeId，不跳路由）。
 * 供图谱点节点联动（图内浮层显示 + 选中态同步）、
 * 详情视图 mount 后声明当前展示节点等"非跳转"场景使用。
 * @param {string|null|undefined} id 节点 id；传 null/undefined 清除选中
 */
export function focusNode(id) {
  store.setSelected(id ?? null);
}

/** 清除选中节点（语义化别名，等价 focusNode(null)）。 */
export function clearFocus() {
  store.setSelected(null);
}

/**
 * 打开搜索视图。
 * @param {string} [query] 可选搜索词；传入则同步 store.lastQuery，
 *                         供搜索视图 mount 时回填输入框、图谱高亮联动
 */
export function openSearch(query) {
  if (query != null) store.setState({ lastQuery: query });
  router.navigate('#/search');
}

/** 打开图谱视图。 */
export function openGraph() {
  router.navigate('#/graph');
}

/** 打开仪表盘视图。 */
export function openDashboard() {
  router.navigate('#/dashboard');
}
