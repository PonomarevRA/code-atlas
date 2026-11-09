/** Пользовательские разделы отделены от масштаба системной проекции. */
export const VIEW_MODES = Object.freeze([
  { id: 'architecture', label: 'Код' },
  { id: 'system', label: 'Архитектура' },
  { id: 'groups', label: 'Группы · экспертный' },
]);
/** Старое имя solution остаётся совместимым с сохранённым контекстом и тестами проекции. */
export function normalizeViewMode(mode) {
  if (mode === 'solution') return 'system';
  return VIEW_MODES.some(item => item.id === mode) ? mode : 'architecture';
}
/** Определяет общий системный раздел независимо от прежнего имени масштаба. */
export function isSystemMode(mode) { return normalizeViewMode(mode) === 'system'; }
/** Репозитории — начальный масштаб; проекты раскрывают исходные .csproj и пакеты. */
export function systemScale(mode, scale) { return mode === 'solution' || scale !== 'projects' ? 'repositories' : 'projects'; }
/** Показывает только работающие инструменты раздела, не сбрасывая их значения. */
export function presentViewMode(mode, documentRoot = document) {
  const system = isSystemMode(mode);
  const code = normalizeViewMode(mode) === 'architecture';
  for (const item of documentRoot.querySelectorAll('[data-code-tools]')) item.hidden = system;
  for (const item of documentRoot.querySelectorAll('[data-groups-tools]')) item.hidden = system || code;
  for (const item of documentRoot.querySelectorAll('[data-architecture-tools]')) item.hidden = !code;
}
