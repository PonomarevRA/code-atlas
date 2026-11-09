/** Семантические роли помогают читать карту; соглашение об имени не считается доказательством поведения. */
export const CODE_ROLES = [
  { id: 'entry', label: 'Входы', color: '#a76b13', fill: '#faf2df', description: 'Принимает запросы или сообщения и передаёт управление дальше.' },
  { id: 'logic', label: 'Логика и расчёты', color: '#247b65', fill: '#eaf5f0', description: 'Содержит правила обработки и расчёты.' },
  { id: 'data', label: 'Данные', color: '#397db2', fill: '#eaf2fa', description: 'Хранит, читает или подготавливает данные.' },
  { id: 'contract', label: 'Контракты', color: '#7a5eb0', fill: '#f1edf9', description: 'Описывает интерфейсы, модели и форматы обмена.' },
  { id: 'integration', label: 'Интеграции', color: '#bb653b', fill: '#fbefe7', description: 'Связывает код с другими сервисами или транспортом.' },
  { id: 'test', label: 'Тесты', color: '#a5487e', fill: '#f9edf4', description: 'Проверяет поведение кода.' },
  { id: 'infrastructure', label: 'Инфраструктура', color: '#68788b', fill: '#edf1f5', description: 'Настраивает запуск и общие технические механизмы.' },
  { id: 'unknown', label: 'Роль не определена', color: '#74827d', fill: '#f0f3f1', description: 'По доступным метаданным нельзя надёжно определить назначение.' },
];
const roles = new Map(CODE_ROLES.map(role => [role.id, role]));
const explicitRoles = new Map([
  ['controller', 'entry'], ['handler', 'entry'], ['consumer', 'entry'], ['endpoint', 'entry'],
  ['calculator', 'logic'], ['service', 'logic'], ['domain', 'logic'],
  ['repository', 'data'], ['storage', 'data'], ['database', 'data'], ['entity', 'data'],
  ['interface', 'contract'], ['dto', 'contract'], ['model', 'contract'],
  ['client', 'integration'], ['proxy', 'integration'], ['adapter', 'integration'],
  ['configuration', 'infrastructure'], ['config', 'infrastructure'],
]);

/** Формирует роль с объяснением основания, сохраняя единый справочник цветов. */
function result(id, evidence, confidence) {
  return { ...roles.get(id), evidence, confidence };
}

/** Краткий состав блока не скрывает неизвестные роли и не смешивает их с подтверждёнными. */
export function codeRoleSummary(roleCounts = {}) {
  const parts = [];
  for (const role of CODE_ROLES) {
    const count = Number(roleCounts[role.id]);
    if (Number.isFinite(count) && count > 0) parts.push(`${role.label}: ${count}`);
  }
  return parts.join(' · ');
}

/** Роль агрегата требует 70% всех классифицированных элементов; неизвестные тоже входят в знаменатель. */
function aggregateRole(roleCounts) {
  let total = 0;
  let dominantId = 'unknown';
  let dominantCount = 0;
  for (const role of CODE_ROLES) {
    const count = Number(roleCounts[role.id]);
    if (!Number.isFinite(count) || count <= 0) continue;
    total += count;
    if (role.id !== 'unknown' && count > dominantCount) { dominantId = role.id; dominantCount = count; }
  }
  if (!total) return null;
  const evidence = `Состав блока по метаданным и соглашениям: ${codeRoleSummary(roleCounts)}`;
  if (dominantCount / total >= 0.7) return result(dominantId, evidence, 'convention');
  return { ...result('unknown', evidence, 'unknown'), description: 'В блоке нет одной преобладающей роли. Состав помогает понять, какие части кода здесь находятся.' };
}

/**
 * Классифицирует сначала явную роль/kind, затем соглашения о папках и именах файлов/проектов.
 * Имя отдельного метода не определяет роль: Calculate/Load может встречаться в любом слое.
 * confidence описывает источник классификации, а не точность статического call-графа.
 */
export function classifyCodeNode(node) {
  for (const field of ['architectureRole', 'role', 'category', 'kind']) {
    const value = String(node[field] || '').toLowerCase();
    const id = roles.has(value) ? value : explicitRoles.get(value);
    if (id) return result(id, `Метаданные ${field}: ${node[field]}`, 'metadata');
  }
  if (node.roleCounts) {
    const aggregate = aggregateRole(node.roleCounts);
    if (aggregate) return aggregate;
  }
  const path = String(node.targetFile || node.file || '').replaceAll('\\', '/');
  const project = node.architectureLevel === 'projects' ? String(node.label || '') : '';
  const convention = (path || project).toLowerCase();
  const rules = [
    ['test', /(?:^|[/.])(?:tests?|specifications?)(?:[/.]|$)|(?:tests?|specs?)\.(?:cs|js|ts|py)$/],
    ['contract', /(?:^|[/.])(?:contracts?|dto|models?|abstractions?|interfaces?)(?:[/.]|$)/],
    ['entry', /(?:^|[/.])(?:controllers?|endpoints?|consumers?|handlers?|api)(?:[/.]|$)|(?:controller|consumer|handler)\.(?:cs|ts|js)$/],
    ['integration', /(?:^|[/.])(?:clients?|proxies|proxy|messaging|kafka|rabbitmq|adapters?|integration)(?:[/.]|$)|(?:client|proxy|adapter)\.cs$/],
    ['data', /(?:^|[/.])(?:repositories|repository|persistence|storage|stores?|data|database|entities|dataproviders?|dataaccess)(?:[/.]|$)|(?:repository|dbcontext)\.cs$/],
    ['infrastructure', /(?:^|[/.])(?:configuration|logging|extensions?|hosting|bootstrap|infrastructure)(?:[/.]|$)|(?:configuration|settings)\.cs$/],
    ['logic', /(?:^|[/.])(?:calculations?|calculators?|services?|domain|providers?)(?:[/.]|$)|(?:calculator|service|provider)\.cs$/],
  ];
  for (const [id, pattern] of rules) {
    if (pattern.test(convention)) return result(id, `По соглашению об имени: ${path || project}`, 'convention');
  }
  return result('unknown', 'Явная роль и подходящее соглашение об имени отсутствуют.', 'unknown');
}

/** Считает направленные связи видимой карты; вес агрегированного ребра сохраняется. */
export function codeConnectionCounts(edges) {
  const counts = new Map();
  for (const edge of edges) {
    const source = String(edge.source), target = String(edge.target);
    if (!counts.has(source)) counts.set(source, { incoming: 0, outgoing: 0 });
    if (!counts.has(target)) counts.set(target, { incoming: 0, outgoing: 0 });
    const weight = Number(edge.weight);
    const amount = edge.weight != null && Number.isFinite(weight) && weight >= 0 ? weight : 1;
    counts.get(source).outgoing += amount;
    counts.get(target).incoming += amount;
  }
  return counts;
}
