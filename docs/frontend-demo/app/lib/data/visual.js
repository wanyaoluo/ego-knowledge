// lib/data/visual.js · 视觉映射真源
// spec：concept 紫 / dossier 琥珀 / note 浅紫 / source 灰紫
// 集中 kind / edge / status / freshness 的颜色与中文标签，以及 authority 尺寸映射。
// graph / detail / search / dashboard 直接 import；数据层（index/search/stats）不反向依赖展示语义。

// ===== kind 视觉 =====
export const KIND_COLORS = {
  concept: '#b59ee6',
  dossier: '#e6b885',
  note: '#c9b8e8',
  source: '#6b5d7a',
  decision: '#8ea786',
  view: '#897989',
};
export const KIND_LABELS = {
  concept: '概念',
  dossier: '档案',
  note: '笔记',
  source: '来源',
  decision: '决策',
  view: '视图',
};

// 8 类关系 → 颜色 + 线型（实/虚/点）+ 中文标签
// source_refs 边提亮：原 #6b5d7a × 全局 opacity 0.55 在深底上仅 2.26:1（<3 不可见），
// 提至 #a594b8（灰紫，保持 source 语义）后 ×0.55 = 4.38:1，达 UI 图形 AA（≥3:1）。
// 仅提亮边，不影响 KIND_COLORS.source 节点色（节点作为大字 UI 组件已达标）。
export const EDGE_COLORS = {
  related: '#b59ee6',
  derived_from: '#e6b885',
  evidence_refs: '#9d86c9',
  source_refs: '#a594b8',
  evidence_for: '#96c48e',
  part_of: '#897989',
  depends_on: '#e89bbb',
  applied_in: '#8ea786',
};
export const EDGE_STYLE = {
  related: 'solid',
  derived_from: 'solid',
  evidence_refs: 'dashed',
  source_refs: 'dotted',
  evidence_for: 'solid',
  part_of: 'solid',
  depends_on: 'solid',
  applied_in: 'solid',
};
export const EDGE_LABELS = {
  related: '相关',
  derived_from: '派生自',
  evidence_refs: '证据',
  source_refs: '来源',
  evidence_for: '佐证',
  part_of: '部分',
  depends_on: '依赖',
  applied_in: '应用于',
};

export const STATUS_LABELS = {
  draft: '草稿',
  active: '活跃',
  authoritative: '权威',
  legacy: '遗留',
  deprecated: '弃用',
  archived: '归档',
};
export const FRESHNESS_LABELS = {
  stable: '稳定',
  watch: '观察',
  volatile: '易变',
};
// freshness 视觉色（timeline 时间代理排序 / review 紧迫度 共用真源）
// 呼应 tokens 脉冲语义：volatile 樱粉躁动 / watch 紫观察 / stable 沉绿沉淀
export const FRESHNESS_COLORS = {
  volatile: '#e89bbb',
  watch: '#b59ee6',
  stable: '#8ea786',
};

// authority 区间（从样本数据计算）→ 用于节点大小归一化
export const AUTH_MIN = 0.00267;
export const AUTH_MAX = 0.04889;

/** authority 数值 → 节点像素大小（黑曜石克制：8–18px，sqrt 抑制大节点不占视野）
 *  原线性 26–58px 导致 hub 节点撑满视野；改 sqrt 曲线后高 authority 增长平缓，
 *  默认叶子 8px、hub 上限 18px，呼应 Obsidian「节点小而克制」。 */
export function authSize(a) {
  const t = Math.max(0, Math.min(1, (a - AUTH_MIN) / (AUTH_MAX - AUTH_MIN)));
  return 8 + 10 * Math.sqrt(t);
}
/** authority 数值 → 进度条百分比（3–100） */
export function authPct(a) {
  return Math.max(3, Math.min(100, ((a - AUTH_MIN) / (AUTH_MAX - AUTH_MIN)) * 100));
}
/**
 * authority 数值 → 归一化 0–1（雷达图等连续维度用，无最低兜底）。
 * @param {number} a
 * @returns {number} 0–1
 */
export function authNorm(a) {
  return Math.max(0, Math.min(1, (a - AUTH_MIN) / (AUTH_MAX - AUTH_MIN)));
}
// [v2.5 克制] 移除 authStarLevel / authStarGlow（authority 星等 glow padding 映射）：
//   黑曜石调研证实节点本身无 glow，starGlow 驱动的 underlay 外发光已从图谱删除，此处为死代码。
/** 长字符串截断 */
export function trunc(s, n = 10) {
  if (!s) return '';
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}
