/** Нормализует отображаемые имена, чтобы Unicode-эквиваленты давали одинаковый цвет. */
export function normalizeColorName(name) { return String(name ?? '').normalize('NFC').trim().toLowerCase(); }
/** Хеш имени стабилен между загрузками; не зависит от соседей и числовых id сообществ. */
export function nameHash(name) {
  let hash = 2166136261;
  for (const character of normalizeColorName(name)) hash = Math.imul(hash ^ character.codePointAt(0), 16777619);
  return hash >>> 0;
}
/** Палитра проекта: бледная область, контрастная граница и тёмный текст. */
export function projectColor(name, { unassigned = false } = {}) {
  if (unassigned) return { hue: 210, fill: 'hsl(210 8% 94%)', stroke: 'hsl(210 9% 43%)', text: 'hsl(210 12% 24%)' };
  const hue = nameHash(name) % 360;
  return { hue, fill: `hsl(${hue} 48% 94%)`, stroke: `hsl(${hue} 48% 43%)`, text: `hsl(${hue} 48% 24%)` };
}
/** Цвет имени узла варьируется в пределах ±18° оттенка его проекта; без владельца — нейтральная гамма. */
export function nodeColor(node, projects = []) {
  const id = String(node.codeProjectId || '__unassigned__');
  const project = projects.find(item => String(item.id) === id);
  const name = node.label || node.file || node.id || '';
  const hash = nameHash(name);
  const lightness = 36 + (hash >>> 8) % 12;
  if (id === '__unassigned__') return `hsl(210 ${5 + hash % 8}% ${lightness}%)`;
  const base = projectColor(project?.label || id);
  const hue = (base.hue + hash % 37 - 18 + 360) % 360;
  return `hsl(${hue} ${48 + (hash >>> 16) % 18}% ${lightness}%)`;
}
/** Фрагмент наследует проект и исходное имя группы, а смешанная группа — только своё имя. */
export function groupColor(group, projects = []) {
  const label = group.originalCommunityLabel || group.label || 'Группа';
  if (group.codeProjectId) return nodeColor({ label, codeProjectId: group.codeProjectId }, projects);
  return `hsl(${nameHash(label) % 360} 48% 43%)`;
}
