// lib/data-layer.js · 向后兼容 barrel（re-export ./data/ 子目录）
// P0 拆分（架构 review 2026-06-22）：原单文件 ~263 行按职责拆到 ./data/ 下 4 个模块：
//   - data/visual.js  视觉映射真源（kind/edge/status/freshness 颜色+标签 + authSize/authPct/trunc）
//   - data/index.js   数据加载 + 索引派生（loadGraph/buildIndex/toElements/getNodeById）
//   - data/search.js  搜索索引与执行（buildSearchIndex/search）
//   - data/stats.js   统计聚合（getStats）
// 本文件仅做 re-export，4 视图（graph/search/detail/dashboard）现有 import 路径无需修改。
// 后续视图迁移到 ./data/* 直连后，本 barrel 可移除。

export * from './data/visual.js';
export * from './data/index.js';
export * from './data/search.js';
export * from './data/stats.js';
