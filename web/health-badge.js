const healthMetrics = {
  readability: ['readabilityScore', 'Локальная читаемость R', 'R'],
  health: ['score', 'Здоровье H', 'H'], documentation: ['documentationScore', 'Документированность D', 'D'],
  organization: ['organizationScore', 'Организация O', 'O'], transparency: ['transparencyScore', 'Прозрачность T', 'T'],
};
/** Readability needs its own complete versioned profile, even when a bare scalar is present. */
function metricScore(health, metric, field) {
  if (metric === 'readability' && (!health?.readability?.version || health.readability.status !== 'complete')) return null;
  return health?.[field];
}
/** Health is a versioned static profile; high is good and unknown never turns green. */
export function nodeHealthText(health, metric = 'health') {
  const [field, name] = healthMetrics[metric] || healthMetrics.health;
  const score = metricScore(health, metric, field);
  const profile = metric === 'readability' ? health?.readability : health;
  return `${name}: ${score ?? 'неизвестно'} / 100. ` +
    `Статус: ${profile?.status || 'недоступно'}. Политика: ${(metric === 'readability' ? health?.readability?.version : health?.version) || 'не установлена'}. ` +
    `Источник: ${health?.sourceKind || 'не установлен'}. ` +
    'Организация учитывает файлы и папки выбранного участка; число файлов и глубина папок сами по себе не цель. ' +
    'R — некалиброванная эвристика локального синтаксиса; смысловое ревью не выполнено. Наличие документации не подтверждает её точность. Поведение и производительность проверяются отдельно.';
}

/** Distinct H/D markers preserve the existing complexity marker when that mode is selected. */
export function appendHealthBadge(group, health, x, y, metric = 'health') {
  const [field, , prefixText] = healthMetrics[metric] || healthMetrics.health;
  const score = metricScore(health, metric, field);
  const known = Number.isFinite(score);
  const color = !known ? '#7b8784' : score < 60 ? '#b13e43' : score < 80 || (metric === 'readability' ? health?.readability?.verdict : health?.verdict) === 'REVIEW' ? '#986718' : '#327f70';
  const svg = (tag, attrs = {}) => { const item = document.createElementNS('http://www.w3.org/2000/svg', tag); for (const [key, value] of Object.entries(attrs)) item.setAttribute(key, value); return item; };
  const label = nodeHealthText(health, metric);
  const badge = svg('g', {class: 'code-health-badge', transform: `translate(${x},${y})`, role: 'img', tabindex: 0,
    'aria-label': label, 'data-health-score': known ? score : '', 'data-health-metric': metric});
  badge.append(svg('circle', {r: 12, fill: color}));
  const number = svg('text', {y: 4, 'text-anchor': 'middle', fill: '#fff', 'font-size': 9, 'font-weight': 700});
  number.textContent = known ? Math.round(score) : '?'; badge.append(number);
  const prefix = svg('text', {x: 0, y: -15, 'text-anchor': 'middle', fill: color, 'font-size': 8});
  prefix.textContent = prefixText; badge.append(prefix);
  const title = svg('title'); title.textContent = label; badge.append(title); group.append(badge);
}
