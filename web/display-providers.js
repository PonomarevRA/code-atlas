export const DISPLAY_PROVIDERS = Object.freeze([
  { id: 'topology', label: 'Топология · карта', projectBlocks: true, bundles: true },
  { id: 'flow', label: 'Поток · карточки', projectBlocks: false, bundles: false },
]);
/** Возвращает безопасный локальный провайдер отображения, независимо от алгоритма группировки. */
export function displayProvider(id) { return DISPLAY_PROVIDERS.find(provider => provider.id === id) || DISPLAY_PROVIDERS[0]; }

/** Code has one reading surface; legacy topology addresses remain valid for systems and expert groups. */
export function workspaceDisplayProvider(viewMode, id) {
  return displayProvider(viewMode === 'architecture' ? 'flow' : id);
}
