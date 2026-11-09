/** Literal, case-sensitive line search with a fixed result cap; source is never a regular expression. */
export function matchingLines(text, query, limit = 1000) {
  const result = []; if (!query || query.length > 200) return result;
  const lines = String(text).split(/\r\n|\r|\n/);
  for (let index = 0; index < lines.length && result.length < limit; index++) if (lines[index].includes(query)) result.push(index + 1);
  return result;
}

/** Map only known grammar token classes into DOM. Lexical coloring never becomes method binding. */
export function appendSourceTokens(code, data, line) {
  const values = data.syntax?.status === 'ready' ? data.syntax.lines?.[line.number - 1] : null;
  if (!Array.isArray(values) || values.map(value => value.text).join('') !== line.text) { code.textContent = line.text || ' '; return; }
  for (const value of values) {
    const span = document.createElement('span');
    const kind = ['keyword','string','number','comment','function','type','operator'].includes(value.kind) ? value.kind : 'code';
    span.className = `source-token-${kind}`; span.textContent = value.text; code.append(span);
  }
  if (!values.length) code.textContent = ' ';
}

/** A line is accepted only when it exists; stale indexed positions are not silently clamped. */
export function validSourceLine(value, total) {
  const text = String(value).trim(); if (!/^\d+$/.test(text)) return null;
  const line = Number(text); return Number.isSafeInteger(line) && line >= 1 && line <= total ? line : null;
}
