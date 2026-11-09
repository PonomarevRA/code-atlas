/** Находит элемент интерфейса по уникальному id. */
export const $ = id => document.getElementById(id);
/** Создаёт безопасный текстовый элемент без интерпретации данных проекта как HTML. */
export function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
