import { el } from './dom.js';
import { infrastructureCard } from './sequence-infrastructure.js';

/** Keep destinations, configuration alternatives and evidence inspectable for the selected method. */
export function renderInfrastructureDetails(data, onSource = () => {}) {
  const facts = data.infrastructure || [];
  if (!facts.length) return null;
  const section = el('details', undefined, 'parameter-details');
  section.append(el('summary', `Ресурсы метода · ${facts.length} из ${data.infrastructureTotal ?? facts.length}`));
  section.append(el('p', 'Статические связи текущего исходника. Замыкания включены лексически; достижимость и активное окружение неизвестны.', 'small muted'));
  for (const fact of facts) {
    const presentation = infrastructureCard(fact), item = el('details', undefined, 'parameter-call');
    item.append(el('summary', presentation.label), el('p', presentation.context));
    const source = el('button', `Место обращения · строка ${fact.line}`, 'parameter-source');
    source.addEventListener('click', () => onSource(data.nodeId, fact.line));
    item.append(source, el('pre', presentation.evidence, 'parameter-expression'));
    section.append(item);
  }
  return section;
}
