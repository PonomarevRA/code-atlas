import { NodeDescriptionViewer } from './node-description.js';
import { renderAttributeFacts } from './attribute-details.js';

/** Самостоятельная грань просмотра использует проверку снимка и отмену запросов общего viewer. */
export class NodeAttributesViewer extends NodeDescriptionViewer {
  /** Сохраняет единый контракт endpoint без запуска обхода цепочки вызовов. */
  constructor(root, getContext, options = {}) {
    super(root, getContext, { ...options, titlePrefix: 'Атрибуты', loadingText: 'Читаем атрибуты объявления, типов и параметров…' });
  }
  /** Рисует только атрибуты выбранного узла с переходами к точным строкам. */
  render(data) {
    this.find('status').textContent = 'Явно записанные аннотации и выражения аргументов · область и происхождение указаны в фактах.';
    this.find('body').replaceChildren(renderAttributeFacts(data, this.onSource));
    if (this.scrollOnRender) this.root.scrollIntoView?.({ behavior: 'instant', block: 'start' });
  }
}
