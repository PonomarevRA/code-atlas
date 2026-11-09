/** Документация объявления имеет приоритет; комментарии тела метода сюда не попадают. */
export function declarationSummary(documentation) {
  if (documentation?.summary?.trim()) return documentation.summary.replace(/\s+/g, ' ').trim();
  if (documentation?.unsupported) return '';
  const comments = [];
  for (const comment of documentation?.comments || []) {
    const original = String(comment.text || '');
    const text = original.replace(/^\s*\/\*+|\*\/\s*$/g, '')
      .replace(/^\s*\/\/\/?\s?|^\s*\*\s?/gm, '').trim();
    // XML без summary не показываем как обычный комментарий.
    const xml = /^\s*\/\/\//.test(original) && /<\/?[a-z][^>]*>/i.test(text);
    if (text && !xml) comments.push(text);
  }
  return comments.join(' ').replace(/\s+/g, ' ').trim();
}

/** Переносит слова в ограниченное число строк; длинные идентификаторы режутся, полный текст остаётся в подсказке. */
function wrap(text, limit, maxLines) {
  const lines = [];
  let remaining = text;
  while (remaining && lines.length < maxLines) {
    if (remaining.length <= limit) { lines.push(remaining); break; }
    if (lines.length === maxLines - 1) { lines.push(`${remaining.slice(0, limit - 1).trimEnd()}…`); break; }
    const space = remaining.lastIndexOf(' ', limit);
    const end = space > limit / 2 ? space : limit;
    lines.push(remaining.slice(0, end)); remaining = remaining.slice(end).trimStart();
  }
  return lines;
}

/**
 * Комментарий метода и описание его класса показываются раздельно, без догадок о назначении.
 * Единые строки и высота используются раскладкой и SVG. Пустая документация не меняет размер.
 */
export function cardDocumentation(node, width = 260) {
  // Снимок хранит комментарии отдельно от live-description. Область fallback
  // не позволяет выдать описание содержащего класса за описание метода.
  const captured = String(node.comment || '').replace(/\s+/g, ' ').trim();
  const method = declarationSummary(node.documentation) || String(node.callableFacts?.description || '').trim() || (node.commentScope === 'declaration' ? captured : '');
  const owner = declarationSummary(node.typeContext?.documentation) || String(node.implementationComment || '').replace(/\s+/g, ' ').trim()
    || (node.commentScope === 'containing-type' ? captured : '');
  const texts = [];
  const member = node.memberFacts;
  if (member && ['method', 'constructor'].includes(member.kind)) {
    const qualifier = member.visibilityKind === 'naming-convention' ? 'соглашение имён' : member.visibilityKind === 'package-export' ? 'экспорт из пакета' : '';
    texts.push(`${member.visibility}${member.static ? ' · static' : ''}${qualifier ? ` · ${qualifier}` : ''}`);
  }
  if (method) texts.push(method);
  if (owner && owner !== method) texts.push(`О классе: ${owner}`);
  const lines = [], limit = Math.max(16, Math.floor((width - 24) / 6.5));
  for (const text of texts) lines.push(...wrap(text, limit, texts.length > 1 ? 2 : 3));
  return { text: texts.join('\n'), summary: method, ownerSummary: owner && owner !== method ? owner : '',
    lines, height: lines.length ? lines.length * 17 + 16 : 0 };
}
