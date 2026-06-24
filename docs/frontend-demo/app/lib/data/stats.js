// lib/data/stats.js · 仪表盘统计聚合
// 输入：graph + buildIndex 产出的 index；输出：dashboard 消费的聚合结构。
// 纯只读派生，不修改 graph 与 index。

/**
 * 仪表盘统计聚合（Phase 2 ek-dashboard 消费）。
 * 本轮骨架，返回完整结构。
 */
export function getStats(graph, index) {
  const topDomains = Object.entries(index.domainCounts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8)
    .map(([domain, count]) => ({ domain, count }));
  const topAuthority = [...graph.nodes]
    .sort((a, b) => (Number(b.authority) || 0) - (Number(a.authority) || 0))
    .slice(0, 10)
    .map((n) => ({ id: n.id, title: n.title, kind: n.kind, authority: Number(n.authority) || 0 }));

  return {
    total: graph.nodes.length,
    edges: graph.edges.length,
    isolated: index.isolatedCount,
    kindCounts: { ...index.kindCounts },
    statusCounts: { ...index.statusCounts },
    freshnessCounts: { ...index.freshnessCounts },
    topDomains,
    topAuthority,
    authRange: index.authRange,
  };
}
