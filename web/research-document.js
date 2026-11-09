/** Text-only rendering for local research Markdown; never interprets project HTML. */
const element = (tag, text) => { const item = document.createElement(tag); if (text != null) item.textContent = text; return item; };

/** Minimal Markdown presentation uses text nodes; source HTML and script tags remain inert. */
export function renderResearchDocument(target, markdown) {
  target.replaceChildren();
  let code = null, fence = '';
  for (const line of markdown.split('\n')) {
    if (/^`{3,}/.test(line)) {
      if (!code) { fence = line; code = element('pre'); target.append(code); }
      else if (line === fence) code = null;
      else code.append(document.createTextNode(line + '\n'));
      continue;
    }
    if (code) { code.append(document.createTextNode(line + '\n')); continue; }
    const prose = line.replace(/\\([\\`*_{}\[\]<>#!|~])/g, '$1');
    const heading = /^(#{1,3})\s+(.+)$/.exec(prose);
    if (heading) target.append(element('h' + Math.min(heading[1].length + 1, 4), heading[2]));
    else if (line) target.append(element('p', prose));
  }
}

