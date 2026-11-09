const NS = 'http://www.w3.org/2000/svg';
export const CODE_COLORS = Object.freeze({ code: '#236b78', string: '#975614', literal: '#7953a1', keyword: '#365fb0' });

/** Lexical colour only: never infer types, evaluate expressions or insert source HTML. */
export function codeTokens(value) {
  const source = String(value || ''), tokens = [];
  const pattern = /(?:\$?@?"(?:""|\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|\b(?:null|true|false|None|True|False)\b|\b(?:return|throw|await|new|var|if|else|for|foreach|while|switch|case|default)\b|\b\d+(?:\.\d+)?(?:[mMdDfF])?\b/g;
  let offset = 0;
  for (const match of source.matchAll(pattern)) {
    if (match.index > offset) tokens.push({ text: source.slice(offset, match.index), kind: 'code' });
    const text = match[0], kind = /^[\$@"']/.test(text) ? 'string'
      : /^(?:null|true|false|None|True|False|\d)/.test(text) ? 'literal' : 'keyword';
    tokens.push({ text, kind }); offset = match.index + text.length;
  }
  if (offset < source.length) tokens.push({ text: source.slice(offset), kind: 'code' });
  return tokens;
}

/** Safe tspans preserve source spacing; descriptions use ordinary text outside this helper. */
export function appendCodeText(group, attributes, value) {
  const element = document.createElementNS(NS, 'text');
  for (const [name, content] of Object.entries(attributes)) element.setAttribute(name, content);
  element.setAttribute('xml:space', 'preserve');
  element.setAttribute('data-code-text', String(value));
  for (const token of codeTokens(value)) {
    const span = document.createElementNS(NS, 'tspan');
    span.setAttribute('fill', CODE_COLORS[token.kind]); span.setAttribute('data-token-kind', token.kind);
    span.textContent = token.text; element.append(span);
  }
  group.append(element);
  return element;
}
