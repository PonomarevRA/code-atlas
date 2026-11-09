import { el } from './dom.js';

const SCOPE_LABELS = { declaration: 'Выбранное объявление', type: 'Содержащий тип', parameter: 'Параметр', file: 'Уровень файла' };

/** Показывает лексические аннотации отдельно по владельцам; проектный текст никогда не исполняется. */
export function renderAttributeFacts(data, onSource = () => {}) {
  const section = el('section', undefined, 'attribute-details');
  section.append(el('h3', 'Аннотации и атрибуты'));
  const facts = data.attributeFacts;
  if (!facts || !['parsed', 'partial'].includes(facts.status)) {
    section.append(el('p', 'Атрибуты недоступны: объявление не удалось разобрать поддержанным провайдером.', 'small muted'));
    return section;
  }
  const incomplete = facts.status === 'partial' || facts.truncated;
  let count = 0;
  for (const group of facts.groups || []) {
    const block = el('section', undefined, 'attribute-group');
    block.append(el('h4', `${SCOPE_LABELS[group.scope] || group.scope || 'Контекст'}${group.name ? ` · ${group.name}` : ''}`));
    if (!group.attributes?.length) block.append(el('p', incomplete ? 'В доступной части атрибуты не найдены.' : 'Явные атрибуты отсутствуют.', 'small muted'));
    for (const attribute of group.attributes || []) {
      count++;
      const card = el('article', undefined, 'attribute-card');
      card.append(el('strong', `@ ${attribute.name || 'Атрибут'}`));
      if (attribute.target) card.append(el('span', `Цель: ${attribute.target}`, 'attribute-target'));
      if (attribute.text) card.append(el('pre', attribute.text, 'attribute-expression'));
      for (const argument of attribute.arguments || []) {
        const row = el('div', undefined, 'attribute-argument');
        row.append(el('span', argument.name || `Аргумент ${argument.position}`, 'small muted'), el('code', argument.expression || ''));
        card.append(row);
      }
      if (attribute.truncated) card.append(el('p', 'Текст или аргументы показаны частично.', 'small muted'));
      if (attribute.file && Number.isInteger(attribute.line)) {
        const source = el('button', `${attribute.file}:${attribute.line}`, 'parameter-source');
        const ownerId = attribute.ownerNodeId || attribute.declarationNodeId || group.ownerNodeId || data.nodeId;
        source.addEventListener('click', () => onSource(ownerId, attribute.line));
        card.append(source);
      }
      block.append(card);
    }
    section.append(block);
  }
  if (!count) section.append(el('p', incomplete ? 'В доступной части объявления атрибуты не найдены.' : 'В выбранном объявлении и проверенном контексте явные атрибуты не найдены.', 'small muted'));
  if (incomplete) section.append(el('p', 'Извлечение неполное: показана часть атрибутов или контекста.', 'small muted'));
  section.append(el('p', facts.warning || 'Показаны записи текущего исходника. Наследование атрибутов и их действие при выполнении не вычисляются.', 'small muted'));
  return section;
}
