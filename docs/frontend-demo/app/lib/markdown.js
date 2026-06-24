// lib/markdown.js · markdown 解析单一真源
// 职责：简易 markdown → HTML（零依赖同步）+ marked CDN 动态加载（渐进增强）。
// 历史：原 detail 视图内部实现 simpleMarkdownToHtml / loadMarked，reader 视图（Phase 3）
//       需复用同款解析；上抽为单一真源，避免两份实现漂移。detail 改 import 本模块，逻辑零变化。
// 安全：simpleMarkdownToHtml 先 HTML escape 再做替换，防注入。

import { escapeHtml } from './escape.js';

const MARKED_URL = 'https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js';

// marked 加载 Promise（模块级去重，已加载则复用；失败清空允许下次重试）
let markedLoading = null;

/**
 * 动态加载 marked。失败不 reject，返回 false 走 fallback。
 * marked 12 默认配置（GFM + 不内嵌 dangerously）。
 * @returns {Promise<boolean>} true = window.marked.parse 可用
 */
export function loadMarked() {
  if (window.marked && typeof window.marked.parse === 'function') return Promise.resolve(true);
  if (markedLoading) return markedLoading;
  markedLoading = new Promise((resolve) => {
    const s = document.createElement('script');
    s.src = MARKED_URL;
    s.crossOrigin = 'anonymous';
    s.referrerPolicy = 'no-referrer';
    s.onload = () => {
      try {
        if (window.marked && typeof window.marked.setOptions === 'function') {
          window.marked.setOptions({ gfm: true, breaks: false });
        }
        resolve(!!(window.marked && typeof window.marked.parse === 'function'));
      } catch (e) {
        console.warn('[markdown] marked 配置失败', e);
        resolve(false);
      }
    };
    s.onerror = () => { console.warn('[markdown] marked 加载失败，使用简易解析'); resolve(false); };
    document.head.appendChild(s);
  }).then((ok) => ok, () => false).finally(() => { markedLoading = null; });
  return markedLoading;
}

/**
 * 简易 markdown → HTML（fallback，零依赖）。
 * 支持：h1-h6 / 段落 / 无序列表 / 加粗 / 行内 code / 代码块。
 * 安全：先 escape HTML 再做替换，避免 XSS。
 * @param {string} md
 * @returns {string}
 */
export function simpleMarkdownToHtml(md) {
  if (!md) return '';
  let src = escapeHtml(md).replace(/\r\n/g, '\n');
  const lines = src.split('\n');
  const out = [];
  let inList = false;
  let inCode = false;
  let codeBuf = [];
  let paraBuf = [];

  const flushPara = () => {
    if (paraBuf.length) {
      out.push('<p>' + paraBuf.join(' ') + '</p>');
      paraBuf = [];
    }
  };
  const closeList = () => {
    if (inList) { out.push('</ul>'); inList = false; }
  };
  const inlineFmt = (s) => s
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.trim().startsWith('```')) {
      if (inCode) {
        out.push('<pre><code>' + codeBuf.join('\n') + '</code></pre>');
        codeBuf = [];
        inCode = false;
      } else {
        flushPara();
        closeList();
        inCode = true;
      }
      continue;
    }
    if (inCode) { codeBuf.push(line); continue; }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      flushPara();
      closeList();
      const lv = h[1].length;
      out.push('<h' + lv + '>' + inlineFmt(h[2]) + '</h' + lv + '>');
      continue;
    }
    const li = line.match(/^[-*]\s+(.*)$/);
    if (li) {
      flushPara();
      if (!inList) { out.push('<ul>'); inList = true; }
      out.push('<li>' + inlineFmt(li[1]) + '</li>');
      continue;
    }
    if (line.trim() === '') {
      flushPara();
      closeList();
      continue;
    }
    closeList();
    paraBuf.push(inlineFmt(line));
  }
  flushPara();
  closeList();
  if (inCode) out.push('<pre><code>' + codeBuf.join('\n') + '</code></pre>');
  return out.join('\n');
}
