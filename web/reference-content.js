/** Ограниченное представление DocFX/Markdown. Данные документа никогда не становятся HTML. */
import {el} from './dom.js';

/** UID остаётся точным для поиска; displayProperty влияет только на подпись. */
export function xrefIdentity(value, caption = '') {
  let decoded;
  try { decoded = decodeURIComponent(value); } catch { decoded = value; }
  const [identity, query = ''] = decoded.split('?', 2);
  const uid = identity.replace(/^(?:[TMPFEON]:|Overload:)/, '').replace(/\*$/, '');
  const display = new URLSearchParams(query).get('displayProperty');
  let label = caption || uid;
  if (!caption && display === 'name') label = uid.split('.').at(-1);
  if (!caption && display === 'nameWithType') label = uid.split('.').slice(-2).join('.');
  return {uid, label};
}

/** Ищет конец Markdown ссылки с вложенными скобками в DocId перегрузки. */
function markdownLink(text, start) {
  const captionEnd = text.indexOf('](', start + 1);
  if (captionEnd < 0) return null;
  let depth = 1, index = captionEnd + 2;
  for (; index < text.length && depth; index++) {
    if (text[index] === '(') depth++;
    if (text[index] === ')') depth--;
  }
  if (depth) return null;
  return {caption: text.slice(start + 1, captionEnd), target: text.slice(captionEnd + 2, index - 1), end: index};
}

/** Безопасные inline code и xref; неизвестные ссылки и HTML остаются видимым текстом. */
function inline(parent, text, onReference) {
  let plain = '', index = 0;
  const flush = () => { if (plain) parent.append(document.createTextNode(plain)); plain = ''; };
  while (index < text.length) {
    // Backtick перед числом в CLR имени (List`1) — arity, а не Markdown delimiter.
    const arity = /\w/.test(text[index - 1] || '') && /^`{1,2}\d/.test(text.slice(index));
    if (text[index] === '`' && !arity) {
      const marker = text.slice(index).match(/^`+/)[0], end = text.indexOf(marker, index + marker.length);
      if (end >= 0) {
        flush(); parent.append(el('code', text.slice(index + marker.length, end).trim()));
        index = end + marker.length; continue;
      }
    }
    const raw = text.startsWith('<xref:', index) ? text.slice(index).match(/^<xref:([^<>\s]+)>/) : null;
    const legacy = (index === 0 || !/\w/.test(text[index - 1]))
      ? text.slice(index).match(/^((?:[TMPFEON]|Overload):[A-Za-z_][\w.`{}+,#]*(?:\([^\s)]*\))?)/) : null;
    const link = text[index] === '[' ? markdownLink(text, index) : null;
    if (raw || legacy || link?.target.startsWith('xref:')) {
      let value, consumed;
      if (raw) { value = raw[1]; consumed = raw[0].length; }
      else if (legacy) { value = legacy[1].replace(/\.$/, ''); consumed = value.length; }
      else { value = link.target.slice(5); consumed = link.end - index; }
      const reference = xrefIdentity(value, link?.caption);
      flush();
      const node = el(onReference ? 'button' : 'code', reference.label, 'reference-xref');
      node.title = reference.uid;
      if (onReference) { node.type = 'button'; node.addEventListener('click', () => onReference(reference.uid)); }
      parent.append(node); index += consumed; continue;
    }
    if (link && /^https?:\/\//i.test(link.target)) {
      // Только явно заданный http(s) источник; относительные DocFX пути не угадываем.
      flush(); const node = el('a', link.caption);
      node.href = link.target; node.target = '_blank'; node.rel = 'noopener noreferrer';
      parent.append(node); index = link.end; continue;
    }
    plain += text[index++];
  }
  flush();
}

/** Markdown table показывает исходные значения, не выполняя встроенный HTML. */
function table(lines, start, onReference) {
  const node = el('table'), head = el('thead'), body = el('tbody');
  let index = start;
  while (index < lines.length && lines[index].includes('|') && lines[index].trim()) {
    if (index === start + 1) { index++; continue; }
    const row = el('tr');
    const cells = lines[index].trim().replace(/^\||\|$/g, '').split(/(?<!\\)\|/);
    for (const value of cells) { const cell = el(index === start ? 'th' : 'td'); inline(cell, value.trim().replace(/\\\|/g, '|'), onReference); row.append(cell); }
    (index === start ? head : body).append(row); index++;
  }
  node.append(head, body);
  const wrapper = el('div', undefined, 'reference-table'); wrapper.append(node);
  return {node: wrapper, end: index};
}

/** XML indentation внутри прозы не создаёт новый абзац; явные блоки остаются границами. */
function paragraphLines(lines, start) {
  const parts = [lines[start]];
  let end = start + 1;
  while (end < lines.length && lines[end].trim()
    && !/^\s*(?:#{1,6}\s|`{3,}|~{3,}|-\s|\d+\.\s|:::code\s|(?:Remarks|[Ee]xample)\s*:)/.test(lines[end])) parts.push(lines[end++]);
  return {text: parts.join('\n'), end};
}

/** Отображает одну ограниченную страницу; неполный code fence сохраняет оставшийся код. */
export function renderReferenceContent(container, text, {messages = {}, onReference} = {}) {
  container.replaceChildren();
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) { index++; continue; }
    const fence = line.match(/^\s*(`{3,}|~{3,})(.*)$/);
    if (fence) {
      const marker = fence[1], codeLines = [], language = fence[2].trim(); index++;
      while (index < lines.length) {
        const closing = lines[index].trim();
        if (closing[0] === marker[0] && closing.length >= marker.length && [...closing].every(char => char === marker[0])) { index++; break; }
        codeLines.push(lines[index++]);
      }
      const block = el('div', undefined, 'reference-code');
      if (language) block.append(el('small', language));
      const pre = el('pre'); pre.append(el('code', codeLines.join('\n'))); block.append(pre); container.append(block); continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      const label = heading[2].trim();
      const labels = {Remarks: messages.remarks, Examples: messages.examples};
      const node = el(`h${Math.min(6, heading[1].length + 1)}`);
      inline(node, labels[label] || label, onReference); container.append(node); index++; continue;
    }
    const legacySection = line.match(/^\s*(Remarks|[Ee]xample)\s*:\s*(.*)$/);
    if (legacySection) {
      const label = legacySection[1] === 'Remarks' ? messages.remarks || 'Remarks' : messages.examples || 'Examples';
      container.append(el('h3', label));
      const paragraph = paragraphLines(lines, index), body = el('p');
      inline(body, paragraph.text.replace(/^\s*(?:Remarks|[Ee]xample)\s*:\s*/, ''), onReference);
      container.append(body); index = paragraph.end; continue;
    }
    const snippet = line.match(/^\s*:::code\s+(.+?):::\s*$/);
    if (snippet) {
      const source = snippet[1].match(/\bsource="([^"]+)"/), language = snippet[1].match(/\blanguage="([^"]+)"/);
      const note = el('p', undefined, 'reference-snippet');
      note.append(document.createTextNode((messages.externalExample || 'External example source') + ': '), el('code', source?.[1] || snippet[1]));
      if (language) note.append(document.createTextNode(` (${language[1]})`));
      container.append(note); index++; continue;
    }
    if (line.includes('|') && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1] || '')) {
      const parsed = table(lines, index, onReference); container.append(parsed.node); index = parsed.end; continue;
    }
    const list = line.match(/^\s*(-|\d+\.)\s+(.+)$/);
    if (list) {
      const ordered = list[1] !== '-', node = el(ordered ? 'ol' : 'ul');
      if (ordered) node.start = parseInt(list[1], 10);
      while (index < lines.length) {
        const item = lines[index].match(ordered ? /^\s*\d+\.\s+(.+)$/ : /^\s*-\s+(.+)$/);
        if (!item) break;
        const child = el('li'); inline(child, item[1], onReference); node.append(child); index++;
      }
      container.append(node); continue;
    }
    const paragraph = el('p'), body = paragraphLines(lines, index);
    inline(paragraph, body.text, onReference); container.append(paragraph); index = body.end;
  }
}

/** Полный текст страницы доступен рядом с форматированным представлением для проверки. */
export function showReferencePage(text, options) {
  document.getElementById('document-text').textContent = text;
  const container = document.getElementById('document-content');
  const enabled = options.enabled !== false;
  container.hidden = !enabled;
  document.getElementById('document-raw').open = !enabled;
  if (enabled) renderReferenceContent(container, text, options);
}
