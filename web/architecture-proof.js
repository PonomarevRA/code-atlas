/** Draws only the current evidence page. Text and keyboard actions carry the same directed facts. */
import {connectionCurve} from './edge-curves.js';
const NS = 'http://www.w3.org/2000/svg';

/** Produce a bounded sequential witness diagram; repeated files remain separate occurrences. */
export function renderProofMap(host, edges, selectedIndex = -1) {
  const document = host.ownerDocument;
  const svg = document.createElementNS(NS, 'svg');
  const height = Math.max(72, edges.length * 96 + 20);
  svg.setAttribute('width', '760'); svg.setAttribute('height', String(height));
  svg.setAttribute('viewBox', `0 0 760 ${height}`); svg.setAttribute('aria-hidden', 'true');
  const make = (tag, attrs, text) => {
    const element = document.createElementNS(NS, tag);
    for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, String(value));
    if (text !== undefined) element.textContent = text;
    return element;
  };
  for (let index = 0; index < edges.length; index++) {
    const edge = edges[index], y = index * 96 + 18;
    const from = {x:310, y:y + 27}, to = {x:450, y:y + 27};
    svg.append(make('rect', {x:10,y,width:300,height:54,rx:6}), make('rect', {x:450,y,width:300,height:54,rx:6}));
    for (const [x, text] of [[20,edge.source], [460,edge.target]]) {
      const label = make('text', {x,y:y + 32}, text.length > 34 ? text.slice(0,31) + '…' : text);
      label.append(make('title', {}, text)); svg.append(label);
    }
    const path = make('path', {d:connectionCurve(from,to),class:index === selectedIndex ? 'selected' : ''});
    const tip = make('path', {d:`M ${to.x-10} ${to.y-6} L ${to.x} ${to.y} L ${to.x-10} ${to.y+6}`});
    svg.append(path, tip, make('text', {x:320,y:y + 68}, `${index + 1}. ${edge.relation}`));
  }
  host.replaceChildren(svg);
}
