// lib/data/search.js · 搜索索引与执行
// 当前为骨架：title/slug/id 前缀/子串匹配占位，按 authority 排序。
// 5 路 backend（exact/bm25/graph/dense/fusion）在 views/search 前端模拟，不在此处。
//
// 性能（演进⑥ #1/#2）：
//   - buildSearchIndex 模块级单例缓存：以 graph.nodes 引用为 key，数据不变则复用，
//     避免每次搜索 / 视图切换重复构建（loadGraph 外层对象每次新建，但 nodes 数组
//     引用稳定指向 window.__EK_GRAPH.nodes，适合作为缓存 key）。
//   - 预计算每节点 dense 路用 bigrams（title+slug），存入索引项，dense backend 直接读，
//     不再每次搜索对全量节点重算 Jaccard 输入。

// ===== 模块级单例缓存 =====
// key = graph.nodes 引用；index = 派生索引数组。graph 更换（热更新数据）时自动失效重建。
let _cacheKey = null;
let _cacheIndex = null;

/** bigrams（dense 语义相似预计算用；2-gram 集合，内部函数不导出） */
function _bigrams(s) {
  const set = new Set();
  const str = (s || '').toLowerCase().replace(/\s+/g, '');
  for (let i = 0; i < str.length - 1; i++) set.add(str.slice(i, i + 2));
  return set;
}

/**
 * 构建搜索索引：title/slug/id 前缀匹配占位 + dense 路 bigrams 预计算。
 * 单例缓存：graph.nodes 引用不变则返回同一份索引，避免反复构建。
 * @param {{nodes: Array, edges: Array}} graph
 * @returns {Array<{id,title,slug,kind,domain,status,freshness,authority,haystack,bigrams}>}
 */
export function buildSearchIndex(graph) {
  const key = graph && graph.nodes;
  if (key && key === _cacheKey && _cacheIndex) return _cacheIndex;
  if (!Array.isArray(key)) return [];
  _cacheIndex = graph.nodes.map((n) => ({
    id: n.id,
    title: n.title,
    slug: n.slug || '',
    kind: n.kind,
    domain: n.domain,
    status: n.status,
    freshness: n.freshness,
    authority: Number(n.authority) || 0,
    haystack: `${n.title} ${n.slug || ''} ${n.kind} ${n.domain || ''}`.toLowerCase(),
    // 预计算 dense 路用 bigrams（title+slug），与原 views/search dense 实现等价
    bigrams: _bigrams(`${n.title} ${n.slug || ''}`),
  }));
  _cacheKey = key;
  return _cacheIndex;
}

/** 简单前缀/子串搜索（占位实现） */
export function search(index, query, limit = 50) {
  const q = (query || '').trim().toLowerCase();
  if (!q) return [];
  return index
    .filter((item) => item.haystack.includes(q))
    .sort((a, b) => b.authority - a.authority)
    .slice(0, limit);
}
