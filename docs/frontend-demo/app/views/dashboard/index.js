// views/dashboard/index.js · 仪表盘（Phase 2 · ek-dashboard · v2.3 图表重做）
// Bento 卡片网格：总条目 / 知识构成光谱 / 成熟度光谱 / 鲜活度脉搏 / 领域 / 孤立 / 权威 / 健康体检
// v2.3 信息设计：传统图表 → 风格化叙事可视化（与 cyber-pink 紫琥珀发光统一）。
// 纯 CSS + SVG，无图表库依赖。CSS 在 index.html 用 <link> 引入（无 build 环境不支持 import css）。
import {
  KIND_COLORS, KIND_LABELS, STATUS_LABELS, FRESHNESS_LABELS,
  loadGraph, buildIndex, getStats, authPct,
} from '../../lib/data-layer.js';
import { escapeHtml } from '../../lib/escape.js';

// ===== 本地视觉常量（dashboard 专属，避免污染 data-layer） =====
// 成熟度光谱色阶（升格叙事：draft 琥珀 → active 紫 → authoritative 沉绿）
// legacy/deprecated/archived 为"退役尾段"，灰调弱化
const MATURITY_COLORS = {
  draft: 'var(--accent-tertiary)',
  active: 'var(--accent-secondary)',
  authoritative: 'var(--accent-positive)',
  legacy: 'var(--text-tertiary)',
  deprecated: 'var(--accent-danger)',
  archived: 'var(--text-tertiary)',
};
// 升格正向主段（草稿 → 活跃 → 权威），退役尾段单独弱化
const MATURITY_MAIN = ['draft', 'active', 'authoritative'];
const MATURITY_DECAY = ['legacy', 'deprecated', 'archived'];

// 鲜活度脉搏色（volatile 红 / watch 琥珀 / stable 沉绿）
const FRESH_COLORS = {
  stable: 'var(--accent-positive)',
  watch: 'var(--accent-tertiary)',
  volatile: 'var(--accent-danger)',
};
const FRESH_ORDER = ['stable', 'watch', 'volatile'];

// 脉搏波形参数（对齐 tokens.css --pulse-* 真源）
// freq 越高越躁、amp 越大起伏越明显、opacity 控制呼吸基线
const PULSE_PARAMS = {
  stable: { freq: 0.5, amp: 0.10, opacity: 0.55 },   // 沉静 · 低频微伏
  watch: { freq: 1.1, amp: 0.22, opacity: 0.50 },     // 中频观察
  volatile: { freq: 3.0, amp: 0.42, opacity: 0.38 },  // 高频躁动
};

// ===== 工具 =====
const pct = (n, total) => (!total ? '0.0' : ((n / total) * 100).toFixed(1));
const domainLabel = (d) => (!d || d === '_unsorted' || d === '_none' ? '未分类' : d.replace(/-/g, ' '));
const formatShare = (a) => (a * 100).toFixed(2) + '%';
const clamp01 = (v) => Math.max(0, Math.min(1, v));

// ===== 一句话诊断（让数字变成"洞察"）=====

/** kind 构成诊断：主导型 / 三分天下 */
function kindInsight(kindEntries, total) {
  if (!kindEntries.length || !total) return '暂无数据';
  const sorted = [...kindEntries].sort((a, b) => b[1] - a[1]);
  const [topK, topV] = sorted[0];
  const topLabel = KIND_LABELS[topK] || topK;
  const topPct = pct(topV, total);
  if (parseFloat(topPct) >= 50) {
    return `${topLabel}占 ${topPct}% · 是知识库主体，结构向其倾斜`;
  }
  const top3 = sorted.slice(0, 3).map(([k]) => KIND_LABELS[k] || k).join('·');
  return `${top3} 三分天下，${topLabel}（${topPct}%）略占主导`;
}

/** 成熟度诊断：已沉淀 / 沉淀中 / 待推进 */
function maturityInsight(s, total) {
  if (!total) return '暂无数据';
  const authN = s.statusCounts.authoritative || 0;
  const activeN = s.statusCounts.active || 0;
  const draftN = s.statusCounts.draft || 0;
  const mature = authN + activeN;
  const maturePct = pct(mature, total);
  const draftPct = pct(draftN, total);
  if (parseFloat(maturePct) >= 70) {
    return `权威+活跃占 ${maturePct}% · 已深度沉淀（草稿仅 ${draftPct}%）`;
  }
  if (parseFloat(maturePct) >= 40) {
    return `权威+活跃占 ${maturePct}% · 沉淀中（草稿 ${draftPct}% 待推进）`;
  }
  return `成熟内容仅 ${maturePct}% · 草稿 ${draftPct}% 偏高，需推进沉淀`;
}

/** 鲜活度诊断：躁动 / 沉静 / 待激活 */
function freshnessInsight(s, total) {
  if (!total) return '暂无数据';
  const stable = s.freshnessCounts.stable || 0;
  const watch = s.freshnessCounts.watch || 0;
  const volatile = s.freshnessCounts.volatile || 0;
  const stablePct = pct(stable, total);
  const volPct = pct(volatile, total);
  if (parseFloat(volPct) >= 20) {
    return `${volPct}% 易变内容躁动 · 需关注衰变风险`;
  }
  if (parseFloat(stablePct) >= 60) {
    return `${stablePct}% 已沉静 · 知识库鲜活度健康`;
  }
  return `观察态占 ${pct(watch, total)}% · 鲜活度待激活`;
}

/** 体检总评（综合 4 项 grade 给"健康/良好/需关注/警示"） */
function healthVerdict(report) {
  const gradeOrder = { A: 4, B: 3, C: 2, D: 1 };
  const avg = report.reduce((sum, r) => sum + (gradeOrder[r.grade] || 0), 0) / (report.length || 1);
  if (avg >= 3.5) return { label: '健康', tone: 'a' };
  if (avg >= 2.5) return { label: '良好', tone: 'b' };
  if (avg >= 1.5) return { label: '需关注', tone: 'c' };
  return { label: '警示', tone: 'd' };
}

/** 中枢健康度（concentration 在 [0.08, 0.30] 健康带满分，偏离扣分） */
function hubHealthScore(concentration) {
  if (concentration < 0.08) return concentration / 0.08; // 太散
  if (concentration > 0.30) return Math.max(0, 1 - (concentration - 0.30) / 0.30); // 太集中
  return 1; // 健康带内
}

// ===== 风格化可视化（纯 CSS + SVG）=====

/**
 * 知识构成光谱条带（替代 donut）：横向堆叠，每段一种 kind 色 + 发光。
 * 比 donut 信息密度高、易于加发光、与现代仪表盘风格统一。
 */
function spectrumBandHTML(kindEntries, total) {
  if (!total) return '<div class="spec-empty">无数据</div>';
  const segs = kindEntries.map(([k, v]) => {
    const w = ((v / total) * 100).toFixed(2);
    const color = KIND_COLORS[k] || 'var(--kind-view)';
    const label = KIND_LABELS[k] || k;
    return `<span class="spec-seg" style="flex:0 0 ${w}%;background:${color}" title="${escapeHtml(label)} · ${v}（${pct(v, total)}%）"></span>`;
  }).join('');
  return `<div class="spectrum-band" role="img" aria-label="知识构成光谱">${segs}</div>`;
}

/**
 * 成熟度光谱（替代柱图）：draft→active→authoritative 升格渐变叙事 + 退役尾段。
 * 把"状态分布"转译为"成熟度进度"，一眼看出知识库沉淀程度。
 */
function maturitySpectrumHTML(s, total) {
  if (!total) return '<div class="spec-empty">无数据</div>';
  const mainSegs = MATURITY_MAIN
    .filter((k) => (s.statusCounts[k] || 0) > 0)
    .map((k) => {
      const v = s.statusCounts[k];
      const w = ((v / total) * 100).toFixed(2);
      return `<span class="mat-seg mat-${k}" style="flex:0 0 ${w}%">
        ${parseFloat(w) >= 8 ? `<span class="mat-seg-num">${v}</span>` : ''}
      </span>`;
    }).join('');
  const decaySegs = MATURITY_DECAY
    .filter((k) => (s.statusCounts[k] || 0) > 0)
    .map((k) => {
      const v = s.statusCounts[k];
      const w = ((v / total) * 100).toFixed(2);
      return `<span class="mat-seg mat-decay" style="flex:0 0 ${w}%" title="${STATUS_LABELS[k] || k} · ${v}"></span>`;
    }).join('');
  const legend = MATURITY_MAIN
    .filter((k) => (s.statusCounts[k] || 0) > 0)
    .map((k) => `
      <span class="mat-leg mat-leg-${k}">
        <span class="mat-leg-dot"></span>
        <span class="mat-leg-name">${STATUS_LABELS[k] || k}</span>
        <b class="mat-leg-val">${s.statusCounts[k]}</b>
        <span class="mat-leg-pct">${pct(s.statusCounts[k], total)}%</span>
      </span>`).join('');
  return `
    <div class="maturity-axis">
      <span class="mat-axis-end">草稿</span>
      <span class="mat-axis-mid">活跃</span>
      <span class="mat-axis-end">权威</span>
    </div>
    <div class="maturity-spectrum" role="img" aria-label="成熟度光谱">${mainSegs}${decaySegs}</div>
    <div class="maturity-legend">${legend}</div>`;
}

/**
 * 鲜活度脉搏（替代 pills 列表）：SVG 正弦波，频率/振幅对齐 token pulse 参数。
 * volatile 高频躁动 / watch 中频 / stable 低频沉静，配呼吸动画（CSS）。
 */
function pulseRowHTML(key, count, total) {
  const p = PULSE_PARAMS[key];
  const color = FRESH_COLORS[key];
  const W = 180;
  const H = 36;
  const mid = H / 2;
  const amp = H * p.amp;
  // 生成正弦波 path（60 步足够平滑）
  const steps = 60;
  let d = `M 0 ${mid.toFixed(2)}`;
  for (let i = 1; i <= steps; i++) {
    const x = (i / steps) * W;
    const y = mid + Math.sin((i / steps) * Math.PI * 2 * p.freq) * amp;
    d += ` L ${x.toFixed(2)} ${y.toFixed(2)}`;
  }
  // 动画周期：频率越高越快（volatile ~1.17s / watch ~3.18s / stable ~7s）
  const dur = (3.5 / p.freq).toFixed(2);
  const peak = Math.min(1, p.opacity + 0.42);
  return `
    <div class="pulse-row" style="--pc:${color};--pdur:${dur}s;--pbase:${p.opacity};--ppeak:${peak}">
      <span class="pulse-name">${FRESHNESS_LABELS[key] || key}</span>
      <svg class="pulse-wave" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
        <path class="pulse-path" d="${d}"/>
      </svg>
      <span class="pulse-meta">
        <b class="pulse-val">${count}</b>
        <span class="pulse-pct">${pct(count, total)}%</span>
      </span>
    </div>`;
}

/**
 * 迷你健康环（health 卡每行一个）：填充度 = 该指标的健康度（0-1，越满越健康）。
 * 视觉统一用 grade 色 + glow，让 4 个 ring 语义一致（"健康完成度"）。
 */
function miniRingSVG(healthScore, grade) {
  const r = 13;
  const cx = 16;
  const cy = 16;
  const C = 2 * Math.PI * r;
  const score = clamp01(healthScore);
  const len = (score * C).toFixed(2);
  const g = grade.toLowerCase();
  return `<svg class="mini-ring" viewBox="0 0 32 32" aria-hidden="true">
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="rgba(137,121,137,0.18)" stroke-width="2.5"/>
    <circle class="mini-prog grade-${g}" cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke-width="2.5"
      stroke-dasharray="${len} ${(C - len).toFixed(2)}" stroke-dashoffset="${(C * 0.25).toFixed(2)}"
      stroke-linecap="round"/>
  </svg>`;
}

/**
 * v2.2 知识健康体检报告（dashboard 叙事化）。
 * 信息设计加工：从"统计数字罗列"→"知识健康体检"——
 * 把 4 个孤立的统计指标综合成有评级的健康解读：
 *  - 网络连通：孤立节点 = 知识孤岛（越高连通越健康）
 *  - 知识中枢：authority 集中度（适中=健康中枢 / 过高=单点依赖 / 过低=无核心）
 *  - 成熟度：authoritative+active 占比 = 已沉淀程度
 *  - 待沉淀：draft 比例（反向指标，越低越健康）
 * 每项给 A/B/C/D 评级 + 叙事解读 + 健康度（用于迷你环），让数字变成可读的"诊断"。
 * @param {object} s getStats 输出
 * @returns {Array<{key,name,icon,value,ratio,health,grade,narrative}>}
 */
function buildHealthReport(s) {
  const total = s.total || 0;
  const connected = total - s.isolated;
  const report = [];

  // 1. 网络连通（孤立 = 孤岛警示）
  const connRatio = total ? connected / total : 1;
  const connGrade = connRatio >= 0.95 ? 'A' : connRatio >= 0.85 ? 'B' : connRatio >= 0.75 ? 'C' : 'D';
  const connNarr = s.isolated === 0
    ? `无孤岛 · 全部 ${total} 节点入网`
    : (s.isolated >= total * 0.15
      ? `${s.isolated} 个孤岛偏多（${pct(s.isolated, total)}%），建议补接关系网络`
      : `${s.isolated} 个孤岛（${pct(s.isolated, total)}%），网络连通尚可`);
  report.push({
    key: 'connectivity', name: '网络连通', icon: '◈',
    value: `${connected}/${total}`, ratio: connRatio, health: connRatio,
    grade: connGrade, narrative: connNarr,
  });

  // 2. 知识中枢（authority top5 集中度 · 双刃剑指标）
  const top5Sum = s.topAuthority.slice(0, 5).reduce((sum, n) => sum + (Number(n.authority) || 0), 0);
  const totalAuth = s.authRange.avg * total;
  const concentration = totalAuth ? top5Sum / totalAuth : 0;
  const concPct = (concentration * 100).toFixed(1);
  const concHealth = hubHealthScore(concentration);
  let concGrade;
  let concNarr;
  if (concentration >= 0.5) {
    concGrade = 'D';
    concNarr = `top5 占 ${concPct}% · 单点依赖风险，权威过度集中`;
  } else if (concentration >= 0.3) {
    concGrade = 'C';
    concNarr = `top5 占 ${concPct}% · 偏集中，核心枢纽明显`;
  } else if (concentration >= 0.08) {
    concGrade = 'B';
    concNarr = `top5 占 ${concPct}% · 中枢健康，分布适中`;
  } else {
    concGrade = 'C';
    concNarr = `top5 占 ${concPct}% · 分布偏散，缺少核心枢纽`;
  }
  report.push({
    key: 'hub', name: '知识中枢', icon: '✦',
    value: `top5 ${concPct}%`, ratio: concentration, health: concHealth,
    grade: concGrade, narrative: concNarr,
  });

  // 3. 成熟度（authoritative + active 占比 = 升格覆盖 = 已沉淀程度）
  const matureN = (s.statusCounts.authoritative || 0) + (s.statusCounts.active || 0);
  const matureRatio = total ? matureN / total : 0;
  const maturePct = (matureRatio * 100).toFixed(1);
  const matureGrade = matureRatio >= 0.7 ? 'A' : matureRatio >= 0.5 ? 'B' : matureRatio >= 0.3 ? 'C' : 'D';
  const volN = s.freshnessCounts.volatile || 0;
  const matureNarr = `authoritative+active ${matureN} 个主导`
    + (matureRatio >= 0.5 ? '，已沉淀' : '，沉淀不足')
    + (volN > 0 ? `；另 ${volN} 个易变节点需关注衰变` : '；无易变内容');
  report.push({
    key: 'maturity', name: '成熟度', icon: '◉',
    value: `${maturePct}%`, ratio: matureRatio, health: matureRatio,
    grade: matureGrade, narrative: matureNarr,
  });

  // 4. 待沉淀（draft 比例 · 反向指标，越低越健康）
  const draftN = s.statusCounts.draft || 0;
  const draftRatio = total ? draftN / total : 0;
  const draftGrade = draftRatio < 0.1 ? 'A' : draftRatio < 0.2 ? 'B' : draftRatio < 0.35 ? 'C' : 'D';
  const draftNarr = draftN === 0
    ? '无草稿 · 全部已脱离草稿期'
    : (draftRatio >= 0.35
      ? `draft ${draftN} 个（${pct(draftN, total)}%）量较大，需推进沉淀`
      : (draftRatio >= 0.2
        ? `draft ${draftN} 个（${pct(draftN, total)}%），有一定量待完善`
        : `draft ${draftN} 个（${pct(draftN, total)}%），少量待完善，可控`));
  report.push({
    key: 'sediment', name: '待沉淀', icon: '◇',
    value: `draft ${draftN}`, ratio: draftRatio, health: 1 - draftRatio,
    grade: draftGrade, narrative: draftNarr,
  });

  return report;
}

function renderCardTotal(s, kindCount) {
  return `
  <article class="dash-card card-total" style="grid-area:total">
    <header class="card-h">
      <span class="card-tag">TOTAL</span>
      <span class="card-title">条目总数</span>
    </header>
    <div class="big-num">${s.total}</div>
    <div class="card-foot muted">${s.edges} 条关系 · ${kindCount} 种 kind</div>
  </article>`;
}

function renderCardIsolated(s) {
  const ratio = pct(s.isolated, s.total);
  const barW = Math.min(100, (s.isolated / Math.max(1, s.total)) * 100 * 2);
  return `
  <article class="dash-card card-iso" style="grid-area:iso">
    <header class="card-h">
      <span class="card-tag">ISOLATED</span>
      <span class="card-title">孤立节点</span>
    </header>
    <div class="big-num small">${s.isolated}</div>
    <div class="iso-ratio">占比 ${ratio}%</div>
    <div class="iso-bar"><span style="width:${barW.toFixed(1)}%"></span></div>
  </article>`;
}

/**
 * kind 卡：donut → 知识构成光谱条带。
 * 顶部光谱（发光段）+ 下方图例 + 一句话诊断。
 */
function renderCardRing(kindEntries, total) {
  const insight = kindInsight(kindEntries, total);
  return `
  <article class="dash-card card-ring" style="grid-area:ring">
    <header class="card-h">
      <span class="card-tag">KIND</span>
      <span class="card-title">知识构成</span>
    </header>
    ${spectrumBandHTML(kindEntries, total)}
    <ul class="legend">
      ${kindEntries.map(([k, v]) => `
        <li>
          <span class="dot" style="background:${KIND_COLORS[k] || 'var(--kind-view)'}"></span>
          <span class="lg-name">${KIND_LABELS[k] || k}</span>
          <span class="lg-val">${v}</span>
          <span class="lg-pct muted">${pct(v, total)}%</span>
        </li>`).join('')}
    </ul>
    <div class="card-insight"><span class="insight-mark">◇</span>${escapeHtml(insight)}</div>
  </article>`;
}

function renderCardDomain(topDomains) {
  const domMax = topDomains.length ? topDomains[0].count : 1;
  return `
  <article class="dash-card card-dom" style="grid-area:dom">
    <header class="card-h">
      <span class="card-tag">DOMAIN</span>
      <span class="card-title">领域 Top ${topDomains.length}</span>
    </header>
    <ul class="bar-list">
      ${topDomains.map((d) => `
        <li>
          <span class="bl-name" title="${escapeHtml(d.domain)}">${escapeHtml(domainLabel(d.domain))}</span>
          <span class="bl-track"><span class="bl-fill" style="width:${((d.count / domMax) * 100).toFixed(1)}%"></span></span>
          <span class="bl-val">${d.count}</span>
        </li>`).join('')}
    </ul>
  </article>`;
}

/**
 * status 卡：柱图 → 成熟度光谱（升格渐变叙事）。
 * draft→active→authoritative 主段渐变 + 退役尾段 + 一句话诊断。
 */
function renderCardStatus(s, total) {
  const insight = maturityInsight(s, total);
  return `
  <article class="dash-card card-status" style="grid-area:stat">
    <header class="card-h">
      <span class="card-tag">STATUS</span>
      <span class="card-title">成熟度光谱</span>
    </header>
    ${maturitySpectrumHTML(s, total)}
    <div class="card-insight"><span class="insight-mark">◇</span>${escapeHtml(insight)}</div>
  </article>`;
}

/**
 * freshness 卡：pills 列表 → 鲜活度脉搏（SVG 波形 + 呼吸动画）。
 * 三条波形（stable/watch/volatile）+ 一句话诊断。
 */
function renderCardFresh(s) {
  const freshEntries = FRESH_ORDER
    .filter((k) => (s.freshnessCounts[k] || 0) > 0)
    .map((k) => [k, s.freshnessCounts[k]]);
  const insight = freshnessInsight(s, s.total);
  return `
  <article class="dash-card card-fresh" style="grid-area:fresh">
    <header class="card-h">
      <span class="card-tag">FRESHNESS</span>
      <span class="card-title">鲜活度脉搏</span>
    </header>
    <div class="pulse-list">
      ${freshEntries.map(([k, v]) => pulseRowHTML(k, v, s.total)).join('')}
    </div>
    <div class="card-insight"><span class="insight-mark">◇</span>${escapeHtml(insight)}</div>
  </article>`;
}

function renderCardAuthority(topAuthority) {
  const top5 = topAuthority.slice(0, 5);
  return `
  <article class="dash-card card-auth" style="grid-area:auth">
    <header class="card-h">
      <span class="card-tag">AUTHORITY</span>
      <span class="card-title">权重 Top ${top5.length}</span>
    </header>
    <ol class="auth-list">
      ${top5.map((n, i) => `
        <li>
          <span class="auth-rank">${i + 1}</span>
          <span class="auth-title" title="${escapeHtml(n.title)}">${escapeHtml(n.title)}</span>
          <span class="tag auth-kind" style="color:${KIND_COLORS[n.kind] || '#897989'}">${KIND_LABELS[n.kind] || n.kind}</span>
          <span class="auth-track"><span class="auth-fill" style="width:${authPct(Number(n.authority) || 0).toFixed(1)}%;background:${KIND_COLORS[n.kind] || '#897989'}"></span></span>
          <span class="auth-val">${formatShare(Number(n.authority) || 0)}</span>
        </li>`).join('')}
    </ol>
  </article>`;
}

/**
 * 健康体检卡：保留 A/B/C/D 评级 + 叙事，每行加迷你健康环（grade 色发光）+ 顶部总评。
 */
function renderCardHealth(report) {
  const verdict = healthVerdict(report);
  return `
  <article class="dash-card card-health" style="grid-area:decay">
    <header class="card-h">
      <span class="card-tag">INSIGHT</span>
      <span class="card-title">知识健康体检</span>
      <span class="health-verdict verdict-${verdict.tone}" title="综合评级">总评 · ${verdict.label}</span>
    </header>
    <ul class="health-list">
      ${report.map((item) => `
        <li class="health-row">
          <span class="health-grade grade-${item.grade.toLowerCase()}" aria-label="评级 ${item.grade}">${item.grade}</span>
          <div class="health-body">
            <div class="health-head">
              <span class="health-name"><span class="health-icon" aria-hidden="true">${item.icon}</span>${item.name}</span>
              <span class="health-value">${escapeHtml(item.value)}</span>
            </div>
            <div class="health-narrative">${escapeHtml(item.narrative)}</div>
          </div>
          <span class="health-mini" aria-hidden="true">${miniRingSVG(item.health, item.grade)}</span>
        </li>`).join('')}
    </ul>
  </article>`;
}

function renderHTML(s) {
  const kindEntries = Object.entries(s.kindCounts).filter(([, v]) => v > 0);
  const report = buildHealthReport(s);

  return `
  <section class="dash-grid" role="region" aria-label="知识库仪表盘">
    ${renderCardTotal(s, kindEntries.length)}
    ${renderCardIsolated(s)}
    ${renderCardRing(kindEntries, s.total)}
    ${renderCardDomain(s.topDomains)}
    ${renderCardStatus(s, s.total)}
    ${renderCardFresh(s)}
    ${renderCardAuthority(s.topAuthority)}
    ${renderCardHealth(report)}
  </section>`;
}

const ERROR_HTML = `
<div class="placeholder">
  <div class="panel placeholder-card">
    <h1 class="placeholder-title">仪表盘</h1>
    <div class="placeholder-sub">数据加载失败</div>
    <div class="placeholder-hint">无法读取 <code>window.__EK_GRAPH</code>，请确认 <code>data.js</code> 已加载。</div>
  </div>
</div>`;

export function mount(container) {
  const graph = loadGraph();
  if (!graph) {
    container.innerHTML = ERROR_HTML;
    container.classList.add('view-dashboard');
    return;
  }
  const index = buildIndex(graph);
  const stats = getStats(graph, index);
  container.innerHTML = renderHTML(stats);
  container.classList.add('view-dashboard');
}

export function unmount(container) {
  container.innerHTML = '';
  container.classList.remove('view-dashboard');
}
