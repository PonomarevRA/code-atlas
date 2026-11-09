import { el } from './dom.js';

/** Читает одну страницу исходного AST по действию пользователя, без снятия лимитов method-flow. */
export function renderAstExplorer(context, { fetcher, signal, isCurrent }) {
  const root = el('details', undefined, 'node-ast-explorer');
  root.append(el('summary', 'Структура AST · есть ли глубже'));
  root.append(el('p', 'Это синтаксическая структура. Порядок исполнения и цели вызовов здесь не вычисляются. Недоступные рекомендации не означают PASS.', 'small muted'));
  const status = el('p', 'Дочерние узлы загружаются по запросу.');
  const controls = el('div');
  const content = el('div');
  let revision = 0;

  /** Каждый переход проверяет выбранный символ и снимок, включая запоздавшие ответы. */
  async function load(chunkRef = null) {
    const own = ++revision;
    status.textContent = 'Читаем страницу AST…';
    try {
      const body = { nodeId: context.nodeId, snapshotId: context.snapshotId, limit: 24 };
      if (chunkRef) body.chunkRef = chunkRef;
      const response = await fetcher(`/api/projects/${encodeURIComponent(context.projectId)}/method-flow-chunk`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, signal, body: JSON.stringify(body),
      });
      const data = await response.json();
      if (signal?.aborted || !isCurrent() || revision !== own) return;
      if (!response.ok) throw new Error(data.error || `Ошибка AST: ${response.status}`);
      if (data.snapshotId !== context.snapshotId || data.nodeId !== context.nodeId) throw new Error('Страница AST относится к другому узлу или снимку.');
      controls.replaceChildren();
      content.replaceChildren();
      if (!data.available) {
        status.textContent = `AST недоступно: ${data.reason || 'анализ не выполнен'}`;
        controls.append(button('Повторить', () => load()));
        return;
      }
      status.textContent = `${data.current?.type || 'AST'} · прямых дочерних узлов: ${data.childCount ?? '?'}${data.hasParseError ? ' · есть ошибки разбора' : ''}`;
      if (data.parentRef) controls.append(button('На уровень выше', () => load(data.parentRef)));
      if (data.nextRef) controls.append(button('Следующая страница', () => load(data.nextRef)));
      for (const child of (data.children || []).slice(0, 64)) {
        const row = el('div');
        row.append(el('code', `${child.type}: ${child.label || ''}`));
        if (child.deferredBoundary) row.append(el('span', ' · отдельное тело', 'small muted'));
        if (child.hasChildren && child.ref) row.append(button('Глубже', () => load(child.ref)));
        content.append(row);
      }
    } catch (error) {
      if (signal?.aborted || !isCurrent() || revision !== own) return;
      status.textContent = error.message;
      controls.replaceChildren(button('Повторить', () => load()));
    }
  }

  controls.append(button('Показать дочерние узлы', () => load()));
  root.append(status, controls, content);
  return root;
}

/** Обычная кнопка сохраняет клавиатурную навигацию и текстовую подпись. */
function button(label, callback) {
  const node = el('button', label);
  node.type = 'button';
  node.addEventListener('click', callback);
  return node;
}
