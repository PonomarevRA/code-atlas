/** Wrap at source boundaries; retain spaces, literals and full evidence in the caller's tooltip. */
export function wrapDiagramText(value, columns, maxLines = 2) {
  const text = String(value || '').replace(/\r\n/g, '\n');
  const lines = [];
  let offset = 0;
  while (offset < text.length && lines.length < maxLines) {
    const newline = text.indexOf('\n', offset);
    const end = newline < 0 ? text.length : newline;
    let cut = Math.min(end, offset + columns);
    if (cut < end) {
      let quote = null, escaped = false, boundary = offset;
      for (let index = offset; index < cut; index++) {
        const character = text[index];
        if (escaped) { escaped = false; continue; }
        if (quote) {
          if (character === '\\') escaped = true;
          else if (character === quote) quote = null;
          continue;
        }
        if ('\"\'`'.includes(character)) quote = character;
        else if (/\s|[.,;(){}[\]]/.test(character)) boundary = index + 1;
      }
      if (boundary > offset) cut = boundary;
    }
    lines.push(text.slice(offset, cut));
    offset = cut === end && newline >= 0 ? end + 1 : cut;
  }
  const truncated = offset < text.length;
  if (truncated && lines.length) lines[lines.length - 1] = lines.at(-1).slice(0, columns - 1) + '…';
  return { lines, truncated };
}
