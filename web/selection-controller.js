/** Выбор уже показанного символа меняет только оформление; навигация/раскладка остаются у приложения. */
export function selectInCurrentView(view, id, { focused = false } = {}) {
  if (focused || !view.nodeElements.has(id)) return false;
  view.setSelected(id);
  return true;
}
