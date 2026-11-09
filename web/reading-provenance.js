import { el } from './dom.js';

/** Normalize additive provenance without treating a legacy response as current evidence. */
export function readingProvenance(value = {}) {
  const facts = value.sourceProvenance || value;
  const hash = input => typeof input === 'string' && /^[a-f0-9]{64}$/i.test(input) ? input.toLowerCase() : null;
  const capturedSourceHash = hash(facts.capturedSourceHash), currentSourceHash = hash(facts.currentSourceHash);
  const comparable = capturedSourceHash && currentSourceHash && facts.sourceKind === 'working-tree';
  const claimedComparison = comparable && (facts.status === 'match' && capturedSourceHash === currentSourceHash
    || facts.status === 'changed' && capturedSourceHash !== currentSourceHash);
  return { status: claimedComparison ? facts.status : 'unknown',
    snapshotId: facts.snapshotId || value.snapshotId || null, snapshotCreatedAt: facts.snapshotCreatedAt || null,
    capturedSourceHash, currentSourceHash,
    currentReadAt: facts.currentReadAt || null, sourceKind: facts.sourceKind || value.sourceKind || null,
    reason: facts.reason || 'comparison-unavailable', file: facts.file || value.file || '' };
}

/** Display a nonblocking comparison; rebuilding remains an explicit caller-owned action. */
export function renderReadingProvenance(container, value, { onContinue, onRefresh } = {}) {
  const facts = readingProvenance(value), section = el('section', undefined, 'reading-provenance');
  section.setAttribute?.('data-source-status', facts.status);
  const labels = { match: 'Прочитанный файл совпадает со снимком.', changed: 'Прочитанный файл отличается от снимка.',
    unknown: 'Совпадение со снимком не установлено.' };
  section.append(el('p', labels[facts.status], facts.status === 'changed' ? 'reader-notice' : 'small muted'));
  section.append(el('small', `Карта: ${facts.snapshotCreatedAt || 'дата не установлена'} · снимок ${facts.snapshotId || 'не установлен'}`));
  section.append(el('p', `Исходник: ${facts.file || 'файл не установлен'} · прочитан ${facts.currentReadAt || 'текущее чтение не подтверждено'}`, 'small muted'));
  const hashes = el('details', undefined, 'reading-provenance-hashes');
  hashes.append(el('summary', 'Хеши и происхождение'),
    el('p', `Снимок: ${facts.capturedSourceHash || 'хеш отсутствует'}`),
    el('p', `Прочитанный файл: ${facts.currentSourceHash || 'хеш отсутствует'}`),
    el('small', `Источник: ${facts.sourceKind || 'не установлен'} · ${facts.reason}`));
  section.append(hashes);
  if (facts.status === 'changed') {
    const actions = [['Продолжить чтение', onContinue], ['Обновить карту', onRefresh]];
    for (const [label, callback] of actions) {
      if (typeof callback !== 'function') continue;
      const button = el('button', label); button.type = 'button';
      button.addEventListener('click', callback); section.append(button);
    }
  }
  container.append(section);
  return section;
}
