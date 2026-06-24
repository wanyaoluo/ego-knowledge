// router.js · hash 路由
// #/graph · #/search · #/detail/:id · #/dashboard · #/structure-map · #/gallery · #/reader · #/timeline · #/review · #/explore · #/promotion-river
// 不引入框架，监听 hashchange；视图切换由 app.js 编排。

const ROUTES = [
  { view: 'graph', pattern: /^#\/graph\/?$/ },
  { view: 'search', pattern: /^#\/search\/?$/ },
  { view: 'detail', pattern: /^#\/detail(?:\/(.+?))?\/?$/, paramKeys: ['id'] },
  { view: 'dashboard', pattern: /^#\/dashboard\/?$/ },
  { view: 'structure-map', pattern: /^#\/structure-map\/?$/ },
  { view: 'gallery', pattern: /^#\/gallery\/?$/ },
  { view: 'reader', pattern: /^#\/reader\/?$/ },
  { view: 'timeline', pattern: /^#\/timeline\/?$/ },
  { view: 'review', pattern: /^#\/review\/?$/ },
  { view: 'explore', pattern: /^#\/explore\/?$/ },
  { view: 'promotion-river', pattern: /^#\/promotion-river\/?$/ },
];

const DEFAULT_HASH = '#/dashboard';
const listeners = new Set();

/**
 * 解析当前 hash → { view, params } | null
 * 未知 hash 视为 dashboard（兜底，避免空白屏）。
 */
export function parse() {
  const hash = location.hash || DEFAULT_HASH;
  for (const route of ROUTES) {
    const m = hash.match(route.pattern);
    if (m) {
      const params = {};
      (route.paramKeys || []).forEach((k, i) => {
        const v = m[i + 1];
        if (v !== undefined) params[k] = decodeURIComponent(v);
      });
      return { view: route.view, params };
    }
  }
  return { view: 'dashboard', params: {} };
}

/** 修改 hash 触发切换；不重复 push 相同 hash */
export function navigate(path) {
  if (!path) return;
  const target = path.startsWith('#') ? path : `#${path}`;
  if (location.hash === target) {
    // 同路径也触发一次（详情页内部跳同 view 不同 id 时需要）
    notify();
  } else {
    location.hash = target;
  }
}

export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function notify() {
  const parsed = parse();
  listeners.forEach((fn) => {
    try { fn(parsed); } catch (err) { console.error('[router] listener error', err); }
  });
}

// 初始化：未指定 hash 时填默认值
if (!location.hash) {
  location.hash = DEFAULT_HASH;
}
window.addEventListener('hashchange', notify);
