import { declarationSummary } from './card-documentation.js';

/** Keep expressions as source evidence while making reuse and presentation limits visible. */
export function sequencePresentation(node) {
  const summary = declarationSummary(node.documentation);
  const expression = node.sourceExpression || node.condition || '';
  let title = node.label, context = summary || expression, notice = '', extensionContext = false;
  if (node.conditionResult === 'reuse') {
    title = node.kind === 'loop' ? `Цикл ${node.controlKind} · результат условия` : 'Результат условия';
    context = node.conditionExpression || expression;
  } else if (node.decision) context = node.kind === 'loop' ? `Цикл ${node.controlKind || ''}` : 'Проверка условия';
  else if (context === title) context = '';
  // Rare provider facts use the same plain-text card context and optional mod decorators.
  if (!context && node.extensions?.length) {
    context = node.extensions.map(item => item.label).join(' · ');
    extensionContext = true;
  }
  if (node.candidateCount > node.shownCandidates) {
    notice = `Показано ${node.shownCandidates} из ${node.candidateCount} реализаций`;
    context = notice;
  }
  if (node.expansionNotice && (!node.bodyExpanded || node.expansionFailed || node.expansionPending)) { notice = node.expansionNotice; context = notice; }
  if (node.kind === 'candidate') context = summary || node.condition || 'Возможная реализация';
  const data = node.dataSummary;
  if (data && (data.truncated || data.total > data.shown)) {
    const count = data.truncated ? `не менее ${data.total}` : String(data.total);
    notice = `Показано ${data.shown} из ${count} значений`;
    context = `${notice}. ${expression}`;
  }
  return { title, context, summary, notice, contextIsCode: node.kind !== 'candidate' && !summary && !notice && !extensionContext && !node.decision && Boolean(expression) };
}
