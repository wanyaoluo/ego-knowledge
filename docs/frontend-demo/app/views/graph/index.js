// views/graph/index.js · 图谱视图（Phase 2 · ek-graph-upgrade）
// 升级项：fcose 布局 / compound 父节点 / 收敛控件(depth+kind+隐藏孤立) / hover 邻域高亮 / minimap
// 保留功能：kind 着色 / authority 大小 / 详情面板 / 重新布局 / 适应视图
// 注意：CSS 在 index.html 用 <link> 引入（无 build 环境 ES module 不支持 import css）
// 降级策略：fcose 扩展走 CDN 动态注入，加载/运行失败自动回退 cose（demo 不中断）
import {
  KIND_COLORS, KIND_LABELS, EDGE_COLORS, EDGE_STYLE, EDGE_LABELS,
  STATUS_LABELS, FRESHNESS_LABELS,
  loadGraph, buildIndex, toElements, authSize, authPct, authNorm, trunc,
} from '../../lib/data-layer.js';
import { escapeHtml } from '../../lib/escape.js';
import * as store from '../../lib/store.js';
import * as actions from '../../lib/actions.js';

let cy = null;
let graph = null;
let index = null;
let activeKinds = {};        // { kind: boolean }
let root = null;             // 视图根元素
let currentLayout = null;    // 当前布局引用（unmount 时 stop，避免 destroy 后 layout 帧仍 notify）
const timers = new Set();    // mount 期间设置的 timer，unmount 时统一清

// === Phase 2 新增状态 ===
let fcoseReady = false;      // fcose 扩展是否已注册成功
let fcoseLoading = null;     // fcose 加载 Promise（去重，避免重复注入）
let mountSession = 0;        // mount 会话标识，防止 loadFcose 回调跨会话误执行
let depthValue = 0;          // 收敛：最小度数阈值（0=不限制）
let depthDebounce = null;    // depth slider 防抖 timer（input 事件高频，停顿后才 refilter）
let compoundEnabled = false; // [v2.4 减负] 关闭 compound 分组：方块容器看不懂 + 拖父节点连片

// === v2.8 d3-force 持续物理引擎状态 ===
// cytoscape fcose 是静态布局（跑一次即停），无法支持「拖拽实时排斥 / 弹簧拉扯 / 孤立散布」
// 等持续物理行为。引入 d3-force（CDN）做持续物理驱动，cytoscape 仅保留渲染/交互/层次钻取。
// 数据流：d3-force tick → cy.batch 同步节点位置（合并 redraw）；拖拽 fx/fy 锁定 + alphaTarget reheat。
let d3Ready = false;           // d3-force 是否已就绪（CDN 加载成功 + forceSimulation 可用）
let d3Loading = null;          // d3-force 加载 Promise（去重）
let simulation = null;         // d3-force simulation 实例（null=未运行）
let simNodes = [];             // d3 节点数组（与 cy 节点 1:1，引用稳定，d3-force 直接 mutate）
let simLinks = [];             // d3 链接数组（source/target 在 forceLink.id 解析后变为对象引用）
let simNodeMap = new Map();    // cy_id → d3 node 对象（O(1) 查找，避免每次 tick 遍历）
let draggingNodeId = null;     // 当前正在拖拽的节点 id（tick 时跳过 cy.batch 更新该节点，
                               // 避免覆盖 cytoscape 鼠标控制的位置）
// minimap 运行态
let minimapCtx = null;
let minimapW = 0;            // canvas CSS 宽（逻辑像素）
let minimapH = 0;
let minimapRaf = 0;          // rAF 节流句柄

// [v2.5 克制重做] 移除星空粒子 / 节点 glow underlay / 边流光（黑曜石调研：高级感来自克制不是发光）。
//             仅保留深紫琥珀纯底色 + 物理/弹簧交互 + confidence 边框 + 聚焦隔离。

// === v2.3 动效打磨：spring 入场 / hover 弹性 / 布局过渡 / 聚焦 spring / 惯性 ===
// 统一原则：rAF 驱动 + dt 钳制（切后台不爆帧）；闭式 spring/easing，不阻塞主线程；
//          reduced-motion 时 motionReduced 为真 → 所有动效归零（即时到位），仅留状态变化。
let motionReduced = false;      // mount 时读一次 prefers-reduced-motion
let entranceRaf = 0;            // 入场/淡入 stagger rAF
let entranceItems = [];        // 当前入场中的节点项（中断/卸载时 snap 到终态，避免卡中间态）
let layoutTweenRaf = 0;         // 重新布局位置过渡 rAF
let focusRaf = 0;               // 聚焦 pan/zoom spring rAF
let wheelZoomRaf = 0;           // wheel 惯性缩放 rAF
let panInertiaRaf = 0;          // 拖拽平移惯性 rAF
let l1ConvergeRaf = 0;          // L1 收敛感知 fit 轮询 rAF（d3-force 准稳态后 refit）
let bgFollowRaf = 0;            // 背景视差跟随 rAF（pan/zoom → 写 CSS 变量到 .graph-canvas）
let hoverCtl = null;            // hover 弹性控制器（{stop, snapBase}）
// wheel 惯性运行态
let wheelTarget = 0, wheelCurrent = 0, wheelAnchor = null;
// pan 惯性运行态
let panVel = { x: 0, y: 0 };
let panSampling = false;        // 用户正在拖拽背景（采样 pan 事件）
let panInerting = false;        // 惯性驱动 panBy 中（跳过采样避免自反馈）

const ENTRANCE_STAGGER = 0.012; // 每节点错峰秒（141 节点 ≈ 1.7s 全展开）
const ENTRANCE_DUR = 0.62;      // 单节点入场时长（秒）
const LAYOUT_TWEEN_DUR = 540;   // 重新布局过渡毫秒
const FOCUS_DUR = 520;          // 聚焦动画毫秒
const FOCUS_ZOOM = 1.15;        // 聚焦目标缩放（仅放大不缩小）
const PAN_INERTIA_K = 4.2;      // 平移惯性衰减系数（越大停得越快）
const PAN_INERTIA_MIN = 0.02;   // 速度低于该值停止（px/ms）

// === v2.7 层次钻取状态 ===
// 三层信息密度：L1 主题地图（域聚合气泡）/ L2 域子图（单域节点）/ L3 节点聚焦（tap 隔离）。
// viewMode='theme'（默认入口）走层次；'full' 降级为原全量力导向（标注「节点多会乱」）。
// currentDomain 非 null 时 theme 模式进入 L2（该域子图）。focusedNodeId 非 null 时叠加 L3 聚焦。
let viewMode = 'theme';          // 'theme' | 'full'
let currentDomain = null;        // L2 当前域（null=未钻取）
let focusedNodeId = null;        // L3 聚焦节点 id（null=未聚焦）
let focusedTitle = '';           // L3 聚焦节点标题（面包屑展示）
let domainMap = null;            // 域聚合缓存：[{domain,count,avgAuth,authNorm,nodes}] 按 count 降序

// [v2.10] pinnedNodes 原为 B3 拖拽锁定用；拖拽 pin 已移除（皮筋回弹语义优先），
//         此 map 保留为防御性 no-op（applyPinnedToSimulation/restorePinnedPositions 在空 map 上为 no-op）。
let pinnedNodes = new Map();

const GRAPH_HTML = `
<div class="graph-canvas" data-cy></div>

<aside class="panel panel-left">
  <!-- 视图模式切换（紧凑分段控件）：主题地图（层次钻取 L1→L2→L3）/ 全图（全量力导向）。
       从原顶部居中 nav 栏移入左面板顶部，常驻可见，不与返回按钮挤一长条。 -->
  <div class="panel-section panel-mode" data-mode-section>
    <div class="panel-title">视图模式</div>
    <div class="gnav-mode" role="group" aria-label="视图模式切换">
      <button type="button" class="gnav-mode-btn" data-mode="theme" aria-pressed="true">主题地图</button>
      <button type="button" class="gnav-mode-btn" data-mode="full" aria-pressed="false"
              title="展示全部 189 节点 · 节点多会乱">全图</button>
    </div>
  </div>
  <!-- L1 主题地图：知识领地列表（点击进入域子图，键盘可达替代路径） -->
  <div class="panel-section" data-l1-section hidden>
    <div class="panel-title">知识领地 · 点击进入</div>
    <div class="domain-list" data-domain-list role="list"
         aria-label="知识领地列表，点击或 Enter 进入域子图"></div>
    <div class="node-list-meta muted" data-domain-meta></div>
    <div class="l1-legend">
      <div class="l1-legend-row"><span class="l1-legend-size" aria-hidden="true"></span><span>领地大小 = 节点数</span></div>
      <div class="l1-legend-row"><span class="l1-legend-glow" aria-hidden="true"></span><span>暖度 / 势力范围 = 平均权威度</span></div>
      <div class="l1-legend-row"><span class="l1-legend-edge" aria-hidden="true"></span><span>连线粗细 = 跨域关系数</span></div>
      <div class="l1-legend-row"><span aria-hidden="true" style="display:inline-block;width:14px;height:14px;border:1.5px dashed #7a6b7a;border-radius:50%;vertical-align:middle"></span><span>虚线轮廓 = 待连接领地</span></div>
    </div>
  </div>
  <div class="panel-section" data-node-section>
    <div class="panel-title">类型筛选</div>
    <div class="kind-filters" data-kind-filters></div>
  </div>
  <div class="panel-section" data-node-section>
    <div class="panel-title">关系深度</div>
    <div class="depth-control">
      <input type="range" min="0" max="5" step="1" value="0" data-depth-slider class="depth-slider"
             aria-label="关系深度（最小连接度阈值）">
      <div class="depth-scale"><span>0</span><span>1</span><span>2</span><span>3</span><span>4</span><span>5</span></div>
      <div class="depth-meta">
        <span class="muted">最小连接度</span>
        <span class="depth-val" data-depth-val>0 · 全部</span>
      </div>
    </div>
  </div>
  <div class="panel-section" data-node-section>
    <div class="panel-title">显示</div>
    <label class="toggle">
      <input type="checkbox" data-hide-isolated checked>
      <span class="toggle-track"></span>
      <span class="toggle-text">隐藏孤立节点</span>
    </label>
    <label class="toggle">
      <input type="checkbox" data-show-labels checked>
      <span class="toggle-track"></span>
      <span class="toggle-text">显示标签</span>
    </label>
  </div>
  <div class="panel-section panel-actions">
    <button class="btn" data-relayout type="button">重新布局</button>
    <button class="btn btn-ghost" data-fit-view type="button">适应视图</button>
  </div>
  <div class="panel-section" data-node-section>
    <div class="panel-title">关系图例</div>
    <div class="legend" data-legend></div>
  </div>

  <!-- a11y：键盘可达的节点导航区（替代 canvas 节点的键盘路径）。
       cytoscape canvas 无 DOM，键盘用户通过本列表 Tab/Enter 即可聚焦到画布对应节点。
       容器为普通 div + aria-label；条目为原生 <button>（自带 Tab/Enter/Space）。
       不用 listbox/option 模式：未实现方向键导航，避免误导 AT 用户期望方向键行为。
       选中态用 aria-current="true" 标记，随图谱 tap 同步。 -->
  <div class="panel-section" data-node-section>
    <div class="panel-title">节点导航 · 键盘可达</div>
    <input type="search" data-node-search class="node-search"
           placeholder="筛选可见节点…"
           aria-label="筛选可见节点列表"
           autocomplete="off">
    <div class="node-list" data-node-list
         aria-label="可见节点列表，Enter 聚焦到画布对应节点"></div>
    <div class="node-list-meta muted" data-node-list-meta></div>
  </div>
</aside>

<aside class="panel panel-right" data-detail-panel hidden>
  <div class="detail-top">
    <span class="detail-kind" data-d-kind></span>
    <button class="detail-close" data-d-close type="button" aria-label="关闭">×</button>
  </div>
  <h2 class="detail-title" data-d-title></h2>
  <div class="detail-meta" data-d-meta></div>
  <div class="detail-section">
    <div class="detail-section-head">
      <span>权威度</span>
      <span class="authority-val" data-d-auth-val></span>
    </div>
    <div class="authority-bar"><div class="authority-fill" data-d-auth-fill></div></div>
  </div>
  <div class="detail-section">
    <div class="detail-section-head"><span>关联</span><span class="muted" data-d-rel-count></span></div>
    <div class="detail-relations" data-d-relations></div>
  </div>
  <!-- A1+A2：确定性详情入口。浮层给"瞄一眼"，本按钮给"深入读"，渐进式披露。
       主 CTA 视觉权重最高（樱粉主色 + 全宽 + 箭头），让用户一眼知道怎么跳详情。 -->
  <div class="detail-section detail-cta-group">
    <button class="btn btn-detail-entry" data-d-open-detail type="button">
      <span class="btn-detail-label">查看完整详情</span>
      <span class="btn-detail-arrow" aria-hidden="true">→</span>
    </button>
    <button class="btn btn-ghost btn-vsc-mini" data-d-open-vsc type="button" aria-label="复制节点路径">复制路径</button>
  </div>
</aside>

<div class="minimap-wrap" data-minimap-wrap aria-label="小地图" role="navigation">
  <canvas class="minimap" data-minimap></canvas>
  <span class="minimap-label">缩略图 · 可拖动</span>
</div>

<!-- 返回按钮（画布左上角标准位）：层级钻取时显示，label=返回目的地，简短不拉长。
     取代原居中一长条 graph-nav（面包屑 主题地图›域›聚焦 + 模式切换 + 退出聚焦，横向拉满，反直觉）。
     L2 域子图 → ← 主题地图（回 L1）；L3 聚焦 → ← {域}域（退出聚焦，回 L2）；L1/全图 → 隐藏（根级）。
     模式切换（主题地图/全图）已移入左面板顶部，不再挤占顶部。 -->
<div class="graph-back" data-graph-back role="navigation" aria-label="返回上级" hidden></div>

<div class="overlay" data-loading>
  <div class="loading-dot"></div><div class="loading-dot"></div><div class="loading-dot"></div>
  <span>载入知识图谱</span>
</div>
<div class="overlay overlay-error" data-load-error hidden>
  <span>数据加载失败</span>
  <span class="muted">不要双击 index.html（file:// 会禁止读取数据）</span>
  <span class="muted">请在浏览器地址栏访问 <code>http://localhost:5188</code></span>
</div>
`;

// fcose 布局（B1 防重叠 + B2 引力调优）：
//  - nodeRepulsion 22000 + nodeSeparation 55：cytoscape 无原生 collide，靠强排斥 + 节点分离间距
//    协同防重叠（大小不一的节点不再叠在一起）；
//  - edgeElasticity 0.55：边弹性提高让有连接的节点聚拢（相关簇靠近，引力语义）；
//  - gravity 0.15：适度降低向心，呼应 Obsidian Coulomb 反平方自然散布，减少线交叉「贼乱」感。
const FCOSE_LAYOUT = {
  name: 'fcose',
  quality: 'default',
  randomize: true,
  animate: false,          // 与 Phase 1 一致，规避 RAF 残留 notify 报错
  nodeRepulsion: 22000,    // 强排斥：大小不一的节点互相推开防重叠
  idealEdgeLength: 175,    // 相连节点间距
  edgeElasticity: 0.55,    // 边弹性：相关节点聚拢（引力）
  nestingFactor: 0.1,
  gravity: 0.15,           // 弱向心：cluster 自然散开
  numIter: 3000,
  nodeSeparation: 55,      // fcose 特有：节点间额外分离间距（防紧邻重叠）
  tile: true,              // 多连通分量分片（孤立节点打包到边缘）
  padding: 30,
};

// cose 降级布局（fcose 加载/运行失败时使用，无 nodeSeparation，靠强 nodeRepulsion 兜底）
const COSE_LAYOUT = {
  name: 'cose',
  animate: false,
  nodeRepulsion: 24000,
  idealEdgeLength: 185,
  edgeElasticity: 0.55,
  gravity: 0.18,
  numIter: 2500,
  fit: true,
  padding: 50,
  randomize: true,
  nodeDimensionsIncludeLabels: true,
  tile: false,
};

const CY_STYLE = [
  {
    selector: 'node',
    style: {
      label: 'data(label)',
      'text-valign': 'bottom',
      'text-halign': 'center',
      'text-margin-y': 6,
      color: '#c1b3c0',
      'font-size': '10px',
      'font-family': 'inherit',
      'text-wrap': 'ellipsis',
      'text-outline-color': '#120818',
      'text-outline-width': 3,
      // v2.5 克制：标签淡出由 zoom 驱动 text-opacity（updateLabelFade），不再用 min-zoomed-font-size 硬切
      'background-color': 'data(color)',
      width: 'data(size)',
      height: 'data(size)',
      'border-width': 0,
      'transition-property': 'border-width,border-style,opacity',
      'transition-duration': 160,
      // [v2.5 克制] 移除 underlay glow（黑曜石：节点纯色 fill，无外发光光晕）
    },
  },
  // [v2.10 克制微光] 仅高 authority 节点（injectNodeMeta 派生 glow 数据）渲染 underlay 微光。
  // 黑曜石克制：小 padding（4-8px）+ 低 opacity（0.15-0.25），非 v2 大光晕。
  // underlay 不与 border/opacity transition 冲突（独立属性），reduced-motion 下为静态（无动画）。
  // node[glow] 按"字段存在"匹配：仅 authNorm≥0.30 的高权威节点携带 glow，低权威节点保持纯色 fill。
  {
    selector: 'node[glow]',
    style: {
      'underlay-color': 'data(color)',
      'underlay-padding': 'data(glow)',
      'underlay-opacity': 'data(glowOp)',
      'underlay-shape': 'ellipse',
    },
  },
  // v2.3 入场/淡入：rAF 逐帧驱动 opacity+size 期间，临时关闭 cytoscape 内置 transition，
  // 否则 160ms transition 会与每帧 set 打架导致淡入发粘。动画结束移除该 class 即恢复。
  { selector: 'node.entering', style: { 'transition-property': 'none', 'transition-duration': 0 } },
  // v2.2 confidence → 边框质感（节点脾性维度之一）—— 信息设计保留
  // high 实线樱粉（已验证）/ medium 虚线紫（半信半疑）/ none 无边框（默认，未经评估）
  {
    selector: 'node[confClass = "high"]',
    style: {
      'border-color': '#e89bbb',
      'border-width': 2,
      'border-style': 'solid',
      'border-opacity': 0.92,
    },
  },
  {
    selector: 'node[confClass = "medium"]',
    style: {
      'border-color': '#b59ee6',
      'border-width': 1.5,
      'border-style': 'dashed',
      'border-opacity': 0.82,
    },
  },
  // [B4] 孤立节点（degree=0，未隐藏时）：dashed 灰色边框特殊显示，与普通节点区分
  //      （不堆在那无区分；线型+颜色双维度，色盲友好）。border 覆盖 confidence 边框（孤立语义优先）。
  //      边缘放置由 fcose tile 打包多连通分量实现（无连线的孤立节点自然分到边缘）。
  {
    selector: 'node[isolated]',
    style: {
      'border-style': 'dashed',
      'border-color': '#6b5d6b',
      'border-width': 1.5,
      'border-opacity': 0.6,
    },
  },
  // [v2.5 克制] 选中/聚焦态：仅用 border 强调（樱粉加粗），不再叠加 underlay glow。
  //             「舞台光」由其他节点 faded 来体现，而非自身发光闪烁。
  {
    selector: 'node:selected',
    style: { 'border-color': '#e89bbb', 'border-width': 3, 'border-opacity': 1 },
  },
  {
    selector: 'node.focused',
    style: { 'border-color': '#e89bbb', 'border-width': 3, 'border-opacity': 1 },
  },
  // [v2.4 聚焦隔离] faded 更彻底（0.12 → 0.06），让聚焦节点 + 邻域成绝对视觉主体
  { selector: 'node.faded', style: { opacity: 0.06 } },
  // hover 邻域高亮（与 tap 的 focused/faded 独立，detail 打开时禁用避免冲突）
  // v2.5 克制：hover-bright 仅 border 提亮 + spring scale 弹性反馈，不加 underlay glow
  { selector: 'node.hover-dim', style: { opacity: 0.14 } },
  {
    selector: 'node.hover-bright',
    style: { 'border-color': '#e89bbb', 'border-width': 2, 'border-opacity': 0.9 },
  },
  {
    selector: 'edge',
    style: {
      // v2.5 克制：边细（1px）+ 半透明（0.5），减少「贼乱」；hover/聚焦时提亮加粗
      width: 1,
      'line-color': 'data(color)',
      'line-style': 'data(lineStyle)',
      'target-arrow-color': 'data(color)',
      'target-arrow-shape': 'triangle',
      'curve-style': 'bezier',
      opacity: 0.5,
      'arrow-scale': 0.7,
      // dashed/dotted 边静态虚线形态保留（信息设计：区分关系类型），无流动动画
      'line-dash-pattern': [6, 4],
    },
  },
  // solid 关系（related/derived_from/evidence_for/part_of/depends_on/applied_in）保持实线
  { selector: 'edge[lineStyle = "solid"]', style: { 'line-dash-pattern': [1, 0] } },
  { selector: 'edge[lineStyle = "dotted"]', style: { 'line-dash-pattern': [2, 5] } },
  // [v2.5 克制] 高亮仅靠 opacity+width，不变 dash-pattern（Obsidian：无动画，只颜色/透明度变化）
  { selector: 'edge.highlight', style: { opacity: 0.9, width: 1.8 } },
  { selector: 'edge.faded', style: { opacity: 0.04 } },
  { selector: 'edge.hover-dim', style: { opacity: 0.05 } },
  { selector: 'edge.hover-bright', style: { opacity: 0.85, width: 1.6 } },
  // v2.6 兜底：两端节点都被推出视口时边渐隐（fit 全集修了后基本不触发；
  // 仅用户主动放大/拖动把节点推出视口时生效）。opacity 介于 faded(0.04) 与 hover-dim(0.05) 之上、
  // normal(0.5) 之下——保留"还有条线"的弱提示，不生硬延伸到边缘外。
  { selector: 'edge.out-of-viewport', style: { opacity: 0.08 } },
];

// ===== v2.7 L1 主题地图 · 领地区域样式与布局 =====
// 领地（domain 聚合）≠ 普通节点：黑曜石结论"区域感来自轮廓/填充，不是节点发光"。
// 领地视觉 = 半透明色块填充 + 实线边界轮廓 + 势力范围晕染（underlay）= "有边界的领域"而非"发光的点"。
// underlay 势力范围承载 authority 信号（聚合层有意编码：势力浓度=平均权威度）。
// 配色：紫(#b59ee6 低权威)→琥珀(#e6b885 高权威)插值，暖度即权威度——单一色族保持克制。
const PURPLE_RGB = [181, 158, 230];   // --accent-secondary 紫
const AMBER_RGB = [230, 184, 133];    // --accent-tertiary 琥珀
const TERR_BASE = 30;                 // 气泡最小像素（count=0 兜底，实际最小 count=2）
const TERR_FACTOR = 6.5;              // sqrt(count) 系数：count 2→39 / 18→57 / 82→89

/** 紫→琥珀插值（t=0 紫 / t=1 琥珀），返回 hex 色串。 */
function territoryColor(t) {
  const k = Math.max(0, Math.min(1, t));
  const r = Math.round(PURPLE_RGB[0] + (AMBER_RGB[0] - PURPLE_RGB[0]) * k);
  const g = Math.round(PURPLE_RGB[1] + (AMBER_RGB[1] - PURPLE_RGB[1]) * k);
  const b = Math.round(PURPLE_RGB[2] + (AMBER_RGB[2] - PURPLE_RGB[2]) * k);
  return `#${[r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('')}`;
}

const CY_STYLE_L1 = [
  {
    selector: 'node',
    style: {
      label: 'data(label)',
      'text-valign': 'center',
      'text-halign': 'center',
      color: '#f5edf4',
      'font-size': '11px',
      'font-weight': 600,
      'font-family': 'inherit',
      'text-wrap': 'wrap',
      'text-max-width': 'data(labelWrap)',
      'text-outline-color': '#120818',
      'text-outline-width': 4,
      // 区域感核心（黑曜石结论：区域感来自轮廓/填充，不是节点发光）：
      //  ① 半透明色块填充（0.16）→ 色域而非实心点；
      //  ② 实线边界轮廓（2.5px 同色）→ 明确的地盘界线；
      //  ③ underlay 势力范围（data(glow) 大 padding + 低 opacity）→ 晕染开的地盘辐射。
      // 三层叠加 = "有边界的领域"而非"发光的点"。
      'background-color': 'data(color)',
      'background-opacity': 0.16,
      width: 'data(size)',
      height: 'data(size)',
      'underlay-color': 'data(color)',
      'underlay-padding': 'data(glow)',
      'underlay-opacity': 'data(glowOp)',
      'underlay-shape': 'ellipse',
      'border-width': 2.5,
      'border-color': 'data(color)',
      'border-opacity': 0.72,
      'transition-property': 'border-width,border-color,border-opacity,opacity,background-opacity',
      'transition-duration': 160,
    },
  },
  // 孤立领地（无跨域关系）：dashed 虚线轮廓 + 更淡填充，与活跃领地实线区分。
  // 不靠颜色单一维度（a11y：色盲友好），叠加线型 + 浓度让用户一眼识别"待连接"领地。
  // 注：不用 opacity 表达淡化——入场动画 applyEntranceFinal 会用 bypass style 把 opacity 置 1，
  //     覆盖 stylesheet；改用 background-opacity（不在 bypass 列表）表达"更淡的地盘"。
  {
    selector: 'node[orphan]',
    style: {
      'border-style': 'dashed',
      'border-width': 2,
      'border-dash-pattern': [5, 4],
      'background-opacity': 0.1,
    },
  },
  { selector: 'node.entering', style: { 'transition-property': 'none', 'transition-duration': 0 } },
  // hover：樱粉描边加粗提示可钻取（领地默认 2.5px 轮廓，hover 加粗到 3.5 + 提亮，不脉冲，克制）
  { selector: 'node.hover-bright', style: { 'border-color': '#e89bbb', 'border-width': 3.5, 'border-opacity': 1 } },
  { selector: 'node.faded', style: { opacity: 0.18 } },
  // 跨域连线：粗细=关系数（sqrt 抑制），中性灰紫半透明，无箭头（领地关系是无向聚合）
  {
    selector: 'edge',
    style: {
      width: 'data(width)',
      'line-color': '#897989',
      'curve-style': 'bezier',
      opacity: 0.4,
      'target-arrow-shape': 'none',
      'source-arrow-shape': 'none',
    },
  },
  { selector: 'edge.highlight', style: { opacity: 0.85, 'line-color': '#b59ee6' } },
  { selector: 'edge.faded', style: { opacity: 0.06 } },
];

// L1 布局：领地势力范围大（直径 70-170px），需更强排斥 + 节点分离间距防重叠。
// 防重叠三层协同：nodeRepulsion 宏观推开（撑开 100+px 距离）/
//                nodeSeparation 微观间距（fcose 特有，防紧邻重叠）/
//                tile 把无连线的孤立领地分量打包到边缘（自然边缘放置）。
// 引力：edgeElasticity 提高让有跨域连线的领地聚拢（相关簇靠近），gravity 适度降低让 cluster 自然散开
//      （呼应 Obsidian Coulomb 反平方自然散布，不向心挤成一团）。
const L1_LAYOUT_FCOSE = {
  name: 'fcose', quality: 'default', randomize: true, animate: false,
  nodeRepulsion: 16000, idealEdgeLength: 230, edgeElasticity: 0.5,
  gravity: 0.25, numIter: 2500, nodeSeparation: 95, tile: true, padding: 60,
};
// cose 降级（无 nodeSeparation，靠提高 nodeRepulsion + idealEdgeLength 兜底防重叠）
const L1_LAYOUT_COSE = {
  name: 'cose', animate: false, nodeRepulsion: 18000, idealEdgeLength: 240,
  edgeElasticity: 0.5, gravity: 0.28, numIter: 2000, fit: true, padding: 70,
  randomize: true, tile: false,
};

// ===== fcose 扩展动态加载（CDN，按序 layout-base → cose-base → cytoscape-fcose）=====
// script 标签加载后 fcose 自动注册到 cytoscape（无需手动 use，见 iVis-at-Bilkent/cytoscape.js-fcose README）
const FCOSE_SCRIPTS = [
  'https://unpkg.com/layout-base@2.0.1/layout-base.js',
  'https://unpkg.com/cose-base@2.2.0/cose-base.js',
  'https://unpkg.com/cytoscape-fcose@2.2.0/cytoscape-fcose.js',
];

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = src;
    s.crossOrigin = 'anonymous';
    s.referrerPolicy = 'no-referrer';
    s.onload = () => resolve();
    s.onerror = () => reject(new Error('script load failed: ' + src));
    document.head.appendChild(s);
  });
}

/**
 * 加载 fcose 扩展。去重（并发调用共享同一 Promise）；失败不 reject，返回 false 触发降级。
 * @returns {Promise<boolean>} true=fcose 可用，false=降级 cose
 */
function loadFcose() {
  if (fcoseReady) return Promise.resolve(true);
  if (fcoseLoading) return fcoseLoading;
  fcoseLoading = (async () => {
    for (const src of FCOSE_SCRIPTS) await loadScript(src);
    return !!(window.cytoscape && typeof window.cytoscape === 'function');
  })();
  return fcoseLoading
    .then((ok) => { fcoseReady = !!ok; return fcoseReady; })
    .catch((err) => {
      console.warn('[graph] fcose 加载失败，降级 cose 布局', err);
      fcoseReady = false;
      return false;
    })
    .finally(() => { fcoseLoading = null; });
}

// ===== v2.8 d3 加载（CDN，完整包 UMD）=====
// 用 d3 完整包而非单 d3-force：d3-force UMD 假设 d3.timer/d3.quadtree/d3.dispatch 已存在
// （依赖 d3-timer/d3-quadtree/d3-dispatch），单包加载会报 "r.timer is not a function"。
// 完整包一次性满足所有依赖，注册到 window.d3 命名空间（forceSimulation/forceLink/...）。
// index.html 已用 <script> 静态引入，本函数作为兜底：若静态加载失败（CDN 抖动/被墙），
// 运行时再尝试一次；仍失败则降级 fcose 静态布局（物理特性放弃，demo 不中断）。
const D3_FORCE_CDN = 'https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js';

function loadD3Force() {
  if (d3Ready) return Promise.resolve(true);
  if (window.d3 && typeof window.d3.forceSimulation === 'function') {
    d3Ready = true;
    return Promise.resolve(true);
  }
  if (d3Loading) return d3Loading;
  d3Loading = loadScript(D3_FORCE_CDN)
    .then(() => !!(window.d3 && typeof window.d3.forceSimulation === 'function'))
    .then((ok) => { d3Ready = !!ok; return d3Ready; })
    .catch((err) => {
      console.warn('[graph] d3-force 加载失败，物理引擎降级为静态布局', err);
      d3Ready = false;
      return false;
    })
    .finally(() => { d3Loading = null; });
  return d3Loading;
}

// ===== v2.8 d3-force 持续物理引擎 · 参数表与启停 =====
// 参数来源：黑曜石物理调研参数表（力导向图聚类 + 隔离拖拽）
//
// 设计原则（四力协同）：
//   - charge（forceManyBody）处理宏观分散：100+ px 距离的节点/领地互相推开
//   - collide（forceCollide）处理微观防撞：正在重叠的节点互相挤出 + 拖拽时实时排斥响应
//   - link（makeRubberBandForce 自定义力）边弹簧：非线性距离衰减的皮筋力
//     · 近-中距离（dist < maxLen）Hooke 弹簧（趋于 restLen），baseStrength 按 solid/dashed/dotted 分级
//     · 远距离（dist ≥ maxLen）指数衰减归零（拽越远力越小，到一定距离脱开不受力）
//     · 用户原话诉求："拽得越远给予加速度的力，到一定距离快速减小并消失"
//   - center（forceCenter）弱居中：防止节点漂出视口；strength 弱避免与拖拽强对抗
//   - 孤立节点（无 link）→ 没有 link 力拉回中心，被 charge 推到边缘自然散布（小行星带式）

// L2 域子图 / 全图（141-189 节点，节点小 8-18px）：斥力中等 + collide 紧凑 + link 分级
const PHYSICS_DEFAULT = {
  chargeBase: -130,                  // 节点小但多，需中等斥力散开（调研建议 -30~-80，本 demo 189 节点需更强）
  chargeSizeK: 0,                    // 普通节点不按大小加权（authSize 范围窄 8-18px，差异不明显）
  linkDistance: 70,                  // 相连节点间距（紧凑，呼应 Obsidian 60-80）
  linkStrength: {                    // 边弹簧 baseStrength 分级：solid 强 / dashed 中 / dotted 弱（用户原话诉求 #3）
    solid: 0.7,                      // related/derived_from/evidence_for/part_of/depends_on/applied_in
    dashed: 0.4,                     // evidence_refs（证据关联，中等强度）
    dotted: 0.2,                     // source_refs（来源引用，最弱）
  },
  // [v2.9 皮筋非线性距离衰减] baseStrength 不再固定（d3 标准 forceLink 是固定刚度弹簧，
  // 拽多远都按同一强度拉回，违反"皮筋断裂"语义）。改用自定义 makeRubberBandForce：
  //   - dist < restLen × maxLenRatio：Hooke 弹簧（趋于 restLen，拽越远力越大）
  //   - dist ≥ restLen × maxLenRatio：指数衰减归零（连接点"皮筋松弛脱开"，不再互相牵连）
  // 取值理由：restLen=70，主簇内自然边长 ≈ 60-180px，maxLen=210 时绝大多数边全强度；
  //          拖拽把节点拽出 210px 外（脱离主簇视口）时力开始衰减，到 420px（+3×decay）
  //          衰减到 ≈5% 基本脱开——既保弹簧聚合又让远拽节点独立，避免整片被牵连乱拉。
  linkMaxLenRatio: 3,                // 力衰减阈值 = restLen × 3 ≈ 210px
  linkDecayRatio: 1,                 // 衰减特征长度 = restLen × 1 = 70px（每 70px 力降到 1/e ≈ 37%）
  collideStrength: 0.9,              // 节点防撞（比领地略软，允许紧凑布局）
  collideIterations: 2,
  centerStrength: 0.03,              // 弱居中（调低：让 charge 主导，孤立节点不被强拉中心）
  // 孤立节点（degree=0）边缘散布：forceRadial 把它们推到外圈（黑曜石小行星带），
  // 连通节点 radius=0 + strength=0 → 不受力，仍由 charge + link 主导自然聚类。
  // 这样孤立节点不会被 center 拉回中心堆叠，自然散布在主簇外围。
  isolatedRadialStrength: 0.25,      // 孤立节点径向力强度（中度：把孤立节点稳定在外圈，但允许 charge 让它们在环上散布）
};

// L1 领地图无需 isolated 处理（领地间都有跨域关系或本身是聚合）
// [vision v29 修复] 原 chargeBase=-260 + chargeSizeK=0.6 导致 charge 实际值 -325~-1518
// （charge = base × (1 + (√count-1)×K)，count=82 → -1518），远超全图 -130，13 领地被推到
// 画布边缘、中央大面积空白。根因：领地少（13）+ 大气泡视觉半径已由 collide radius 兜底防重叠，
// charge 不需要那么强。调弱 charge + 加强 center 让领地聚在中心区域均匀散布。
// 收敛后由 scheduleL1ConvergeFit 补一次 fit，确保领地居中不漂边缘。
const PHYSICS_L1 = {
  chargeBase: -120,                  // 原 -260：13 领地不需要那么强斥力，降低避免被推边缘
  chargeSizeK: 0.15,                 // 原 0.6：削弱 √count 加权（原值让大领地斥力放大近 6 倍 → 爆炸）
  linkDistance: 200,                 // 原 230：略收紧跨域连线，让相连领地适度聚拢
  linkStrength: { solid: 0.5 },      // L1 仅 xdom 一种边（无分级语义）
  linkMaxLenRatio: 3,                // 皮筋阈值 = 200 × 3 = 600px
  linkDecayRatio: 1,                 // 衰减特征长度 = 200px
  collideStrength: 0.9,              // 原 0.95：微降留轻微挤让余量（collide radius 已含 +12px 缓冲，仍硬防撞）
  collideIterations: 2,              // 迭代 2 次保证硬约束
  centerStrength: 0.09,              // 原 0.04：加强居中拉力，把领地拉回画布中央不漂边缘
};

// ===== v2.9 自定义力 · 皮筋弹簧（非线性距离衰减）=====
// 替代 d3 标准 forceLink 的原因：forceLink.strength 是固定刚度（不随距离变），
// 拽多远都按同一强度拉回，违反用户原话诉求"拽得越远给予加速度的力，到一定距离快速
// 减小并消失"。本自定义力让 baseStrength 随当前距离非线性衰减，实现"皮筋松弛断裂"。
//
// d3-force force 协议：实现 force(alpha) 每帧调用 + force.initialize(nodes) 一次性绑定。
// force(alpha) 内按 d3-force-link 同款 Verlet 风格（直接改位置而非加速度，与 d3 协议一致）：
//   w = (dist - restLen) / dist × effectiveStrength × alpha
//   source.pos += unitVector × w
//   target.pos -= unitVector × w
//
// 物理模型（每条 link）：
//   ┌─ 近-中距离（dist < restLen × maxLenRatio）：Hooke 弹簧全强度
//   │   effectiveStrength = baseStrength
//   │   · dist < restLen：相互推开（避免节点重叠，与 collide 协同）
//   │   · dist > restLen：相互拉回（拽越远力越大，线性增长到 maxLen 处达峰值）
//   │
//   └─ 远距离（dist ≥ restLen × maxLenRatio）：指数衰减归零
//       effectiveStrength = baseStrength × exp(-(dist - maxLen) / decay)
//       · dist = maxLen：baseStrength（maxLen 处连续过渡，无突变）
//       · dist = maxLen + decay：≈ 37%（baseStrength × e⁻¹）
//       · dist = maxLen + 3×decay：≈ 5%（基本脱开，连接点独立不受牵连）
//       · dist = maxLen + 5×decay：≈ 0.7%（视觉上"皮筋断裂"）
//
// 参数取值（以 PHYSICS_DEFAULT 为例，restLen=70）：
//   maxLenRatio=3 → maxLen=210：主簇内自然边长 ≈60-180px，绝大多数边全强度；
//                  拖拽把节点拽出主簇视口（>210px）时才开始衰减。
//   decayRatio=1 → decay=70：每 70px 力降到 1/e；
//                  +210px（3 个 decay）≈5%，+350px（5 个 decay）≈0.7%——既给远拽节点
//                  充分独立空间（皮筋松弛），又不生硬截断（指数衰减自然过渡）。
/**
 * 创建皮筋弹簧 force（非线性距离衰减）。链式 API 兼容 d3-force 协议：
 *   .id/.distance/.strength/.maxLenRatio/.decayRatio/.links/.initialize
 * 与原 forceLink 接口对称，syncSimulation 调用 .links(newLinks) 可热更新。
 *
 * @param {Array<{source:string|object,target:string|object,lineStyle?:string}>} links
 * @returns {object} d3-force compatible force object
 */
function makeRubberBandForce(links) {
  let nodes = null;
  let nodeById = null;
  let idAccessor = (d) => d.id;
  let distanceAccessor = () => 70;
  let strengthAccessor = () => 0.5;
  let maxLenRatio = 3;
  let decayRatio = 1;

  // id 字符串 → 节点对象（d3-force 协议：initialize 后才能解析）。
  // 已是对象则保持引用（syncSimulation 多次 .links() 不会重复解析破坏引用）。
  function resolveLink(link) {
    if (typeof link.source === 'object' && link.source !== null) return;
    if (typeof link.target === 'object' && link.target !== null) return;
    if (!nodeById) return;
    const s = nodeById.get(link.source);
    const t = nodeById.get(link.target);
    if (s) link.source = s;
    if (t) link.target = t;
  }

  function force(alpha) {
    if (!nodes) return;
    for (let i = 0, n = links.length; i < n; i++) {
      const link = links[i];
      const source = link.source;
      const target = link.target;
      // source/target 未解析（如新增但未 initialize）跳过，避免误判
      if (!source || !target) continue;
      if (typeof source === 'string' || typeof target === 'string') continue;
      const dx = target.x - source.x;
      const dy = target.y - source.y;
      const distSq = dx * dx + dy * dy;
      const restLen = distanceAccessor(link);
      const baseStrength = strengthAccessor(link);
      if (distSq < 1e-6) {
        // 两点重合：用微小 jiggle 避免除零（不引入新依赖，强度按 base × alpha × ε）
        source.x -= baseStrength * alpha * 0.5;
        target.x += baseStrength * alpha * 0.5;
        continue;
      }
      const dist = Math.sqrt(distSq);
      const maxLen = restLen * maxLenRatio;
      const decay = restLen * decayRatio;
      // 非线性距离衰减：dist < maxLen 全强度；dist ≥ maxLen 指数衰减归零
      const effective = dist < maxLen
        ? baseStrength
        : baseStrength * Math.exp(-(dist - maxLen) / decay);
      if (effective < 1e-4) continue;            // 力几乎为零，跳过节省开销
      // Verlet 风格 Hooke 位移：alpha 是 d3 退火系数（逐帧递减），与 d3-force-link 一致
      const w = (dist - restLen) / dist * effective * alpha;
      const ox = dx * w;
      const oy = dy * w;
      target.x -= ox;
      target.y -= oy;
      source.x += ox;
      source.y += oy;
    }
  }

  force.initialize = function (allNodes) {
    nodes = allNodes;
    nodeById = new Map();
    for (let i = 0; i < allNodes.length; i++) {
      const nd = allNodes[i];
      nodeById.set(idAccessor(nd), nd);
    }
    for (let i = 0; i < links.length; i++) resolveLink(links[i]);
  };

  force.id = function (fn) {
    if (arguments.length) { idAccessor = fn; return force; }
    return idAccessor;
  };
  force.distance = function (fn) {
    if (arguments.length) {
      distanceAccessor = typeof fn === 'function' ? fn : () => +fn;
      return force;
    }
    return distanceAccessor;
  };
  force.strength = function (fn) {
    if (arguments.length) {
      strengthAccessor = typeof fn === 'function' ? fn : () => +fn;
      return force;
    }
    return strengthAccessor;
  };
  force.maxLenRatio = function (v) {
    if (arguments.length) { maxLenRatio = +v || 3; return force; }
    return maxLenRatio;
  };
  force.decayRatio = function (v) {
    if (arguments.length) { decayRatio = +v || 1; return force; }
    return decayRatio;
  };
  force.links = function (_) {
    if (arguments.length) {
      links = _;
      // links 热更新（syncSimulation）：若 initialize 已执行（nodeById 已建），立即解析新数组
      if (nodeById) {
        for (let i = 0; i < links.length; i++) resolveLink(links[i]);
      }
      return force;
    }
    return links;
  };

  return force;
}

/**
 * 启动 d3-force 持续物理引擎。从 cy 当前节点/边派生 d3 数据（引用稳定），
 * tick 时 cy.batch 同步节点位置到 cytoscape（合并 redraw，性能友好）。
 * @param {{l1?: boolean}} [opts] l1=true 用 L1 领地图参数；否则用默认节点图参数
 * @returns {boolean} true=物理引擎已启动；false=d3 不可用或 cy 未就绪（调用方应降级静态）
 */
function startPhysics(opts = {}) {
  if (!cy || !d3Ready || !window.d3) return false;
  if (motionReduced) return false;   // reduced-motion 不跑持续物理（a11y 硬承诺）
  stopPhysics();

  const isL1 = !!opts.l1;
  const params = isL1 ? PHYSICS_L1 : PHYSICS_DEFAULT;

  // 构造 d3 节点（从 cytoscape 当前节点派生，跳过 compound 父节点）
  // 引用稳定：后续 refilter 增删时 mutate 同一数组，避免 simulation 重建丢速度
  simNodes = [];
  simNodeMap = new Map();
  // 预计算 degree（用于孤立节点识别 + forceRadial 边缘散布）
  const degreeMap = {};
  cy.edges().forEach((e) => {
    const s = e.source().id();
    const t = e.target().id();
    degreeMap[s] = (degreeMap[s] || 0) + 1;
    degreeMap[t] = (degreeMap[t] || 0) + 1;
  });
  cy.nodes().forEach((n) => {
    if (n.isParent()) return;
    const p = n.position();
    const size = Number(n.data('size')) || (isL1 ? 30 : 10);
    // collide radius：节点视觉半径 + 缓冲（防贴边）
    const radius = size / 2 + (isL1 ? 12 : 5);
    // charge 按 √count 加权（L1 领地成员越多占地越大；L2/全图不加权）
    const sizeFactor = isL1
      ? Math.sqrt(Math.max(1, Number(n.data('count')) || 1))
      : 1;
    const id = n.id();
    const d3n = {
      id,
      x: p.x || (Math.random() - 0.5) * 400,
      y: p.y || (Math.random() - 0.5) * 400,
      radius,
      sizeFactor,
      // 孤立节点标记（degree=0）：forceRadial 据此把它们推到外圈散布（黑曜石小行星带）
      isIsolated: !isL1 && (degreeMap[id] || 0) === 0,
    };
    simNodes.push(d3n);
    simNodeMap.set(id, d3n);
  });

  // 构造 d3 链接（source/target 用 cy_id；forceLink.id 会解析为对象引用）
  simLinks = cy.edges().reduce((acc, e) => {
    const srcId = e.source().id();
    const tgtId = e.target().id();
    // 仅纳入两端都在 simNodes 的边（refilter 后可能有悬空边）
    if (!simNodeMap.has(srcId) || !simNodeMap.has(tgtId)) return acc;
    acc.push({
      source: srcId,
      target: tgtId,
      lineStyle: e.data('lineStyle') || 'solid',
    });
    return acc;
  }, []);

  // 外圈半径（用于 forceRadial）：基于可见节点数估算主簇半径，孤立节点推到主簇外缘
  // 形成"小行星带"（用户原话诉求 #4）。系数 80 让外圈 ≈ √N × 80（189 节点 → ≈1100），
  // 略大于 charge + link 自然形成的主簇半径（实测主簇 avg≈1010），保证孤立节点在主簇外。
  const N = simNodes.length;
  const outerRadius = Math.max(500, Math.sqrt(N) * 80);

  simulation = window.d3.forceSimulation(simNodes)
    .alpha(1)
    .alphaMin(0.001)
    .alphaDecay(0.0228)              // 默认退火率（≈300 ticks 收敛）
    .velocityDecay(0.4)              // 默认摩擦
    // charge：宏观分散。领地按 sizeFactor 加权（成员多的领地斥力更强）
    .force('charge', window.d3.forceManyBody()
      .strength((d) => params.chargeBase * (1 + (d.sizeFactor - 1) * params.chargeSizeK))
      .distanceMin(2))               // 防极近距离 charge 爆炸
    // link：皮筋弹簧（非线性距离衰减）—— 自定义 force，替代 d3 标准 forceLink。
    // 原因：forceLink.strength 固定（不随距离变），无法实现"拽越远力越大、到一定距离
    //      快速减小消失"的皮筋语义。见 makeRubberBandForce 实现与物理模型说明。
    // baseStrength 仍按 lineStyle 分级（solid 强/dashed 中/dotted 弱），距离衰减层另加。
    .force('link', makeRubberBandForce(simLinks)
      .id((d) => d.id)
      .distance(params.linkDistance)
      .strength((l) => {
        const s = params.linkStrength[l.lineStyle];
        return s != null ? s : 0.5;
      })
      .maxLenRatio(params.linkMaxLenRatio)
      .decayRatio(params.linkDecayRatio))
    // collide：微观防撞 + 拖拽时实时排斥（iterations=2 硬约束）
    .force('collide', window.d3.forceCollide()
      .radius((d) => d.radius)
      .strength(params.collideStrength)
      .iterations(params.collideIterations))
    // center：弱居中（防止节点漂出视口，但不与拖拽强对抗）
    .force('center', window.d3.forceCenter(0, 0).strength(params.centerStrength));

  // 孤立节点边缘散布（仅 L2/全图；L1 领地全部互联或本身聚合，跳过）
  // forceRadial：孤立节点 → 推到 outerRadius 外圈；连通节点 → radius=0+strength=0 不受力。
  // 这样孤立节点不被 center 拉回中心，散布成主簇外围的小行星带（用户原话诉求 #4）。
  if (!isL1 && typeof window.d3.forceRadial === 'function' && params.isolatedRadialStrength) {
    simulation.force('isolatedRadial', window.d3.forceRadial(
      (d) => d.isIsolated ? outerRadius : 0,
      0, 0,
    ).strength((d) => d.isIsolated ? params.isolatedRadialStrength : 0));
  }

  // tick：d3-force 计算完毕 → cy.batch 同步到 cytoscape（合并 redraw）
  // 跳过 fx!=null（被拖拽/pin）的节点：那些由 cytoscape 鼠标或 pin 控制，d3 不能覆盖
  simulation.on('tick', () => {
    if (!cy) return;
    cy.batch(() => {
      for (let i = 0; i < simNodes.length; i++) {
        const d = simNodes[i];
        if (d.fx != null) continue;       // 锁定中（拖拽中），跳过
        const n = cy.getElementById(d.id);
        if (n && n.length && !n.isParent()) {
          n.position({ x: d.x, y: d.y });
        }
      }
    });
    // v2.10：物理 tick 期间节点持续位移，但 cy.batch 改 position 不触发 viewport/layoutstop
    // 事件 → minimap 原本只在 viewport/layoutstop/add/remove 重绘，物理期间会卡在旧帧（看似消失）。
    // 此处每 tick 触发一次重绘（scheduleMinimapDraw 内部 rAF 节流，合到每帧一次），minimap 实时跟随。
    // L1 无 minimap（minimapCtx=null），guard 跳过避免无谓 rAF 调度。
    if (minimapCtx) scheduleMinimapDraw();
  });

  // [dev 钩子] 仅 ?debug=1 时暴露内部状态到 window，供实机验证物理引擎（生产路径无副作用）
  if (new URLSearchParams(window.location.search).has('debug')) {
    window.__ek_sim = simulation;
    window.__ek_cy = cy;
    window.__ek_simNodes = simNodes;
  }

  return true;
}

/**
 * 停止 d3-force 物理引擎并清空状态。destroyCy / 层级切换 / unmount 时调用。
 * 幂等：simulation 已为 null 时仅清残留状态。
 */
function stopPhysics() {
  if (l1ConvergeRaf) { cancelAnimationFrame(l1ConvergeRaf); l1ConvergeRaf = 0; }
  if (simulation) {
    try { simulation.stop(); } catch (e) { /* 已停止 */ }
    try { simulation.on('tick', null); } catch (e) { /* ignore */ }
    simulation = null;
  }
  simNodes = [];
  simLinks = [];
  simNodeMap = new Map();
  draggingNodeId = null;
}

/**
 * 把 pinnedNodes（用户拖完固定的节点）应用到 simulation：设 fx/fy 锁定。
 * runLayout 启动物理后调用，让 pin 节点保持位置不被物理重排。
 */
function applyPinnedToSimulation() {
  if (!simulation || !pinnedNodes.size) return;
  pinnedNodes.forEach((pos, id) => {
    const d3n = simNodeMap.get(id);
    if (d3n) {
      d3n.fx = pos.x;
      d3n.fy = pos.y;
    }
  });
}

/**
 * refilter 后同步 simulation 数据（增删节点/边）。
 * 不重建 simulation（保留速度场连续性），改用 d3-force 的 update 机制：
 *  - 新节点 push 到 simNodes + simNodeMap（带初始位置避免堆原点）
 *  - 删除节点从 simNodes 移除（simulation.nodes() 重新指认）
 *  - 边全量重建（边数变化通常意味着结构变化）
 *  - 重新计算 isIsolated（refilter 后 degree 可能变化：关闭某 kind 让原本有边的节点变孤立）
 *  - simulation.nodes(simNodes) + forceLink.links(simLinks) 重新绑定
 *  - alpha(0.5).restart() 轻度 reheat 让新节点融入
 */
function syncSimulation() {
  if (!simulation || !cy) return;
  // 重算 degree（kind/depth 切换后 degree 可能变化）
  const degreeMap = {};
  cy.edges().forEach((e) => {
    const s = e.source().id();
    const t = e.target().id();
    degreeMap[s] = (degreeMap[s] || 0) + 1;
    degreeMap[t] = (degreeMap[t] || 0) + 1;
  });
  const visibleIds = new Set();
  // 增 / 改
  cy.nodes().forEach((n) => {
    if (n.isParent()) return;
    const id = n.id();
    visibleIds.add(id);
    let d3n = simNodeMap.get(id);
    if (!d3n) {
      // 新节点：从邻居重心起笔（避免堆原点），若无邻居则随机散布
      const p = n.position();
      d3n = {
        id,
        x: (p && p.x) || (Math.random() - 0.5) * 400,
        y: (p && p.y) || (Math.random() - 0.5) * 400,
        radius: (Number(n.data('size')) || 10) / 2 + 5,
        sizeFactor: 1,
        isIsolated: (degreeMap[id] || 0) === 0,
      };
      simNodes.push(d3n);
      simNodeMap.set(id, d3n);
    } else {
      // 已存在：更新 radius（kind 切换可能改 size）+ isIsolated（degree 变化）
      d3n.radius = (Number(n.data('size')) || 10) / 2 + 5;
      d3n.isIsolated = (degreeMap[id] || 0) === 0;
    }
  });
  // 删
  for (let i = simNodes.length - 1; i >= 0; i--) {
    if (!visibleIds.has(simNodes[i].id)) {
      simNodeMap.delete(simNodes[i].id);
      simNodes.splice(i, 1);
    }
  }
  // 边全量重建（增删后结构变化，重建最简洁）
  simLinks = cy.edges().reduce((acc, e) => {
    const srcId = e.source().id();
    const tgtId = e.target().id();
    if (!simNodeMap.has(srcId) || !simNodeMap.has(tgtId)) return acc;
    acc.push({
      source: srcId,
      target: tgtId,
      lineStyle: e.data('lineStyle') || 'solid',
    });
    return acc;
  }, []);
  // 重新绑定数据 + 轻度 reheat
  simulation.nodes(simNodes);
  const linkForce = simulation.force('link');
  if (linkForce) linkForce.links(simLinks);
  simulation.alpha(0.5).restart();
}

// ===== 元素构建：compound 父节点 + depth 收敛（不改 data-layer，在本视图后处理）=====

/**
 * v2.2 节点"性格"派生（v2.5 克制瘦身：移除 glow 数据，仅保留 confidence 边框分类）。
 *
 * 信息设计加工概念（raw → 视觉脾性）：
 *  - confClass（confidence → 边框质感）：high 实线樱粉 / medium 虚线紫 / none 无边框
 *      · 让"可信度"从数值变成一眼可辨的边框语言（黑曜石克制下唯一保留的节点装饰）
 *  - freshnessClass（freshness 分类）：personaPulse 已在 v2.4 移除，data 派生保留备复用
 *
 * [v2.5 克制] 移除：glowColor / glowColorStrong / starLevel / starGlow（驱动 underlay 外发光，
 *             黑曜石调研证实节点本身无 glow）。不改 data-layer（仅视图层视觉派生）。
 * compound 父节点跳过。
 * @param {{nodes: Array, edges: Array}} els
 */
function injectNodeMeta(els) {
  els.nodes.forEach((n) => {
    if (String(n.data.id).startsWith('compound-')) return;

    // confidence → 边框质感分类（high ≥ 0.7 / medium ≥ 0.4 / none 含 null）
    const conf = n.data.confidence;
    n.data.confClass = (conf == null || conf === '')
      ? 'none'
      : (Number(conf) >= 0.7 ? 'high' : (Number(conf) >= 0.4 ? 'medium' : 'none'));

    // freshness → 呼吸速率分类（personaPulse 已移除，保留 data 备将来轻量复用）
    n.data.freshnessClass = ['volatile', 'watch', 'stable'].includes(n.data.freshness)
      ? n.data.freshness
      : 'stable';

    // [v2.10 克制微光] 高 authority 节点加 underlay 微光（黑曜石式克制，非 v2 大光晕）。
    // 阈值 0.30：authority 长尾分布（p95 的 authNorm 才 0.2），0.30 对应 top ~6 枢纽节点（≈3%），
    // 既保持"仅高 authority"语义又让微光实际可见（非仅 top 2）。线性 authNorm 阈值已校准到分布。
    // 仅 authNorm ≥ 0.30 的节点设 glow 数据 → CY_STYLE node[glow] 按"字段存在"匹配渲染 underlay。
    // padding 4-8px + opacity 0.15-0.25，在 [0.30,1.0] 区间内按 authNorm 插值（越权威微光越明显）。
    // 低权威节点不设字段（不匹配选择器，保持纯色 fill）。
    const aNorm = authNorm(Number(n.data.authority) || 0);
    if (aNorm >= 0.30) {
      const k = (aNorm - 0.30) / 0.70;        // 高权威区间内 0-1
      n.data.glow = 4 + k * 4;                 // underlay-padding 4-8px
      n.data.glowOp = 0.15 + k * 0.10;         // underlay-opacity 0.15-0.25
    }

    // [v2.10 标签可读] 放宽截断：toElements 默认 trunc(title) 10 字符太狠看不出内容。
    // fullTitle 保留完整标题，此处按 16 字符重截；低 zoom 由 updateLabelFade 淡出（远看简洁）。
    // label 为空（showLabels 关）时保持空，不强加。
    if (n.data.label) {
      n.data.label = trunc(n.data.fullTitle, 16);
    }

    // [B4] 孤立节点标记（degree=0）：未隐藏时由 CY_STYLE node[isolated] 渲染 dashed 灰边框，
    //      与普通节点区分。degree 来自 buildIndex 的 degreeIndex（全集无向度数）。
    //      仅孤立节点设字段：cytoscape [isolated] 按"字段存在"匹配，非孤立不设避免 false 误匹配。
    const deg = (index && index.degreeIndex) ? (index.degreeIndex.get(n.data.id) || 0) : 0;
    if (deg === 0) n.data.isolated = true;
  });
  return els;
}

/**
 * 给 toElements 的结果注入 compound 父节点 + 子节点 parent 字段。
 * 父节点 id = 'compound-' + kind；仅对当前可见节点存在的 kind 建容器。
 * @param {{nodes: Array, edges: Array}} base toElements 输出
 * @param {boolean} enabled 是否启用分组
 * @returns {{nodes: Array, edges: Array}}
 */
function buildCompoundElements(base, enabled) {
  if (!enabled) return base;
  const childNodes = base.nodes.map((n) => ({
    data: { ...n.data, parent: 'compound-' + n.data.kind },
  }));
  // 收集存在的 kind（保留顺序：按 KIND_COLORS 键序）
  const presentKinds = new Set(childNodes.map((n) => n.data.kind));
  const parents = [];
  Object.keys(KIND_COLORS).forEach((k) => {
    if (!presentKinds.has(k)) return;
    parents.push({
      data: {
        id: 'compound-' + k,
        kind: k,
        label: KIND_LABELS[k] || k,
        color: KIND_COLORS[k],
      },
    });
  });
  return { nodes: parents.concat(childNodes), edges: base.edges };
}

/**
 * depth 收敛：保留 degree ≥ depth 的节点（depth=0 不收敛）。
 * degree 来自 buildIndex 的 degreeIndex（无向度数）。
 * 注：此处 base 来自 toElements（尚无 compound 父节点），父节点由后续 buildCompoundElements
 *     基于过滤后的节点重建，自动只含仍存在的 kind。
 * @param {{nodes: Array, edges: Array}} base
 * @param {number} depth
 * @returns {{nodes: Array, edges: Array}}
 */
function applyDepth(base, depth) {
  if (!depth || depth < 1) return base;
  const deg = index ? index.degreeIndex : null;
  if (!deg) return base;
  const kept = base.nodes.filter((n) => (deg.get(n.data.id) || 0) >= depth);
  const keptIds = new Set(kept.map((n) => n.data.id));
  const keptEdges = base.edges.filter((e) => keptIds.has(e.data.source) && keptIds.has(e.data.target));
  return { nodes: kept, edges: keptEdges };
}

// ===== v2.7 L1 主题地图 · 域聚合与领地元素 =====
// 不改 data-layer：聚合在视图层完成（buildIndex 已提供 domainCounts/authRange，
// 此处补 per-domain 平均 authority 与跨域边计数，全部从 graph 本地派生）。

/** 域显示名（_unsorted 是内部哨兵 → 友好名；其余用原始 slug，与面包屑示例「novel-writing 域」一致）。 */
function domainDisplayName(d) {
  return d === '_unsorted' ? '未归类' : d;
}

/**
 * 构建域聚合缓存：每个 domain 的节点数 / 平均 authority / 归一化 authority / 成员 id。
 * 缓存到 module 级 domainMap，供领地元素与左侧领地列表复用。graph 不变时只算一次。
 * @param {{nodes: Array, edges: Array}} graph
 * @returns {Array} 按 count 降序的域聚合数组
 */
function buildDomainMap(graph) {
  const acc = {};
  graph.nodes.forEach((n) => {
    const d = n.domain || '_unsorted';
    if (!acc[d]) acc[d] = { domain: d, count: 0, authSum: 0, nodes: [] };
    acc[d].count++;
    acc[d].authSum += Number(n.authority) || 0;
    acc[d].nodes.push(n.id);
  });
  const list = Object.values(acc).map((v) => ({
    domain: v.domain,
    count: v.count,
    avgAuth: v.authSum / v.count,
    authNorm: authNorm(v.authSum / v.count),
    nodes: v.nodes,
  }));
  list.sort((a, b) => b.count - a.count);
  return list;
}

/**
 * 派生 L1 领地气泡 + 跨域连线的 cytoscape 元素。
 * 气泡：size=sqrt(count) 映射；color/glow=authority 归一化（紫→琥珀，低→高）。
 * 连线：两端不同域的原始边聚合为领地间单边，width=sqrt(跨域关系数)。
 * @returns {{nodes: Array, edges: Array}}
 */
function buildTerritoryElements() {
  if (!domainMap) domainMap = buildDomainMap(graph);
  const byDomain = {};
  graph.nodes.forEach((n) => { byDomain[n.id] = n.domain || '_unsorted'; });

  // 先聚合跨域边（源/目标不同域的原始边，按领地对排序键计数），
  // 再据"该领地是否出现在任意跨域边"判定孤立领地（无跨域关系 = 孤立）。
  const pairCount = {};
  graph.edges.forEach((e) => {
    const ds = byDomain[e.source], dt = byDomain[e.target];
    if (!ds || !dt || ds === dt) return;
    const key = [ds, dt].sort().join('||');
    pairCount[key] = (pairCount[key] || 0) + 1;
  });
  const connectedDomains = new Set();
  Object.keys(pairCount).forEach((key) => key.split('||').forEach((d) => connectedDomains.add(d)));

  const nodes = domainMap.map((d) => {
    const t = d.authNorm;
    const size = TERR_BASE + Math.sqrt(d.count) * TERR_FACTOR;
    const orphan = !connectedDomains.has(d.domain);
    const col = territoryColor(t);
    const data = {
      id: 'domain:' + d.domain,
      domain: d.domain,
      // 孤立领地标签显式标注"待连接"，让用户一眼区分（不只靠颜色）
      label: orphan
        ? `${domainDisplayName(d.domain)}\n${d.count} · 待连接`
        : `${domainDisplayName(d.domain)}\n${d.count} 节点`,
      labelWrap: Math.max(60, size - 12),
      count: d.count,
      avgAuth: d.avgAuth,
      // 孤立领地降饱和为中性灰，降低视觉权重（呼应"边缘/未接入"语义）
      color: orphan ? '#7a6b7a' : col,
      size,
      // 势力范围（领地辐射的柔光区域）：向外扩 = 领地半径的 ~42%
      // 黑曜石结论"区域感来自轮廓/填充"——underlay 大 padding + 低 opacity
      // 形成半透明色域，配合半透明填充 + 实线边界 = "有边界的地盘"而非"发光的点"。
      // authority 越高势力范围越浓（信息编码：权威度=地盘浓度，保留原语义）。
      // 孤立领地势力范围更淡（淡化权重，不与活跃领地争夺视觉焦点）。
      glow: size * 0.42,
      glowOp: orphan ? 0.05 + t * 0.04 : 0.09 + t * 0.12,
    };
    // 仅孤立领地设 orphan 标记：cytoscape node[orphan] 选择器按"字段存在"匹配，
    // 非孤立不设该字段，避免 false 值被误判为存在（否则所有领地都会变 dashed 灰）。
    if (orphan) data.orphan = true;
    return { data };
  });

  const edges = Object.entries(pairCount).map(([key, cnt]) => {
    const [a, b] = key.split('||');
    return {
      data: {
        id: `xdom:${a}-${b}`,
        source: 'domain:' + a,
        target: 'domain:' + b,
        width: 1 + Math.sqrt(cnt) * 0.55,
        count: cnt,
      },
    };
  });
  return { nodes, edges };
}

/** 构建仅含 currentDomain 的子图（L2 用）；edges 保留全集，toElements 按 visibleIds 自然过滤。 */
function domainFilteredGraph(domain) {
  return {
    nodes: graph.nodes.filter((n) => (n.domain || '_unsorted') === domain),
    edges: graph.edges,
  };
}

function hideLoading() {
  const el = root.querySelector('[data-loading]');
  if (el) el.hidden = true;
}
function showError() {
  hideLoading();
  const el = root.querySelector('[data-load-error]');
  if (el) el.hidden = false;
}

/**
 * 计算 cy 当前应渲染的元素集合（L2 域子图 / 全图；综合 kind / depth / 隐藏孤立 / compound）。
 * v2.7：L2（theme + currentDomain）基于域子图 + 域内索引；全图基于全集 + 全局索引。
 *      L1 主题地图不走本函数（用 buildTerritoryElements）。
 * v2.5：末尾经 injectNodeMeta 注入 confidence 边框分类（信息设计），不再注入 glow。
 * @returns {{nodes: Array, edges: Array}}
 */
function computeElements() {
  const hideIso = root.querySelector('[data-hide-isolated]').checked;
  const showLabels = root.querySelector('[data-show-labels]').checked;
  // L2：基图为当前域子图；全图：基图为全集。index 已在 enterLevel 按级别重建匹配。
  const baseGraph = (viewMode === 'theme' && currentDomain) ? domainFilteredGraph(currentDomain) : graph;
  const base = toElements(
    baseGraph,
    { kinds: activeKinds, hideIsolated: hideIso, showLabels },
    index.edgeIndex,
  );
  const withDepth = applyDepth(base, depthValue);
  const withCompound = buildCompoundElements(withDepth, compoundEnabled);
  return injectNodeMeta(withCompound);
}

/**
 * 销毁当前 cy 实例及其附属（惯性 / minimap / 各 rAF / hover 控制器）。
 * 从 unmount 抽出，供 enterLevel 切换层级时复用（不清理 mount 级 timers / root DOM）。
 * 幂等：cy 已为 null 时仅清残留 rAF。
 */
function destroyCy() {
  // [v2.10] 拖拽 pin 已移除（皮筋回弹优先），pinnedNodes 保留为防御性 no-op（空 map）。
  pinnedNodes.clear();
  if (entranceRaf) { cancelAnimationFrame(entranceRaf); entranceRaf = 0; }
  entranceItems = [];
  if (layoutTweenRaf) { cancelAnimationFrame(layoutTweenRaf); layoutTweenRaf = 0; }
  if (focusRaf) { cancelAnimationFrame(focusRaf); focusRaf = 0; }
  if (hoverCtl) { hoverCtl.stop(); hoverCtl = null; }
  destroyInertia();
  destroyMinimap();
  destroyBgFollow();   // v2.10：复位背景视差变量，避免跨层级残留 pan/zoom 偏移
  if (labelFadeRaf) { cancelAnimationFrame(labelFadeRaf); labelFadeRaf = 0; }
  lastLabelOp = -1;
  if (edgeViewportFadeRaf) { cancelAnimationFrame(edgeViewportFadeRaf); edgeViewportFadeRaf = 0; }
  stopLayout();
  stopPhysics();   // v2.8：销毁 d3-force simulation（层级切换/unmount 必清）
  if (cy) {
    try { cy.silent(true); } catch (e) { /* ignore */ }
    try { cy.remove(cy.elements()); } catch (e) { /* ignore */ }
    try { cy.destroy(); } catch (e) { /* ignore */ }
    cy = null;
  }
}

/**
 * v2.7 层级切换总入口。根据 viewMode / currentDomain 决定渲染 L1 领地图 / L2 域子图 / 全图。
 *  - 清聚焦态（L3 不跨层级保留）+ 关详情面板
 *  - 按级别重建 index（L2 用域子图索引，保证 hideIso/depth 在域内语义正确）
 *  - destroyCy + 挂对应 mounter + 更新导航/面板/统计
 * 幂等可重复调用（drill / 返回 / 模式切换都走这里）。
 */
function enterLevel() {
  // 清聚焦与详情（跨层级不保留 L3）
  focusedNodeId = null;
  focusedTitle = '';
  const detailPanel = root.querySelector('[data-detail-panel]');
  if (detailPanel) detailPanel.hidden = true;
  actions.clearFocus();
  // 清挂载级 transient timer（runLayout fit / restore-selected 等），避免跨层级误触发
  timers.forEach((t) => window.clearTimeout(t));
  timers.clear();
  if (depthDebounce) { window.clearTimeout(depthDebounce); depthDebounce = null; }

  const isL1 = (viewMode === 'theme' && !currentDomain);
  // index 始终基于全集：孤立/度数按全局判（L2 域子图里"只跨域连接"的节点不算孤立，
  // 否则会被误隐 → 钻取看到空图）。节点集由 computeElements 按 currentDomain 过滤。
  index = buildIndex(graph);
  // kind 筛选 chip 计数匹配当前级别（L2 显示域内 kind 计数）
  rebuildFilters();

  destroyCy();

  if (isL1) {
    mountThemeMap();
  } else {
    mountGraph();
  }
  renderNav();
  updatePanelForLevel(isL1);
  // L1 统计回写全量知识库规模（领地数不代表节点数，避免顶栏误读）；
  // L2/全图延后调 refilter：cytoscape 构造后 :visible 需一个渲染帧才生效，
  // 同步调用 renderNodeList 会得 0（旧代码 setTimeout(refilter,50) 即此因）。
  if (isL1) {
    updateStats(graph.nodes.length, graph.edges.length);
  } else {
    const t = window.setTimeout(() => refilter(), 60);
    timers.add(t);
  }
}

/** L1 主题地图挂载：领地气泡 + 跨域连线，~13 节点力导向（不会乱）。 */
function mountThemeMap() {
  if (!domainMap) domainMap = buildDomainMap(graph);
  const { nodes, edges } = buildTerritoryElements();
  cy = window.cytoscape({
    container: root.querySelector('[data-cy]'),
    elements: nodes.concat(edges),
    style: CY_STYLE_L1,
    wheelSensitivity: 0.2,
    minZoom: 0.15,
    maxZoom: 2.5,
  });
  // [dev 钩子] 仅 ?debug=1 时同步 cy 引用到 window（每次 mount 更新，跨层级保持新鲜）
  if (new URLSearchParams(window.location.search).has('debug')) window.__ek_cy = cy;
  // 领地 tap → 钻取进入域子图（L2）；空白 tap 不关详情（L1 无详情）
  cy.on('tap', 'node', (evt) => {
    const d = evt.target.data('domain');
    if (d) enterDomain(d);
  });
  bindHoverL1();
  // [B3] 领地拖拽锁定（拖完单体固定，runLayout 保留其位置）
  bindNodeDrag();
  // L1 不接 zoom 标签淡出（仅 ~13 领地，标签常显）；保留视口外边兜底（无害）
  cy.on('pan zoom', scheduleEdgeViewportFade);
  // v2.10 背景视差跟随（领地图也跟随 pan/zoom，空间感一致）
  cy.on('pan zoom', scheduleBgFollow);
  mountInertia();
  // L1 也显示 minimap（用户要求）
  const wrap = root.querySelector('[data-minimap-wrap]');
  if (wrap) wrap.hidden = false;
  mountMinimap();
  prepareEntrance();
  runLayout({ first: true, l1: true });
}

/** L1 领地 hover：樱粉描边提示可钻取 + 邻域（跨域连线）高亮，其余淡化（舞台光，克制）。 */
function bindHoverL1() {
  if (!cy) return;
  cy.on('mouseover', 'node', (evt) => {
    if (!cy) return;
    const n = evt.target;
    cy.elements().removeClass('hover-bright faded highlight');
    const nb = n.closedNeighborhood();
    cy.elements().not(nb).addClass('faded');
    nb.edges().addClass('highlight');
    n.addClass('hover-bright');
  });
  cy.on('mouseout', 'node', () => {
    if (!cy) return;
    cy.elements().removeClass('hover-bright faded highlight');
  });
}

/**
 * [v2.8 d3-force 拖拽] 绑定节点拖拽到 d3-force fx/fy + alphaTarget reheat（L1 领地 + L2/全图节点通用）。
 *
 * 核心物理语义（用户原话诉求 #2、#3）：
 *   - grab：d3.A.fx/fy = 当前位置 → 锁定 A；simulation.alphaTarget(0.3).restart() → reheat，
 *           其他节点被 charge 排斥、被 link 拉扯，实时物理响应（不再静态）。
 *   - 拖拽中（cytoscape position 事件）：同步 d3.A.fx/fy = 新位置 → d3-force 看到 A 在移动，
 *           collide 力把挡路的其他节点**实时排斥开**（防重叠 + 物理碰撞响应）。
 *   - free：alphaTarget(0) 结束拖拽加热；清除 fx/fy=null（节点不再锁定）+ alpha(0.5).restart()
 *           reheat 让皮筋力重新激活，把松手的节点拉回连接点附近（皮筋回弹）。
 *           [v2.10] 取消原 B3 拖拽 pin——pin 会让节点钉死在松手位置，皮筋永远拉不回。
 *
 * 关键：d3-force tick 时跳过 fx!=null 的节点（见 startPhysics），避免覆盖 cytoscape 鼠标位置。
 *      因此 cytoscape 拖 A 与 d3-force 控制其他节点不冲突。
 *
 * 降级：若 simulation 不可用（reduced-motion / d3-force 加载失败），无物理回弹，节点停在松手位置。
 */
function bindNodeDrag() {
  if (!cy) return;
  cy.on('grab', 'node', (evt) => {
    const n = evt.target;
    if (n.isParent()) return;
    const p = n.position();
    // v2.8 d3-force：锁定该节点 + reheat（让其他节点实时响应）
    if (simulation) {
      const d3n = simNodeMap.get(n.id());
      if (d3n) {
        d3n.fx = p.x;
        d3n.fy = p.y;
        draggingNodeId = n.id();
        simulation.alphaTarget(0.3).restart();
      }
    }
  });
  cy.on('position', 'node', (evt) => {
    // 拖拽期间 cytoscape 自动更新 A.position → 实时同步到 d3.A.fx/fy
    // 让 d3-force 看到 A 的最新位置，collide 才能把挡路的节点推开（实时物理碰撞）
    if (!simulation || !draggingNodeId) return;
    const n = evt.target;
    if (n.id() !== draggingNodeId) return;
    const d3n = simNodeMap.get(n.id());
    if (d3n) {
      const p = n.position();
      d3n.fx = p.x;
      d3n.fy = p.y;
    }
  });
  cy.on('free', 'node', (evt) => {
    const n = evt.target;
    draggingNodeId = null;
    if (simulation) {
      // [v2.10 皮筋回弹] 松手必清除 fx/fy → 节点不再锁定，simulation 完全接管；
      // alphaTarget(0) 结束拖拽加热，alpha(0.5).restart() reheat 让皮筋力
      // （makeRubberBandForce：距离衰减且可逆——回到阈值内力立刻恢复全强度）重新激活，
      // 把松手的节点拉回连接点附近，实现"皮筋回弹"。
      // 取消原 B3 拖拽 pin：pin（fx/fy 保留）会让节点钉死在松手位置，皮筋永远拉不回，
      // 与皮筋回弹语义直接冲突，故移除。pinnedNodes 基础设施保留为防御性 no-op。
      const d3n = simNodeMap.get(n.id());
      if (d3n) { d3n.fx = null; d3n.fy = null; }
      simulation.alphaTarget(0);
      simulation.alpha(0.5).restart();
    }
  });
}

/** 钻取进入某域子图（L2）。 */
function enterDomain(domain) {
  if (!domain) return;
  currentDomain = domain;
  enterLevel();
}

/** 返回 L1 主题地图。 */
function enterThemeMap() {
  currentDomain = null;
  viewMode = 'theme';
  enterLevel();
}

/** 切到全图（降级模式：189 节点全量力导向，标注「节点多会乱」）。 */
function enterFullGraph() {
  currentDomain = null;
  viewMode = 'full';
  enterLevel();
}

function mountGraph() {
  const { nodes, edges } = computeElements();

  cy = window.cytoscape({
    container: root.querySelector('[data-cy]'),
    elements: nodes.concat(edges),
    style: CY_STYLE,
    // layout 在初始化后手动 run，便于保存引用以在 unmount 时 stop
    wheelSensitivity: 0.2,
    minZoom: 0.15,
    maxZoom: 2.5,
  });
  // [dev 钩子] 仅 ?debug=1 时同步 cy 引用到 window（每次 mount 更新，跨层级保持新鲜）
  if (new URLSearchParams(window.location.search).has('debug')) window.__ek_cy = cy;

  cy.on('tap', 'node', (evt) => focusNode(evt.target));
  cy.on('tap', (evt) => { if (evt.target === cy) closeDetail(); });

  bindHover();
  // [B3] 节点拖拽锁定（拖完单体固定，runLayout 保留其位置）
  bindNodeDrag();
  // v2.7：L1 隐藏了 minimap，进入 L2/全图恢复显示
  const mwrap = root.querySelector('[data-minimap-wrap]');
  if (mwrap) mwrap.hidden = false;
  mountMinimap();
  // [v2.5 克制] 移除星空粒子背景（黑曜石：纯底色，无装饰层）
  // v2.5：zoom 驱动标签淡出（text fade threshold）
  cy.on('zoom', scheduleLabelFade);
  // v2.6 兜底：pan/zoom 时检测视口外边，给两端都被推出视口的边加 out-of-viewport（渐隐）。
  //   不在此处主动 updateEdgeViewportFade()——cy 刚初始化、runLayout 尚未 fit（80ms 后），
  //   此时节点都在默认位置（视口外），主动调用会全边误 dim。
  //   依赖后续 fit 触发的 zoom/pan 事件自然首次计算（fit 内部 set zoom/pan 必触发事件）。
  cy.on('pan zoom', scheduleEdgeViewportFade);
  // v2.10 背景视差跟随（网格 1:1 跟 pan，按 zoom 缩放，星辰视差慢移）
  cy.on('pan zoom', scheduleBgFollow);
  updateLabelFade();
  // v2.3：wheel/拖拽惯性（接管原生，给跟手+减速手感；reduced-motion 内部已跳过）
  mountInertia();
  // v2.3：入场前置（压成未生态，避免布局期间原点闪现）
  prepareEntrance();
  // v2.3：首次布局走入场动画（不做位置过渡，因初始无旧位置可过渡）
  runLayout({ first: true });
}

// ===== v2.5 克制 · zoom label 淡出（Obsidian text fade threshold）=====
// 缩放低于阈值时标签淡出（远看简洁），近看显示。基于 zoom 级别驱动 node text-opacity。
// 同一张图，缩放级别控制信息密度——不需要两套渲染。
// 量化到 0.05 步进，仅在跨档时更新 stylesheet（避免 wheel 惯性每帧 style 重算）。
const LABEL_FADE_START = 0.3;   // zoom < 此值 → 标签完全淡出（只看节点+线）
const LABEL_FADE_FULL = 0.8;    // zoom > 此值 → 标签完全不透明（看清结构）
let labelFadeRaf = 0;
let lastLabelOp = -1;

function scheduleLabelFade() {
  if (labelFadeRaf || !cy) return;
  labelFadeRaf = requestAnimationFrame(() => { labelFadeRaf = 0; updateLabelFade(); });
}

function updateLabelFade() {
  if (!cy) return;
  const z = cy.zoom();
  const raw = clampN((z - LABEL_FADE_START) / (LABEL_FADE_FULL - LABEL_FADE_START), 0, 1);
  const op = Math.round(raw * 20) / 20;   // 量化 0.05 步进
  if (op === lastLabelOp) return;
  lastLabelOp = op;
  try { cy.style().selector('node').style('text-opacity', op).update(); }
  catch (e) { console.debug('[graph] label fade 容错', e); }
}

// ===== v2.10 背景视差跟随（空间感）=====
// 蓝图背景原本 CSS 静态，cytoscape zoom/pan 时背景不动（节点飘在固定网格上，空间感割裂）。
// 监听 cy.on('pan zoom') → rAF 节流写 --bg-px/--bg-py/--bg-zoom 到 .graph-canvas，
// CSS 用 calc 让网格层 1:1 跟随 pan + 按 zoom 缩放 background-size，星辰层视差慢移（深度）。
// 性能：仅设 3 个 CSS 自定义属性，浏览器重合成背景层（不重绘 cytoscape canvas），单帧开销恒定。
function scheduleBgFollow() {
  if (bgFollowRaf || !cy) return;
  bgFollowRaf = requestAnimationFrame(() => { bgFollowRaf = 0; updateBgFollow(); });
}

function updateBgFollow() {
  if (!cy || !root) return;
  const host = root.querySelector('[data-cy]');
  if (!host) return;
  const pan = cy.pan() || { x: 0, y: 0 };
  const z = cy.zoom() || 1;
  host.style.setProperty('--bg-px', pan.x + 'px');
  host.style.setProperty('--bg-py', pan.y + 'px');
  host.style.setProperty('--bg-zoom', String(z));
}

/** destroyCy/层级切换时复位变量到默认（避免上一层级残留 pan/zoom 偏移到新层级初始帧）。 */
function destroyBgFollow() {
  if (bgFollowRaf) { cancelAnimationFrame(bgFollowRaf); bgFollowRaf = 0; }
  if (root) {
    const host = root.querySelector('[data-cy]');
    if (host) {
      host.style.setProperty('--bg-px', '0px');
      host.style.setProperty('--bg-py', '0px');
      host.style.setProperty('--bg-zoom', '1');
    }
  }
}

// [v2.5 克制] 移除 starfield / edgeFlow / personaPulse 整套（黑曜石调研：高级感来自克制不是发光）。
//   - starfield：星空粒子 canvas（社区 hack，非 Obsidian 默认）→ 删
//   - edgeFlow：边 dash-offset 流光动画（Obsidian 默认无边动画）→ v2.4 已删，静态虚线形态保留
//   - personaPulse：freshness 呼吸脉冲（Obsidian 无节点动画）→ v2.4 已删
//   freshnessClass data 派生保留（injectNodeMeta），备将来轻量复用。


// ===== v2.3 动效打磨 · spring 驱动集 =====
// 1. 节点入场（stagger 淡入 + spring 缩放）
// 2. hover 弹性（单节点 spring scale）
// 3. 重新布局位置过渡（capture→restore→tween）
// 4. 聚焦 spring（pan + zoom 非线性）
// 5. 惯性（wheel 缩放跟手+减速 / 拖拽平移惯性）
// 性能：节点数 ~141，逐帧 cy.batch 合并 redraw；hover/聚焦单节点/视口级开销恒定。

/** 弹性/减速缓动（闭式，不引动画库；spring 视觉感由 easeOutBack 过冲提供） */
function easeOutBack(t) {
  t = clampN(t, 0, 1);                          // 防御：调用方未守卫时钳到 [0,1]，避免 t<0 得负值
  const c1 = 1.70158, c3 = c1 + 1, u = t - 1;
  return 1 + c3 * u * u * u + c1 * u * u;   // 轻微过冲 → 弹性
}
function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }
function easeOutQuart(t) { return 1 - Math.pow(1 - t, 4); }   // 强减速 → 平滑落定（聚焦用）
function easeInOutCubic(t) {
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}
function clampN(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

// ---------- 1. 节点入场 / 淡入（stagger + spring scale）----------
/**
 * 入场前置：布局前把叶子节点压成"未生"态（透明 + 微点 + entering 关 transition），
 * 避免布局期间（约 80ms + 力导向）原点闪现。reduced-motion 直接跳过保持可见。
 */
function prepareEntrance() {
  if (!cy || motionReduced) return;
  cy.batch(() => {
    cy.nodes().forEach((n) => {
      if (n.isParent()) return;
      n.addClass('entering').style({ opacity: 0, width: 2, height: 2 });
    });
  });
}

/**
 * 在给定节点集合上播放入场。stagger=true 时按 authority 降序错峰（重要节点先亮）；
 * scale 走 easeOutBack（弹性过冲），opacity 走 easeOutCubic（平滑不过冲）。
 * 复用场景：首挂载全量（stagger）/ refilter 新增节点（stagger=false）。
 * @param {cytoscape.Collection} nodes
 * @param {boolean} stagger
 */
function playEntranceOn(nodes, stagger) {
  if (!cy) return;
  const list = nodes.filter((n) => !n.isParent());
  if (!list.length) return;
  if (motionReduced) {
    cy.batch(() => {
      list.forEach((n) => applyEntranceFinal(n, Number(n.data('size')) || 20));
    });
    return;
  }
  if (stagger) {
    list.sort((a, b) => (Number(b.data('authority')) || 0) - (Number(a.data('authority')) || 0));
  }
  // 中断保护：若上一轮入场仍在进行（如 refilter 在 mount 入场中触发），
  // 先把在场的入场节点 snap 到终态，避免取消后卡在半透明/半尺寸中间态。
  if (entranceRaf) { cancelAnimationFrame(entranceRaf); entranceRaf = 0; }
  snapEntranceItems();
  const items = list.map((n, i) => ({
    n,
    base: Number(n.data('size')) || 20,
    delay: stagger ? i * ENTRANCE_STAGGER : 0,
  }));
  entranceItems = items;                       // 记录在场集合，供下一次中断 snap
  // 预置未生态（idempotent：prepareEntrance 已设过则幂等）
  cy.batch(() => {
    items.forEach((it) => {
      it.n.addClass('entering').style({ opacity: 0, width: 2, height: 2 });
    });
  });
  const start = performance.now();
  entranceRaf = requestAnimationFrame(function tick(now) {
    if (!cy) { entranceRaf = 0; entranceItems = []; return; }
    const el = (now - start) / 1000;
    let allDone = true;
    cy.batch(() => {
      items.forEach((it) => {
        const tt = el - it.delay;
        if (tt <= 0) { allDone = false; return; }
        if (tt >= ENTRANCE_DUR) {
          applyEntranceFinal(it.n, it.base);
          return;
        }
        allDone = false;
        const p = tt / ENTRANCE_DUR;
        const sc = easeOutBack(p);
        const op = easeOutCubic(p);
        const s = Math.max(0.5, it.base * sc);
        it.n.style({ opacity: op, width: s, height: s });
      });
    });
    if (allDone) { entranceRaf = 0; entranceItems = []; }
    else entranceRaf = requestAnimationFrame(tick);
  });
}

/** 把进行中的入场节点即时 snap 到终态（中断/卸载时调用，避免卡中间态）。 */
function snapEntranceItems() {
  if (!cy || !entranceItems.length) { entranceItems = []; return; }
  cy.batch(() => {
    entranceItems.forEach((it) => applyEntranceFinal(it.n, it.base));
  });
  entranceItems = [];
}

/** 入场终态：opacity=1 / size=base / 移除 entering（恢复 160ms transition 供 faded/hover 用）。 */
function applyEntranceFinal(node, base) {
  node.style({ opacity: 1, width: base, height: base }).removeClass('entering');
}

/** 首挂载入场：全量叶子节点 + stagger 错峰。
 *  [vision v2.6 范围外修复·影响验证] 原 playEntranceOn(cy.nodes().leaves(), true) 的 .leaves()
 *  在 cytoscape 3.30.2 非 compound 图里基于有向图 traversal（出度为 0 的 DAG 叶子），不是"所有
 *  无子节点的节点"。导致 prepareEntrance 把 189 节点全设 opacity=0，但 playEntrance 只跑 84 个
 *  DAG 叶子，剩 105 个（concept/dossier/note + 部分 source，都有出边）永远卡 opacity=0 → 边连到
 *  透明节点 → 视觉上"边指向不可见节点"（vision 报告 30% 过滤同步 + 10% 悬空的实际根源）。
 *  改为 cy.nodes() 与 prepareEntrance 对称；playEntranceOn 内部已 filter !isParent，
 *  compound 父节点（若将来开启）仍会跳过。
 */
function playEntrance() { if (cy) playEntranceOn(cy.nodes(), true); }

// ---------- 2. hover 弹性反馈（单节点 spring scale）----------
/**
 * 对单节点跑 spring 缩放（width/height）。width/height 不在 transition-property，无冲突；
 * v2.5 克制：border 提亮由 hover-bright class（160ms transition）叠加，弹性质感由本 spring 过冲提供。
 * 控制器暴露 stop（软停）/ snapBase（即时复原）。
 */
function springNodeSize(node, toScale, opts = {}) {
  const base = Number(node.data('size')) || 20;
  const curW = parseFloat(node.style('width'));
  const fromScale = Number.isFinite(curW) && curW > 0 ? curW / base : 1;
  const stiffness = opts.stiffness || 220;
  const damping = opts.damping || 18;
  let pos = fromScale, vel = 0, raf = 0, last = 0, done = false;
  const apply = (s) => {
    const v = Math.max(2, base * s);
    node.style({ width: v, height: v });
  };
  function tick(now) {
    if (done || !cy) return;
    if (!last) last = now;
    let dt = (now - last) / 1000; last = now;
    dt = Math.min(dt, 0.032);                       // 钳制（后台大间隔不爆冲）
    const force = -stiffness * (pos - toScale) - damping * vel;
    vel += force * dt;
    pos += vel * dt;
    apply(pos);
    if (Math.abs(vel) < 0.02 && Math.abs(pos - toScale) < 0.01) {
      done = true; apply(toScale); raf = 0; opts.onSettle && opts.onSettle(); return;
    }
    raf = requestAnimationFrame(tick);
  }
  raf = requestAnimationFrame(tick);
  return {
    stop() { if (raf) cancelAnimationFrame(raf); raf = 0; done = true; },
    snapBase() { apply(1); },                        // 切换 hover 目标时即时复原
  };
}

function hoverSpringStart(node, toScale) {
  if (motionReduced || !cy) return;
  if (hoverCtl) { hoverCtl.stop(); hoverCtl.snapBase(); }
  hoverCtl = springNodeSize(node, toScale);
}
function hoverSpringOut(node) {
  if (motionReduced || !cy || !node) return;
  if (hoverCtl) hoverCtl.stop();
  hoverCtl = springNodeSize(node, 1.0, {
    stiffness: 300, damping: 24,
    onSettle: () => { hoverCtl = null; },
  });
}

// ---------- 3. 重新布局位置过渡（capture → restore → tween）----------
function capturePositions() {
  const map = new Map();
  if (!cy) return map;
  cy.nodes().forEach((n) => {
    if (n.isParent()) return;
    const p = n.position();
    map.set(n.id(), { x: p.x, y: p.y });
  });
  return map;
}

/**
 * 节点从旧位置平滑过渡到布局新位置（非瞬移）。
 * 流程：布局已设新位置 → 先退回旧位置 → rAF 沿 easeInOutCubic 插值到新位置 → 完成后 fit。
 * 仅纳入真有位移的节点（省批量开销）。reduced-motion 直接 fit。
 */
function tweenLayoutPositions(oldPos) {
  if (!cy || motionReduced) { fitAllVisible(); return; }
  if (layoutTweenRaf) cancelAnimationFrame(layoutTweenRaf);
  const targets = [];
  // 在同一 batch 内读取新位置 + 退回旧位置：layout.run() 设了新位置但尚未渲染，
  // batch 内同步退回旧位置，渲染器只看到"旧位置→tween"，无反向闪烁。
  cy.batch(() => {
    oldPos.forEach((p, id) => {
      const n = cy.getElementById(id);
      if (n && n.length && !n.isParent()) {
        const to = n.position();
        if (Math.abs(to.x - p.x) > 0.5 || Math.abs(to.y - p.y) > 0.5) {
          targets.push({ n, fx: p.x, fy: p.y, tx: to.x, ty: to.y });
          n.position({ x: p.x, y: p.y });
        }
      }
    });
  });
  if (!targets.length) { fitAllVisible(); return; }
  const startT = performance.now();
  layoutTweenRaf = requestAnimationFrame(function tick(now) {
    if (!cy) { layoutTweenRaf = 0; return; }
    const t = Math.min(1, (now - startT) / LAYOUT_TWEEN_DUR);
    const e = easeInOutCubic(t);
    cy.batch(() => {
      targets.forEach((o) => {
        o.n.position({ x: o.fx + (o.tx - o.fx) * e, y: o.fy + (o.ty - o.fy) * e });
      });
    });
    if (t < 1) layoutTweenRaf = requestAnimationFrame(tick);
    else { layoutTweenRaf = 0; fitAllVisible(); }
  });
}

// ---------- 4. 聚焦动画（spring pan + zoom，非线性跳）----------
/**
 * 平滑把节点拉到视口中心并放大到 FOCUS_ZOOM（仅放大，不强制缩小）。
 * pan/zoom 沿 easeOutQuart 插值（强减速 → 平滑落定），非线性、有"飞过去"的聚焦感。
 * reduced-motion 即时 center+zoom。
 */
function animateFocusTo(node) {
  if (!cy || !node) return;
  if (motionReduced) {
    try {
      cy.viewport({ zoom: Math.max(cy.zoom(), FOCUS_ZOOM) });
      cy.center({ eles: node });
    } catch (e) { console.debug('[graph] focus 即时定位容错', e); }
    return;
  }
  if (focusRaf) { cancelAnimationFrame(focusRaf); focusRaf = 0; }
  const startZoom = cy.zoom();
  const targetZoom = Math.max(startZoom, FOCUS_ZOOM);
  const np = node.position();
  const vw = cy.width(), vh = cy.height();
  // v2.3：容器隐藏（详情面板遮住/视图切换中）时 width/height=0，跳过避免把节点推出视口
  if (!vw || !vh) return;
  const startPan = { x: cy.pan().x, y: cy.pan().y };
  const targetPan = { x: vw / 2 - np.x * targetZoom, y: vh / 2 - np.y * targetZoom };
  const startT = performance.now();
  focusRaf = requestAnimationFrame(function tick(now) {
    if (!cy) { focusRaf = 0; return; }
    const t = Math.min(1, (now - startT) / FOCUS_DUR);
    const e = easeOutQuart(t);
    const z = startZoom + (targetZoom - startZoom) * e;
    const px = startPan.x + (targetPan.x - startPan.x) * e;
    const py = startPan.y + (targetPan.y - startPan.y) * e;
    try { cy.viewport({ zoom: z, pan: { x: px, y: py } }); } catch (err) { console.debug('[graph] focus 动画帧容错', err); }
    if (t < 1) focusRaf = requestAnimationFrame(tick);
    else focusRaf = 0;
  });
}

// ---------- 5. 惯性：wheel 缩放跟手+减速 / 拖拽平移惯性 ----------
/**
 * 接管 wheel（capture 拦截，阻止 cytoscape 原生逐事件跳变）→ rAF 指数平滑趋近 target，
 * 给出"跟手 + 减速"手感；同时采样背景拖拽的 pan 速度，松手后指数衰减惯性继续。
 * reduced-motion：完全不动用，保留 cytoscape 原生（即时、无动效）。
 */
function mountInertia() {
  if (!cy || motionReduced) return;
  const host = cy.container();
  if (!host) return;

  host.addEventListener('wheel', onWheel, { capture: true, passive: false });
  cy.on('pan', onPanSample);
  host.addEventListener('mousedown', onPanStart);
  window.addEventListener('mouseup', onPanEnd);
  mountInertia._host = host;

  function onWheel(e) {
    if (!cy) return;
    e.preventDefault();
    e.stopPropagation();
    const rect = host.getBoundingClientRect();
    const pan = cy.pan();
    const z = cy.zoom();
    // 光标处的模型坐标（缩放锚点，保持该点不动）
    const mx = (e.clientX - rect.left - pan.x) / z;
    const my = (e.clientY - rect.top - pan.y) / z;
    let delta = -e.deltaY;
    if (e.deltaMode === 1) delta *= 16;        // DOM_DELTA_LINE
    else if (e.deltaMode === 2) delta *= 100;  // DOM_DELTA_PAGE
    const factor = Math.exp(delta * 0.0016);   // 指数缩放，量感均匀
    const minZ = cy.minZoom ? cy.minZoom() : 0.15;
    const maxZ = cy.maxZoom ? cy.maxZoom() : 2.5;
    wheelTarget = clampN(z * factor, minZ, maxZ);
    // 新手势（rAF 未运行）从实际缩放起跳，避免跨手势（focus/fit 改过 zoom 后）状态漂移；
    // 手势进行中保留平滑值，由 rAF 持续 ease。
    if (!wheelZoomRaf) wheelCurrent = z;
    wheelAnchor = { x: mx, y: my };
    if (!wheelZoomRaf) startWheelZoom();
  }

  function startWheelZoom() {
    let last = performance.now();
    const k = 13;                               // 趋近速率（dt-based，帧率无关）
    wheelZoomRaf = requestAnimationFrame(function tick(now) {
      if (!cy) { wheelZoomRaf = 0; return; }
      const dt = Math.min(0.05, (now - last) / 1000); last = now;
      const rate = 1 - Math.exp(-k * dt);
      wheelCurrent += (wheelTarget - wheelCurrent) * rate;
      try { cy.zoom({ level: wheelCurrent, position: wheelAnchor }); } catch (e) { console.debug('[graph] wheel zoom 帧容错', e); }
      if (Math.abs(wheelTarget - wheelCurrent) < 0.0015) {
        wheelCurrent = wheelTarget;
        wheelZoomRaf = 0;
        return;
      }
      wheelZoomRaf = requestAnimationFrame(tick);
    });
  }

  function onPanStart() {
    panSampling = true;
    panVel = { x: 0, y: 0 };
    onPanSample._ts = 0;
    if (panInertiaRaf) { cancelAnimationFrame(panInertiaRaf); panInertiaRaf = 0; }
    panInerting = false;
  }
  function onPanSample() {
    if (!cy || panInerting || !panSampling) return;
    // 'pan' 仅在真平移（背景拖拽）时触发；节点拖动不触发 → 速度即背景拖拽速度
    const p = cy.pan();
    const now = performance.now();
    const lastTs = onPanSample._ts || 0;
    if (lastTs) {
      const dt = now - lastTs;
      if (dt > 0) {
        const nx = (p.x - onPanSample._x) / dt;
        const ny = (p.y - onPanSample._y) / dt;
        // 一阶低通平滑，抑制抖动峰值
        panVel.x = panVel.x ? panVel.x * 0.6 + nx * 0.4 : nx;
        panVel.y = panVel.y ? panVel.y * 0.6 + ny * 0.4 : ny;
      }
    }
    onPanSample._ts = now;
    onPanSample._x = p.x;
    onPanSample._y = p.y;
  }
  function onPanEnd() {
    if (!panSampling) return;
    panSampling = false;
    onPanSample._ts = 0;
    if (Math.hypot(panVel.x, panVel.y) < PAN_INERTIA_MIN) return;
    startPanInertia();
  }
  function startPanInertia() {
    panInerting = true;
    let vx = panVel.x, vy = panVel.y;
    let last = performance.now();
    panInertiaRaf = requestAnimationFrame(function tick(now) {
      if (!cy || !panInerting) { panInertiaRaf = 0; panInerting = false; return; }
      const dt = Math.min(0.05, (now - last) / 1000); last = now;
      try { cy.panBy({ x: vx * dt * 1000, y: vy * dt * 1000 }); } catch (e) { console.debug('[graph] pan inertia 帧容错', e); }
      const decay = Math.exp(-PAN_INERTIA_K * dt);
      vx *= decay; vy *= decay;
      if (Math.hypot(vx, vy) < PAN_INERTIA_MIN) { panInertiaRaf = 0; panInerting = false; return; }
      panInertiaRaf = requestAnimationFrame(tick);
    });
  }

  mountInertia._cleanup = function cleanup() {
    host.removeEventListener('wheel', onWheel, { capture: true });
    if (cy) cy.off('pan', onPanSample);
    host.removeEventListener('mousedown', onPanStart);
    window.removeEventListener('mouseup', onPanEnd);
    if (wheelZoomRaf) { cancelAnimationFrame(wheelZoomRaf); wheelZoomRaf = 0; }
    if (panInertiaRaf) { cancelAnimationFrame(panInertiaRaf); panInertiaRaf = 0; }
    panInerting = false; panSampling = false;
  };
}

function destroyInertia() {
  if (mountInertia._cleanup) { mountInertia._cleanup(); mountInertia._cleanup = null; }
  mountInertia._host = null;
}

// ===== fit 全集可见元素（vision 修复 v2.6：边指向不可见节点）=====
// 原 fitLargestComponent 在主分量占比 > 65% 时只 fit 主分量，导致小分量（飞点 / 第二 cluster）
// 跑到视口外，其与视口内节点的连边画到边缘外（vision 报告：60% 边指向不可见节点）。
// 改为 fit 全集 :visible，确保所有可见节点（含 label 空间，padding 80）都在视口内。
// 多分量场景下全图 fit 反而能一眼看到全貌（原 65% 阈值反而漏边缘飞点）。
function fitAllVisible() {
  if (!cy) return;
  const target = cy.elements(':visible');
  if (!target || target.length === 0) return;
  // v2.3：reduced-motion 归零——即时 fit，不走 380ms 视口缓动（a11y 硬承诺）
  if (motionReduced) {
    try { cy.fit(target, 80); cy.center({ eles: target }); } catch (e) { console.debug('[graph] fit 容错', e); }
    return;
  }
  try {
    cy.animate(
      { fit: { eles: target, padding: 80 }, center: { eles: target } },
      { duration: 380 },
    );
  } catch (e) {
    console.debug('[graph] animate-fit 异常，回退即时 fit', e);
    try { cy.fit(target, 80); } catch (e2) { console.debug('[graph] fit fallback 容错', e2); }
  }
}

// [vision v29 L1 修复] L1 收敛感知 fit。
// 背景：d3-force 从 alpha=1 退火到收敛约需 300 ticks（alphaDecay 0.0228 → ~5s）。
//       runLayout 物理 path 的 80ms 初始 fit 太早（alpha≈0.9，领地仍在高速演化），之后
//       物理继续推移领地，最终位置与初始 fit 框不一致。调弱 charge/加强 center 后领地不再
//       飞边缘，但仍需等物理冷到准稳态再补一次 animated fit，确保 13 领地均匀散布居中。
// 实现：rAF 轮询 simulation.alpha()，降到阈值（0.06，约 110 ticks ≈ 1.8s）后执行
//       fitAllVisible（内含 cy.animate 380ms 视口缓动）。幂等：重复调用取消旧 watcher；
//       simulation/cy 失效时自动停止。reduced-motion 不走物理 path，本函数不触发。
function scheduleL1ConvergeFit() {
  if (l1ConvergeRaf) cancelAnimationFrame(l1ConvergeRaf);
  l1ConvergeRaf = requestAnimationFrame(function check() {
    if (!simulation || !cy) { l1ConvergeRaf = 0; return; }
    if (simulation.alpha() < 0.06) {       // 准稳态：领地位置基本稳定，可 refit
      l1ConvergeRaf = 0;
      fitAllVisible();
      return;
    }
    l1ConvergeRaf = requestAnimationFrame(check);
  });
}

// ===== 视口外边兜底（vision v2.6：fit 全集修了后的极端残留场景兜底）=====
// 正常 fit 全集后所有节点都在视口内，本函数是 no-op；仅用户主动放大/拖动把节点推出视口时，
// 给"两端都在视口外"的边加 out-of-viewport class（opacity 0.08），避免线生硬延伸到边缘外。
// 聚焦模式下跳过（faded 已隔离，叠加无意义），同时清残留 class 避免退出聚焦后未恢复。
let edgeViewportFadeRaf = 0;

function scheduleEdgeViewportFade() {
  if (edgeViewportFadeRaf || !cy) return;
  edgeViewportFadeRaf = requestAnimationFrame(() => {
    edgeViewportFadeRaf = 0;
    updateEdgeViewportFade();
  });
}

function updateEdgeViewportFade() {
  if (!cy) return;
  // 聚焦模式：faded 已隔离，跳过；同时清残留 out-of-viewport 避免退出聚焦后未恢复
  if (cy.$('.focused').nonempty()) {
    const stuck = cy.edges('.out-of-viewport');
    if (stuck.length) cy.batch(() => stuck.removeClass('out-of-viewport'));
    return;
  }
  const ext = cy.extent();
  if (!ext || !ext.w || !ext.h) return;
  // 节点是否在视口内（含 pad 缓冲，避免边缘抖动反复 add/remove class）
  const pad = 4;
  const inVp = (node) => {
    const p = node.position();
    return p.x >= ext.x1 - pad && p.x <= ext.x2 + pad
        && p.y >= ext.y1 - pad && p.y <= ext.y2 + pad;
  };
  const toDim = [];
  const toRestore = [];
  cy.edges(':visible').forEach((e) => {
    // 仅"两端都在视口外"才 dim；一端在内一端在外的边用户能看到半截，属正常可见边
    const bothOut = !inVp(e.source()) && !inVp(e.target());
    if (bothOut && !e.hasClass('out-of-viewport')) toDim.push(e);
    else if (!bothOut && e.hasClass('out-of-viewport')) toRestore.push(e);
  });
  if (toDim.length || toRestore.length) {
    cy.batch(() => {
      toDim.forEach((e) => e.addClass('out-of-viewport'));
      toRestore.forEach((e) => e.removeClass('out-of-viewport'));
    });
  }
}

function focusNode(node) {
  if (!cy) return;
  // v2.3：tap 中断 hover 时，停掉残留的 hover spring（mouseout 不一定触发），避免放大态残留
  if (hoverCtl) { hoverCtl.stop(); hoverCtl.snapBase(); hoverCtl = null; }
  cy.elements().removeClass('focused faded highlight hover-dim hover-bright');
  const neighborhood = node.closedNeighborhood();
  cy.elements().not(neighborhood).addClass('faded');
  node.addClass('focused');
  neighborhood.edges().addClass('highlight');
  showDetail(node.data());
  // 同步节点列表的 aria-selected，让 AT 用户能感知当前选中
  syncNodeListSelection(node.id());
  // 选中态同步走 actions（图内 UI 聚焦仍由本函数负责，状态部分委托 actions 层）
  actions.focusNode(node.id());
  // v2.3：聚焦 spring pan/zoom（非线性，平滑把节点拉到中心并放大）
  animateFocusTo(node);
  // v2.7：导航栏同步聚焦 crumb + 退出聚焦按钮（取代 v2.4 focus-mode-bar）
  focusedNodeId = node.id();
  focusedTitle = node.data('fullTitle') || node.data('label') || node.id();
  renderNav();
}

/**
 * 按 id 聚焦画布节点（供 rel-row / 节点列表等 UI 复用）。
 * 触发 focusNode 全套（高亮 / 详情 / 状态同步 / v2.3 聚焦动画）。
 * @param {string} id 节点 id
 */
function focusByNodeId(id) {
  if (!cy || !id) return;
  const tn = cy.getElementById(id);
  if (tn && tn.length) {
    // v2.3：pan/zoom spring 已并入 focusNode，此处不再单独 cy.animate（避免双重动画）
    focusNode(tn);
  }
}

/**
 * 渲染键盘可达节点列表。从 cy 当前可见节点派生（不含 compound 父节点）。
 * 列表行为：
 *  - 排序按 authority 降序（与图谱视觉重点一致）
 *  - 文本筛选（input 即时筛选）
 *  - 上限 30 项（避免 189 节点全量列表过载；输入筛选后可触达更多）
 *  - 当前选中节点 aria-selected=true（随 focusNode 同步）
 */
function renderNodeList() {
  if (!root) return;
  const box = root.querySelector('[data-node-list]');
  const metaEl = root.querySelector('[data-node-list-meta]');
  if (!box) return;

  const searchInput = root.querySelector('[data-node-search]');
  const q = (searchInput && searchInput.value || '').trim().toLowerCase();

  if (!cy) {
    box.innerHTML = '';
    if (metaEl) metaEl.textContent = '';
    return;
  }

  // 取可见叶子节点 → 映射回 graph 原始节点（取 title/slug 等元数据）
  // v2.7：以 computeElements 为可见集合真源。cy :visible 在布局/入场期间异步解析
  //      （实测 60ms=0 / 500ms 才满），同步渲染节点列表会得空。computeElements 是
  //      纯数据派生，立即可用，且与画布渲染口径一致。
  const els = computeElements();
  const visibleIds = els.nodes
    .filter((n) => !String(n.data.id).startsWith('compound-'))
    .map((n) => n.data.id);
  let candidates = visibleIds
    .map((id) => graph.nodes.find((n) => n.id === id))
    .filter((n) => n);

  if (q) {
    candidates = candidates.filter((n) => {
      const hay = `${n.title} ${n.slug || ''} ${n.kind} ${n.domain || ''}`.toLowerCase();
      return hay.includes(q);
    });
  }

  candidates.sort((a, b) => (Number(b.authority) || 0) - (Number(a.authority) || 0));

  const total = candidates.length;
  const MAX = 30;
  const shown = candidates.slice(0, MAX);
  const selectedId = store.getState().selectedNodeId;

  box.innerHTML = '';
  if (total === 0) {
    box.innerHTML = `<div class="node-list-empty muted">${q ? '无匹配节点' : '当前无可见节点'}</div>`;
    if (metaEl) metaEl.textContent = '';
    return;
  }

  shown.forEach((n) => {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'node-list-item';
    // 用 aria-current="true" 标记当前选中节点（不误导为 listbox option）
    if (n.id === selectedId) item.setAttribute('aria-current', 'true');
    item.dataset.nodeId = n.id;
    const color = KIND_COLORS[n.kind] || '#897989';
    item.innerHTML = `<span class="nl-dot" style="background:${color};color:${color}" aria-hidden="true"></span>`
      + `<span class="nl-title">${escapeHtml(trunc(n.title, 22))}</span>`;
    item.addEventListener('click', () => focusByNodeId(n.id));
    box.appendChild(item);
  });

  if (metaEl) {
    metaEl.textContent = total > MAX
      ? `显示前 ${MAX} · 共 ${total} 个`
      : `共 ${total} 个`;
  }
}

/** focusNode 时同步节点列表选中态（不重渲染，只更新 aria-current，避免输入焦点丢失） */
function syncNodeListSelection(id) {
  if (!root) return;
  const items = root.querySelectorAll('[data-node-list] .node-list-item');
  items.forEach((it) => {
    if (it.dataset.nodeId === id) {
      it.setAttribute('aria-current', 'true');
    } else {
      it.removeAttribute('aria-current');
    }
  });
}

/** 绑定节点列表搜索框（rAF 节流重渲染） */
function bindNodeListSearch() {
  const searchInput = root.querySelector('[data-node-search]');
  if (!searchInput) return;
  let raf = 0;
  searchInput.addEventListener('input', () => {
    if (raf) cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => { raf = 0; renderNodeList(); });
  });
}

function showDetail(d) {
  const panel = root.querySelector('[data-detail-panel]');
  panel.hidden = false;
  const kindEl = root.querySelector('[data-d-kind]');
  kindEl.textContent = KIND_LABELS[d.kind] || d.kind;
  kindEl.style.color = KIND_COLORS[d.kind] || '';
  kindEl.style.borderColor = KIND_COLORS[d.kind] || '';
  root.querySelector('[data-d-title]').textContent = d.fullTitle || d.label;

  const meta = root.querySelector('[data-d-meta]');
  const tags = [
    d.domain && d.domain !== '_unsorted' ? `<span class="tag">${escapeHtml(d.domain)}</span>` : '',
    d.status ? `<span class="tag">${escapeHtml(STATUS_LABELS[d.status] || d.status)}</span>` : '',
    d.freshness ? `<span class="tag">${escapeHtml(FRESHNESS_LABELS[d.freshness] || d.freshness)}</span>` : '',
  ].join('');
  meta.innerHTML = tags || `<span class="tag">无域归属</span>`;

  root.querySelector('[data-d-auth-val]').textContent = (Number(d.authority) || 0).toFixed(4);
  root.querySelector('[data-d-auth-fill]').style.width = authPct(Number(d.authority) || 0) + '%';

  const rels = index.edgeIndex.get(d.id) || [];
  root.querySelector('[data-d-rel-count]').textContent = rels.length + ' 条';
  const relBox = root.querySelector('[data-d-relations]');
  relBox.innerHTML = '';
  if (rels.length === 0) {
    relBox.innerHTML = `<div class="muted" style="font-size:12px;padding:4px">孤立节点，无关系</div>`;
  } else {
    rels.forEach((r) => {
      const otherId = r.source === d.id ? r.target : r.source;
      const dir = r.source === d.id ? '→' : '←';
      const other = graph.nodes.find((n) => n.id === otherId);
      // a11y：用 <button> 替代 <div onclick>，原生支持 Enter/Space 触发 + Tab 可达
      // 对齐 detail 视图 rel-item 的 button 写法（工程内一致性）
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'rel-row';
      row.setAttribute('aria-label',
        `聚焦关系节点 ${other ? other.title : otherId}（${EDGE_LABELS[r.type] || r.type}）`);
      row.innerHTML = `<span class="rel-type">${escapeHtml(EDGE_LABELS[r.type] || r.type)}</span>`
        + `<span class="rel-arrow">${dir}</span>`
        + `<span class="rel-title">${escapeHtml(other ? trunc(other.title, 16) : otherId)}</span>`;
      row.addEventListener('click', () => focusByNodeId(otherId));
      relBox.appendChild(row);
    });
  }

  // A1：主入口按钮 → 跳完整详情视图（actions.openDetail 走路由 + 同步选中态）
  const entryBtn = root.querySelector('[data-d-open-detail]');
  if (entryBtn) {
    entryBtn.onclick = () => actions.openDetail(d.id);
  }
  const vsc = root.querySelector('[data-d-open-vsc]');
  vsc.onclick = () => {
    const text = d.slug || d.id;
    if (navigator.clipboard) {
      navigator.clipboard.writeText(text).then(() => {
        vsc.textContent = '已复制 ✓';
        const t = window.setTimeout(() => { vsc.textContent = '复制路径'; }, 1400);
        timers.add(t);
      }).catch(() => {});
    }
  };
}

function closeDetail() {
  if (!root) return;
  const panel = root.querySelector('[data-detail-panel]');
  if (panel) panel.hidden = true;
  // v2.7：清聚焦态 + 清高亮 class + 回到当前层级全景（退出 L3 聚焦隔离）
  focusedNodeId = null;
  focusedTitle = '';
  if (cy) {
    cy.elements().removeClass('focused faded highlight hover-dim hover-bright');
    fitAllVisible();
  }
  actions.clearFocus();
  renderNav();
}

function rebuildFilters() {
  const box = root.querySelector('[data-kind-filters]');
  box.innerHTML = '';
  // L2 域子图：chip 计数显示域内 kind 数（避免显示全局 60 而域内仅 4 的误导）
  const counts = (viewMode === 'theme' && currentDomain)
    ? domainFilteredGraph(currentDomain).nodes.reduce((m, n) => { m[n.kind] = (m[n.kind] || 0) + 1; return m; }, {})
    : index.kindCounts;
  Object.keys(KIND_COLORS).forEach((k) => {
    if (!counts[k]) return;
    const chip = document.createElement('div');
    chip.className = 'kind-chip' + (activeKinds[k] ? '' : ' off');
    chip.setAttribute('role', 'checkbox');
    chip.setAttribute('aria-checked', String(!!activeKinds[k]));
    chip.setAttribute('tabindex', '0');
    chip.innerHTML = `<span class="kind-dot" style="background:${KIND_COLORS[k]};color:${KIND_COLORS[k]}"></span>`
      + `<span class="kind-label">${KIND_LABELS[k]}</span>`
      + `<span class="kind-count">${counts[k]}</span>`;
    const toggle = () => {
      activeKinds[k] = !activeKinds[k];
      chip.classList.toggle('off', !activeKinds[k]);
      chip.setAttribute('aria-checked', String(!!activeKinds[k]));
      store.setFilters({ kinds: { ...activeKinds } });
      refilter();
    };
    chip.addEventListener('click', toggle);
    chip.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
    });
    box.appendChild(chip);
  });
}

/**
 * 元素 diff：只 add/remove 变化部分，保留旧位置（性能演进⑥ #4）。
 * 原 refilter 全量 cy.elements().remove() + cy.add(全量) + runLayout()，每次切 kind/depth
 * 都重建全部元素并重跑 2500 iter 力导向布局。改为：
 *   1. remove：cy 当前有但 desired 没有的元素
 *   2. add：desired 有但 cy 没有的元素（含 compound 父节点，buildCompoundElements 已置前）
 *   3. sync：两边都有的节点，同步可能变化的 data（label / parent）
 * 顺序 remove → add → sync：先移除旧 compound 父节点，再 add 新父节点，最后 sync 子节点 parent
 * 指向已存在的父节点。
 *
 * [vision v2.6 核查] 边与节点同步移除依赖：
 *   - desired.edges 由 toElements（data/index.js）过滤"两端都可见"的边；
 *   - desiredData Map 同时登记 nodes + edges → toRemove 一次性移除被隐藏节点 + 其所有连边；
 *   - applyDepth（本文件）keptIds 同步过滤 keptEdges，depth 收敛也无悬空边。
 *   核查结论：无逻辑漏洞，vision 报告"30% 过滤同步"是 #1（视口外节点）的另一种视觉表现。
 * @param {{nodes: Array, edges: Array}} desired computeElements 输出
 * @returns {object} { addedNodes } 新增的叶子节点 collection（需就近放置）
 */
function applyElementDiff(desired) {
  const emptyResult = { addedNodes: null };
  if (!cy) return emptyResult;

  // 期望元素 id → data
  const desiredData = new Map();
  desired.nodes.forEach((n) => desiredData.set(n.data.id, n.data));
  desired.edges.forEach((e) => desiredData.set(e.data.id, e.data));

  // 1. 收集移除项（filter 返回 collection，cy.remove 需 collection 而非普通数组）
  const toRemove = cy.elements().filter((el) => !desiredData.has(el.id()));

  // 2. 收集新增项 / 同步项
  const toAdd = [];
  const toSync = [];
  desired.nodes.forEach((n) => {
    const el = cy.getElementById(n.data.id);
    if (el.empty()) {
      toAdd.push({ group: 'nodes', data: n.data });
    } else {
      toSync.push({ el, data: n.data });
    }
  });
  desired.edges.forEach((e) => {
    const el = cy.getElementById(e.data.id);
    if (el.empty()) toAdd.push({ group: 'edges', data: e.data });
    // edge data（color/lineStyle）基于 type 不变，不同步
  });

  // 3. 执行
  if (toRemove.length) cy.remove(toRemove);
  let addedNodes = null;
  if (toAdd.length) {
    const added = cy.add(toAdd);
    // 仅叶子节点需就近放置；compound 父节点位置由子节点自动决定
    addedNodes = added.nodes().filter((n) => !n.isParent());
  }
  toSync.forEach(({ el, data }) => syncElementData(el, data));

  return { addedNodes };
}

/**
 * 同步保留节点的可变 data（showLabels 切换 → label；compound 切换 → parent）。
 * 其他字段（id/kind/color/size 等）节点 id 固定时不变，跳过以减少 data 事件。
 */
function syncElementData(el, data) {
  if (el.data('label') !== data.label) el.data('label', data.label);
  const curParent = el.data('parent') || null;
  const newParent = data.parent || null;
  if (curParent !== newParent) {
    if (newParent) el.data('parent', newParent);
    else el.removeData('parent');
  }
}

/**
 * 给新增叶子节点就近放置位置（基于已存在邻居重心 + 小扰动），避免堆叠在原点。
 * 保留用户既有视图，不重跑全量力导向布局；需要时用户可点「重新布局」。
 */
function placeNewNodes(nodes) {
  if (!cy || !nodes || nodes.length === 0) return;
  nodes.forEach((n) => {
    const p = n.position();
    // 已有非原点位置则保留（cy 复用 / 重建场景）
    if (p && (p.x !== 0 || p.y !== 0)) return;
    const placed = n.neighborhood().nodes().filter((nn) => {
      const np = nn.position();
      return np && (np.x !== 0 || np.y !== 0);
    });
    if (placed.length) {
      let sx = 0, sy = 0;
      placed.forEach((nn) => { const np = nn.position(); sx += np.x; sy += np.y; });
      n.position({
        x: sx / placed.length + (Math.random() - 0.5) * 60,
        y: sy / placed.length + (Math.random() - 0.5) * 60,
      });
    } else {
      n.position({ x: (Math.random() - 0.5) * 300, y: (Math.random() - 0.5) * 300 });
    }
  });
}

function refilter() {
  if (!cy) return;
  // v2.7：L1 主题地图无节点筛选语义（领地是聚合），refilter 为 no-op
  if (viewMode === 'theme' && !currentDomain) return;
  const hideIso = root.querySelector('[data-hide-isolated]').checked;
  const showLabels = root.querySelector('[data-show-labels]').checked;
  store.setFilters({ hideIsolated: hideIso, showLabels, depth: depthValue });

  const desired = computeElements();
  // 性能（演进⑥ #4）：diff 增删替代全量 remove+add；新增节点就近放置，保留旧视图，
  // 不再自动重跑全量 fcose（numIter 2500）。结构无变化时为 no-op。
  const { addedNodes } = applyElementDiff(desired);
  if (addedNodes && addedNodes.length) {
    placeNewNodes(addedNodes);
    // v2.3：新增节点淡入（spring 缩放 + 淡入，非瞬蹦），refilter 切换更连贯
    playEntranceOn(addedNodes, false);
    // v2.6：新增节点可能扰动到视口外（placeNewNodes 60px 扰动），fit 全集确保新节点可见。
    // 仅 added 非空时 fit；remove only（关闭某 kind）不 fit，视图自然收缩更稳定。
    fitAllVisible();
  }
  // v2.8：物理模式下同步 d3-force 数据（增删节点/边），轻度 reheat 让新节点融入
  if (simulation) {
    syncSimulation();
  }
  updateStats(
    desired.nodes.filter((n) => !String(n.data.id).startsWith('compound-')).length,
    desired.edges.length,
  );
  // 可见集合变化 → 节点列表随之更新（键盘可达路径与画布同步）
  renderNodeList();
}

/**
 * [B3] 恢复 pinnedNodes 记录的位置（覆盖 layout 结果）。
 * 在 layout.run() 后、tween/fit 前调用，使被拖固定的节点不被重新布局移动。
 * tween 随后从 oldPos（含 pin 节点的拖动位置）插值到 current（恢复后同为拖动位置）= 无位移，自然跳过。
 */
function restorePinnedPositions() {
  if (!cy || !pinnedNodes.size) return;
  cy.batch(() => {
    pinnedNodes.forEach((pos, id) => {
      const n = cy.getElementById(id);
      if (n && n.length) n.position(pos);
    });
  });
}

/**
 * 运行布局。
 * v2.8：双路径——
 *   - 物理模式（默认，d3-force 可用且非 reduced-motion）：先 fcose 跑一次拿合理初始位置，
 *     再启动 d3-force 持续物理（节点斥力 / 边弹簧 / collide 实时排斥 / 拖拽 reheat）。
 *   - 静态模式（reduced-motion 或 d3-force 不可用）：保留原 fcose + tween 位置过渡。
 *
 * @param {{first?: boolean, l1?: boolean}} [opts] first=true 首次挂载（走入场动画，不做位置过渡）；
 *        l1=true 用 L1 领地图布局配置（~13 节点）；否则视为"重新布局"，
 *        节点从旧位置平滑过渡到新位置（v2.3，静态模式才用）。
 */
function runLayout(opts = {}) {
  if (!cy) return;
  stopLayout();
  const isFirst = !!opts.first;
  // L1 可显式传入，否则按当前层级自动判定（"重新布局"按钮在 L1 应跑领地布局）
  const isL1 = opts.l1 != null ? !!opts.l1 : (viewMode === 'theme' && !currentDomain);

  // 路径决策：物理 or 静态
  const usePhysics = !motionReduced && d3Ready && !!window.d3;

  // 第一步：fcose/cose 跑一次拿初始位置（两条路径都需要，物理模式用作 d3 初值）
  const primary = isL1 ? (fcoseReady ? L1_LAYOUT_FCOSE : L1_LAYOUT_COSE)
                       : (fcoseReady ? FCOSE_LAYOUT : COSE_LAYOUT);
  const fallback = isL1 ? L1_LAYOUT_COSE : COSE_LAYOUT;
  try {
    currentLayout = cy.layout(primary);
    currentLayout.run();
  } catch (err) {
    console.warn('[graph] 静态布局失败，降级 cose', err);
    fcoseReady = false;
    currentLayout = cy.layout(fallback);
    currentLayout.run();
  }

  if (usePhysics) {
    // === 物理路径 ===
    // 启动 d3-force 持续物理（fcose 位置作为初值，d3 接管）
    const started = startPhysics({ l1: isL1 });
    if (!started) {
      // 极端兜底：物理启动失败 → 静态 fit + 入场
      console.warn('[graph] d3-force 启动失败，回退静态 fit');
      restorePinnedPositions();
      const t = window.setTimeout(() => {
        fitAllVisible();
        if (isFirst) playEntrance();
      }, 80);
      timers.add(t);
      return;
    }
    // pinned 节点应用 fx/fy 锁定（不被物理重排）
    applyPinnedToSimulation();
    // 入场：物理路径不走位置 tween（d3-force 自己从 fcose 初值平滑演化）
    prepareEntrance();
    const t = window.setTimeout(() => {
      fitAllVisible();
      if (isFirst) playEntrance();
    }, 80);
    timers.add(t);
    // [vision v29 L1 修复] L1 物理收敛后补一次 fit（80ms 初始 fit 时 alpha≈0.9 太早，
    // 领地仍在演化；等 d3-force 冷到准稳态 alpha<0.06 再 animated fit，确保领地居中不漂边缘）
    if (isL1) scheduleL1ConvergeFit();
    return;
  }

  // === 静态路径（reduced-motion 或 d3-force 不可用）===
  // 重新布局：先 capture 旧位置，布局后 tween 过渡（非瞬移）
  const wantTween = !isFirst && !motionReduced;
  const oldPos = wantTween ? capturePositions() : null;
  // [B3] 布局完成后恢复 pinned 节点位置（覆盖 layout 结果），再 tween/fit
  restorePinnedPositions();
  // fcose/cose animate:false 同步完成 → 后置动画
  if (wantTween && oldPos && oldPos.size) {
    // 重新布局：节点平滑滑到新位置，完成后 fit（tween 内部收尾）
    tweenLayoutPositions(oldPos);
  } else {
    // 首次/降级/reduced：80ms 后 fit 主连通分量；首次额外播入场动画
    const t = window.setTimeout(() => {
      fitAllVisible();
      if (isFirst) playEntrance();
    }, 80);
    timers.add(t);
  }
}

function stopLayout() {
  if (currentLayout && typeof currentLayout.stop === 'function') {
    try { currentLayout.stop(); } catch (e) { /* layout 可能已结束 */ }
  }
  currentLayout = null;
}

function updateStats(nc, ec) {
  // 通过 store 驱动顶栏统计：app.js 订阅 store.stats 回写 DOM
  store.setStats({
    nodeCount: nc,
    edgeCount: ec,
    isolated: index ? index.isolatedCount : 0,
  });
}

function mountLegend() {
  const box = root.querySelector('[data-legend]');
  box.innerHTML = '';
  Object.keys(EDGE_COLORS).forEach((t) => {
    const row = document.createElement('div');
    row.className = 'legend-row';
    const st = EDGE_STYLE[t] || 'solid';
    row.innerHTML = `<span class="legend-line ${st}" style="border-color:${EDGE_COLORS[t]}"></span>`
      + `<span>${EDGE_LABELS[t] || t}</span>`;
    box.appendChild(row);
  });
}

// ===== 层级导航（左上角返回按钮 + 左面板模式切换）=====
// 取代原居中一长条 graph-nav：返回按钮浮于画布左上角标准位，label=返回目的地，简短不拉长；
// 模式切换（主题地图/全图）移入左面板顶部，常驻可见。
// 全部层级动作保留：L2→L1（enterThemeMap）/ L3→L2（closeDetail 退出聚焦）/ theme↔full（模式按钮）。
function renderNav() {
  if (!root) return;
  const backEl = root.querySelector('[data-graph-back]');

  // 返回按钮：仅在有上级层级时显示。label = 返回目的地（去哪），简短。
  //   L3 聚焦 → ← {域}域（退出聚焦，回 L2；无域则回主题地图）
  //   L2 域子图 → ← 主题地图（回 L1）
  //   L1 主题地图 / full 全图 → 隐藏（根级；当前位置由左面板模式按钮 active 态标示）
  let label = null;
  let action = null;
  if (viewMode === 'theme') {
    if (focusedNodeId) {
      label = currentDomain ? `${domainDisplayName(currentDomain)} 域` : '主题地图';
      action = closeDetail;
    } else if (currentDomain) {
      label = '主题地图';
      action = enterThemeMap;
    }
  }

  if (backEl) {
    backEl.innerHTML = '';
    if (label && action) {
      backEl.hidden = false;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'graph-back-btn';
      btn.setAttribute('aria-label', `返回${label}`);
      btn.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 18l-6-6 6-6"/></svg><span>${escapeHtml(label)}</span>`;
      btn.addEventListener('click', action);
      backEl.appendChild(btn);
    } else {
      backEl.hidden = true;
    }
  }

  // 左面板模式按钮 active 态（标示当前视图模式）
  root.querySelectorAll('[data-mode]').forEach((btn) => {
    const active = btn.dataset.mode === viewMode;
    btn.setAttribute('aria-pressed', String(active));
    btn.classList.toggle('active', active);
  });
}

/** 绑定左面板模式切换按钮（mount 时一次）。返回按钮在 renderNav 内即时绑定（随层级重建）。 */
function bindNav() {
  if (!root) return;
  root.querySelectorAll('[data-mode]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const m = btn.dataset.mode;
      if (m === 'theme') enterThemeMap();
      else if (m === 'full') enterFullGraph();
    });
  });
}

/**
 * 按层级切换左侧面板段可见性。
 * L1：显示领地列表段，隐藏节点筛选/深度/显示/图例/节点导航段。
 * L2/全图：反之。panel-actions（重新布局/适应视图）两层都保留。
 */
function updatePanelForLevel(isL1) {
  if (!root) return;
  root.querySelectorAll('[data-node-section]').forEach((el) => { el.hidden = isL1; });
  const l1Sec = root.querySelector('[data-l1-section]');
  if (l1Sec) l1Sec.hidden = !isL1;
  // L1 渲染领地列表；L2/全图节点列表由 refilter 负责渲染（含统计同步）
  if (isL1) renderDomainList();
}

/**
 * 渲染 L1 知识领地列表（键盘可达的钻取替代路径，与画布气泡同步）。
 * 每项含：领地色点 + 域名 + 节点数 + 平均权威度；点击/Enter 进入域子图。
 */
function renderDomainList() {
  if (!root) return;
  const box = root.querySelector('[data-domain-list]');
  const metaEl = root.querySelector('[data-domain-meta]');
  if (!box) return;
  if (!domainMap) domainMap = buildDomainMap(graph);
  box.innerHTML = '';
  const totalNodes = domainMap.reduce((s, d) => s + d.count, 0);
  domainMap.forEach((d) => {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'domain-list-item';
    item.setAttribute('role', 'listitem');
    if (d.domain === currentDomain) item.setAttribute('aria-current', 'true');
    const col = territoryColor(d.authNorm);
    // title 兜底：换行已让全名可见，title 再给一个 hover tooltip 通道（含节点数/权威度概览）
    item.title = `${domainDisplayName(d.domain)} · ${d.count} 节点 · 平均权威度 ${(d.authNorm * 100).toFixed(0)}%`;
    item.innerHTML =
      `<span class="dl-dot" style="background:${col};color:${col};box-shadow:0 0 ${4 + d.authNorm * 10}px ${col}" aria-hidden="true"></span>`
      + `<span class="dl-name">${escapeHtml(domainDisplayName(d.domain))}</span>`
      + `<span class="dl-count">${d.count}</span>`
      + `<span class="dl-auth" title="平均权威度">${(d.authNorm * 100).toFixed(0)}%</span>`;
    item.addEventListener('click', () => enterDomain(d.domain));
    box.appendChild(item);
  });
  if (metaEl) {
    metaEl.textContent = `${domainMap.length} 个领地 · 共 ${totalNodes} 节点 · 点击进入域子图`;
  }
}

// ===== hover 邻域高亮 =====
// detail 面板打开（存在 focused 节点）时禁用，避免与 tap 选中态视觉冲突；
// closeDetail 后自动恢复。class 用 hover-* 前缀，与 tap 的 focused/faded 独立。
function bindHover() {
  if (!cy) return;
  cy.on('mouseover', 'node', (evt) => {
    if (!cy) return;
    if (cy.$('.focused').nonempty()) return;            // tap 选中态进行中，不打断
    const n = evt.target;
    if (n.isParent()) return;                            // compound 父节点不触发
    cy.elements().removeClass('hover-dim hover-bright');
    const nb = n.closedNeighborhood();
    cy.elements().not(nb).addClass('hover-dim');
    nb.addClass('hover-bright');
    hoverSpringStart(n, 1.18);                           // v2.3：弹性放大（spring scale）
  });
  cy.on('mouseout', 'node', (evt) => {
    if (!cy) return;
    cy.elements().removeClass('hover-dim hover-bright');
    // v2.3：弹性收回（spring 回到 1.0）
    if (evt.target && !evt.target.isParent()) hoverSpringOut(evt.target);
  });
}

// ===== minimap（canvas 手绘，无额外依赖）=====
// 绘制：所有可见节点按 kind 色画点 + 当前视口框（樱粉描边）。
// 交互：mousedown/move 拖动 → 反算主图坐标 → cy.panTo 居中。
function mountMinimap() {
  const canvas = root.querySelector('[data-minimap]');
  if (!canvas || !cy) return;
  const dpr = window.devicePixelRatio || 1;
  minimapW = 200;
  minimapH = 130;
  canvas.width = minimapW * dpr;
  canvas.height = minimapH * dpr;
  canvas.style.width = minimapW + 'px';
  canvas.style.height = minimapH + 'px';
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  minimapCtx = ctx;

  // 视口/布局/元素变化时重绘（rAF 节流）
  cy.on('viewport layoutstop add remove', scheduleMinimapDraw);
  window.addEventListener('resize', scheduleMinimapDraw);

  bindMinimapDrag(canvas);
  scheduleMinimapDraw();
}

function scheduleMinimapDraw() {
  if (minimapRaf) cancelAnimationFrame(minimapRaf);
  minimapRaf = requestAnimationFrame(() => {
    minimapRaf = 0;
    drawMinimap();
  });
}

// 主图坐标 ↔ minimap 画布坐标的映射参数
function minimapTransform() {
  if (!cy) return null;
  const els = cy.elements(':visible');
  if (els.length === 0) return null;
  const bb = els.boundingBox();
  if (!bb || !bb.w || !bb.h) return null;
  const pad = 8;
  const scale = Math.min((minimapW - pad * 2) / bb.w, (minimapH - pad * 2) / bb.h);
  const ox = (minimapW - bb.w * scale) / 2 - bb.x1 * scale;
  const oy = (minimapH - bb.h * scale) / 2 - bb.y1 * scale;
  return { scale, ox, oy };
}

function drawMinimap() {
  if (!minimapCtx || !cy) return;
  const ctx = minimapCtx;
  const t = minimapTransform();
  // [v2.10] bb 无效（节点未定位 / 全重合在原点 / 无可见元素）时不清空，保留上一帧。
  // 原 clearRect 先行 + 此处 return 会留下空白画布——初始化/层级切换瞬间 bb 常为 0，
  // 用户看到"minimap 消失"。保留上一帧直到拿到有效 bb 再重绘，过渡平滑。
  if (!t) return;
  ctx.clearRect(0, 0, minimapW, minimapH);
  const { scale, ox, oy } = t;

  // 节点点（compound 父节点跳过，只画叶子）
  cy.nodes(':visible').forEach((n) => {
    if (n.isParent()) return;
    const p = n.position();
    ctx.fillStyle = n.data('color') || '#897989';
    ctx.beginPath();
    const nr = Math.max(1.8, (n.width() || 8) * scale * 0.5);
    ctx.arc(p.x * scale + ox, p.y * scale + oy, nr, 0, Math.PI * 2);
    ctx.fill();
  });

  // 视口框
  const ext = cy.extent();
  ctx.strokeStyle = '#e89bbb';
  ctx.lineWidth = 1;
  ctx.globalAlpha = 0.9;
  ctx.strokeRect(ext.x1 * scale + ox, ext.y1 * scale + oy, ext.w * scale, ext.h * scale);
  ctx.globalAlpha = 1;
}

function bindMinimapDrag(canvas) {
  let dragging = false;
  const panToClient = (clientX, clientY) => {
    if (!cy) return;
    const t = minimapTransform();
    if (!t) return;
    const rect = canvas.getBoundingClientRect();
    const mx = clientX - rect.left;
    const my = clientY - rect.top;
    const gx = (mx - t.ox) / t.scale;
    const gy = (my - t.oy) / t.scale;
    cy.panTo({ x: gx, y: gy });
  };
  canvas.addEventListener('mousedown', (e) => { dragging = true; panToClient(e.clientX, e.clientY); });
  canvas.addEventListener('mousemove', (e) => { if (dragging) panToClient(e.clientX, e.clientY); });
  canvas.addEventListener('mouseup', () => { dragging = false; });
  canvas.addEventListener('mouseleave', () => { dragging = false; });
}

function destroyMinimap() {
  if (minimapRaf) { cancelAnimationFrame(minimapRaf); minimapRaf = 0; }
  // cy 事件随 cy.destroy 清除；window resize 需手动解绑
  window.removeEventListener('resize', scheduleMinimapDraw);
  minimapCtx = null;
}

function bindControls() {
  root.querySelector('[data-hide-isolated]').addEventListener('change', refilter);
  root.querySelector('[data-show-labels]').addEventListener('change', refilter);
  root.querySelector('[data-depth-slider]').addEventListener('input', (e) => {
    depthValue = Number(e.target.value) || 0;
    const valEl = root.querySelector('[data-depth-val]');
    if (valEl) {
      valEl.textContent = depthValue === 0 ? '0 · 全部' : depthValue + ' · 度数≥' + depthValue;
    }
    // 性能（演进⑥ #3）：input 事件拖动过程高频触发，label 即时更新给用户反馈，
    // refilter 防抖 200ms，停顿后才执行一次 diff（避免拖动过程持续重建）。
    if (depthDebounce) window.clearTimeout(depthDebounce);
    depthDebounce = window.setTimeout(() => {
      depthDebounce = null;
      refilter();
    }, 200);
  });
  root.querySelector('[data-relayout]').addEventListener('click', () => {
    // [B3] 重新布局保留用户 pin 的节点（fx/fy 语义：刻意钉住的不动），仅重排未 pin 节点。
    //     释放 pin 靠切换层级（enterLevel → destroyCy 清空 pinnedNodes）。
    runLayout();
  });
  root.querySelector('[data-fit-view]').addEventListener('click', () => fitAllVisible());
  root.querySelector('[data-d-close]').addEventListener('click', closeDetail);
  // v2.7：退出聚焦 + 模式切换收敛到导航栏（bindNav）
  bindNav();
  // 节点列表搜索框
  bindNodeListSearch();
}

/**
 * 挂载图谱视图。
 * @param {HTMLElement} container 视图根容器（#view-root 或其子节点）
 */
export function mount(container) {
  root = container;
  root.innerHTML = GRAPH_HTML;
  root.classList.add('view-graph');
  const session = ++mountSession;

  // v2.3：读一次 prefers-reduced-motion，所有动效据此归零（与 starfield/edgeFlow/pulse 口径一致）
  // v2.7：try/catch 防御——matchMedia 在部分沙箱/旧环境会抛 Illegal invocation，
  //      不应让整个视图崩溃；失败时按「无偏好」处理（动效正常，保守不静默）。
  try {
    motionReduced = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  } catch (e) {
    motionReduced = false;
  }

  graph = loadGraph();
  if (!graph || !graph.nodes.length) {
    showError();
    console.error('[graph] 数据未加载', graph);
    return;
  }

  index = buildIndex(graph);

  // v2.7：默认入口 = L1 主题地图（层次钻取替代全量毛球）
  viewMode = 'theme';
  currentDomain = null;
  focusedNodeId = '';
  focusedTitle = '';
  domainMap = buildDomainMap(graph);

  // 从 store 恢复筛选态（首次进入用默认全开；仅 L2/全图生效，L1 无节点筛选）
  const saved = store.getState().filters;
  activeKinds = Object.fromEntries(
    Object.keys(KIND_COLORS).map((k) => [k, saved.kinds && saved.kinds[k] !== undefined ? saved.kinds[k] : true]),
  );
  if (saved.hideIsolated !== undefined) {
    const el = root.querySelector('[data-hide-isolated]');
    if (el) el.checked = saved.hideIsolated;
  }
  if (saved.showLabels !== undefined) {
    const el = root.querySelector('[data-show-labels]');
    if (el) el.checked = saved.showLabels;
  }
  if (typeof saved.depth === 'number') {
    depthValue = saved.depth;
    const el = root.querySelector('[data-depth-slider]');
    if (el) el.value = String(depthValue);
    const valEl = root.querySelector('[data-depth-val]');
    if (valEl) valEl.textContent = depthValue === 0 ? '0 · 全部' : depthValue + ' · 度数≥' + depthValue;
  }
  // [v2.4 减负] compound 分组已默认关闭且移除 UI 开关，不再从 store 恢复
  rebuildFilters();
  mountLegend();
  bindControls();

  // 异步加载 fcose（失败自动降级 cose），就绪后按层级挂载（默认 L1）
  // v2.8：并行加载 d3-force（CDN <script> 通常已就绪，loadD3Force 仅作兜底检测）
  hideLoading();
  Promise.all([loadFcose(), loadD3Force()]).then(() => {
    if (session !== mountSession || !root) return; // 已 unmount 或被新 mount 取代
    enterLevel();
  });
}

export function unmount() {
  timers.forEach((t) => window.clearTimeout(t));
  timers.clear();
  if (depthDebounce) { window.clearTimeout(depthDebounce); depthDebounce = null; }
  destroyCy();
  if (root) { root.innerHTML = ''; root.classList.remove('view-graph'); }
  root = null;
  graph = null;
  index = null;
  domainMap = null;
  // fcose 已注册到全局 cytoscape，不重置 fcoseReady（切回视图时复用）
}
