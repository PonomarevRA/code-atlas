const coverageNames = {inventory:'инвентарь', local:'размер и ветвления', outgoing:'исходящие связи',
  incoming:'входящие связи', cycles:'циклы', fragmentation:'связанные малые файлы'};
const reasons = {'incomplete-declaration-inventory':'неполный инвентарь объявлений',
  'incomplete-local-metrics':'неполные локальные метрики', 'unresolved-source-dependencies':'не разрешены вызовы источника',
  'unlocalized-target-or-incomplete-scope':'неизвестная цель может находиться в любой части выбранного участка',
  'absence-not-proven-on-partial-graph':'отсутствие цикла не доказано на неполном графе',
  'partial-topology-or-local-inventory':'неполны связи или локальный инвентарь',
  'incomplete-local-inventory-in-scope':'неполный инвентарь в выбранном участке'};

/** Explain independent completeness; legacy rows never acquire successful checks from an empty list. */
export function organizationCoverageText(coverage) {
  if (!coverage || !Object.keys(coverage).length) return 'Полнота отдельных показателей не сохранена в этой оценке.';
  const details = [];
  for (const [name, fact] of Object.entries(coverage)) {
    const label = coverageNames[name] || name;
    details.push(fact.status === 'complete' ? `${label}: данные полные` : `${label}: ${reasons[fact.reason] || fact.reason || 'полнота неизвестна'}`);
  }
  return details.join('; ');
}

/** Render bounded organization facts as text; paths/comments cannot supply markup. */
export function renderOrganization(host, data) {
  const element = (tag, text) => { const item = document.createElement(tag); if (text != null) item.textContent = text; return item; };
  host.replaceChildren();
  if (!data || data.status === 'unavailable') {
    host.append(element('p', 'Организация файлов и папок ещё не измерена. Измерьте текущие файлы или обновите снимок.'));
    return;
  }
  host.append(element('p', `${data.fileCount} файлов · ${data.folderCount} папок · измерено ${data.assessedFiles} · неизвестно ${data.unknownFiles}. O ${data.score ?? '?'} / 100 · ${data.status}. Область: выбранные файлы.`));
  if (data.coverage) {
    for (const [name, counts] of Object.entries(data.coverage)) host.append(element('p', `${coverageNames[name] || name}: полные данные ${counts.complete} / ${counts.total}; неизвестно ${counts.unknown}.`));
  }
  host.append(element('p', 'Входящие связи и циклы относятся к графу выбранных источников; внешний код не входит в знаменатель. Числа при неполных данных — наблюдаемые факты, а не итоговая оценка.'));
  const table = element('table'), header = element('tr');
  for (const title of ['Файл', 'O', 'Объявления / операторы', 'Связи с файлами', 'Цикл']) header.append(element('th', title));
  table.append(header);
  for (const row of data.files || []) {
    const tr = element('tr'), metrics = row.metrics || {};
    for (const value of [row.file, row.score ?? '?', `${metrics.declarations ?? '?'} / ${metrics.statements ?? '?'}`, metrics.outgoingFiles ?? '?', metrics.inCycle ? 'наблюдается' : row.status === 'complete' ? 'не найден в участке' : 'не установлен']) tr.append(element('td', String(value)));
    table.append(tr);
    if (row.coverage) {
      const detail = element('tr'), cell = element('td', organizationCoverageText(row.coverage));
      cell.colSpan = 5;
      detail.append(cell);
      table.append(detail);
    }
  }
  host.append(table);
  for (const folder of data.folders || []) host.append(element('p', `Папка ${folder.path}: ${folder.files} файлов · ${folder.tinyFiles} маленьких · ${folder.outgoingFolders} внешних папок · глубина ${folder.depth}${folder.inCycle ? ' · наблюдается цикл' : ''}${folder.complete ? '' : ' · факты неполные'}`));
  if (data.hotspotsTruncated) host.append(element('p', 'Показано до пяти файлов и папок. Полный контекст каждого метода доступен в записи оценки.'));
}
