// lib/data/index.js · 数据加载 + 索引派生
// 职责：从 window.__EK_GRAPH 读取并校验数据 → 构建邻接/统计索引 → 派生 cytoscape 元素 / 节点查询。
// 视觉映射（颜色 / 尺寸）由 ./visual.js 提供，本模块只负责数据访问与派生，不承载展示语义。

import {
  KIND_COLORS, EDGE_COLORS, EDGE_STYLE, authSize, trunc,
} from './visual.js';

/**
 * 从 window.__EK_GRAPH 读取并校验数据。
 * 失败时返回 null 并打印错误（失败路径显式）。
 * @returns {{nodes: Array, edges: Array} | null}
 */
export function loadGraph() {
  const raw = window.__EK_GRAPH;
  if (!raw || !Array.isArray(raw.nodes) || !Array.isArray(raw.edges)) {
    console.error('[data-layer] window.__EK_GRAPH 缺失或结构不合法', raw);
    return null;
  }
  return { nodes: raw.nodes, edges: raw.edges };
}

/**
 * 构建派生索引：边邻接表 / kind 计数 / 孤立节点集合 / 视图统计。
 * @param {{nodes: Array, edges: Array}} graph
 */
export function buildIndex(graph) {
  const edgeIndex = new Map();   // nodeId → Edge[]
  const degreeIndex = new Map(); // nodeId → degree
  const kindCounts = {};
  const statusCounts = {};
  const freshnessCounts = {};
  const domainCounts = {};
  let maxAuth = -Infinity;
  let minAuth = Infinity;
  let authSum = 0;

  Object.keys(KIND_COLORS).forEach((k) => { kindCounts[k] = 0; });

  graph.edges.forEach((e) => {
    [e.source, e.target].forEach((id) => {
      if (!edgeIndex.has(id)) edgeIndex.set(id, []);
      edgeIndex.get(id).push(e);
      degreeIndex.set(id, (degreeIndex.get(id) || 0) + 1);
    });
  });

  graph.nodes.forEach((n) => {
    if (kindCounts[n.kind] !== undefined) kindCounts[n.kind]++;
    statusCounts[n.status] = (statusCounts[n.status] || 0) + 1;
    freshnessCounts[n.freshness] = (freshnessCounts[n.freshness] || 0) + 1;
    const domain = n.domain || '_unsorted';
    domainCounts[domain] = (domainCounts[domain] || 0) + 1;
    const a = Number(n.authority) || 0;
    if (a > maxAuth) maxAuth = a;
    if (a < minAuth) minAuth = a;
    authSum += a;
  });

  const connectedIds = new Set(degreeIndex.keys());
  const isolated = graph.nodes.filter((n) => !connectedIds.has(n.id)).map((n) => n.id);

  return {
    edgeIndex,
    degreeIndex,
    kindCounts,
    statusCounts,
    freshnessCounts,
    domainCounts,
    isolatedCount: isolated.length,
    isolatedIds: isolated,
    authRange: { min: minAuth, max: maxAuth, avg: graph.nodes.length ? authSum / graph.nodes.length : 0 },
  };
}

/**
 * 派生 cytoscape 元素：按 kind/孤立/标签筛选。
 * @param {{nodes: Array, edges: Array}} graph
 * @param {{kinds: Object<string,boolean>, hideIsolated: boolean, showLabels: boolean}} opts
 * @param {Map<string, Array>} [edgeIndex] 来自 buildIndex，避免重复计算
 */
export function toElements(graph, opts, edgeIndex) {
  const kinds = opts.kinds;
  const hideIso = !!opts.hideIsolated;
  const showLabels = !!opts.showLabels;
  const idx = edgeIndex || buildIndex(graph).edgeIndex;

  const nodes = graph.nodes.filter((n) => {
    if (!kinds[n.kind]) return false;
    if (hideIso && (!idx.has(n.id) || idx.get(n.id).length === 0)) return false;
    return true;
  });
  const visibleIds = new Set(nodes.map((n) => n.id));
  const edges = graph.edges.filter((e) => visibleIds.has(e.source) && visibleIds.has(e.target));

  return {
    nodes: nodes.map((n) => ({
      data: {
        id: n.id,
        label: showLabels ? trunc(n.title) : '',
        fullTitle: n.title,
        kind: n.kind,
        domain: n.domain,
        status: n.status,
        freshness: n.freshness,
        authority: n.authority,
        slug: n.slug,
        color: KIND_COLORS[n.kind] || '#897989',
        size: authSize(Number(n.authority) || 0),
      },
    })),
    edges: edges.map((e) => ({
      data: {
        id: `${e.source}->${e.target}:${e.type}`,
        source: e.source,
        target: e.target,
        type: e.type,
        color: EDGE_COLORS[e.type] || '#897989',
        lineStyle: EDGE_STYLE[e.type] || 'solid',
      },
    })),
  };
}

/** 按 id 取节点 */
export function getNodeById(graph, id) {
  return graph.nodes.find((n) => n.id === id) || null;
}
