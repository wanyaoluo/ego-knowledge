// lib/escape.js · HTML 转义单一真源
// 职责：所有 innerHTML 拼接处的用户内容字段统一过此函数，防止 DOM 结构破坏与脚本注入。
// 历史：原 detail/search/dashboard 三视图各自定义且实现不一致（search 漏单引号、dashboard 命名漂移且无 null 防御），
// 现上抽为单一真源；graph 视图 W1 一并接入。

/**
 * HTML 字符转义：转义 `& < > ' "` 共 5 个字符；null/undefined 安全返回空串。
 * @param {*} s 任意值（非 string 会先经 String()）
 * @returns {string} 转义后的安全字符串
 */
export function escapeHtml(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
