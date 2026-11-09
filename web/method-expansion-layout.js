/** Dock method bodies left of their call, aligning the first entry without changing source-order edges. */
export function dockExpandedMethods(cell, call, methods, resume, { gap = 70, row = 52 } = {}) {
  const width = Math.max(0, ...methods.map(method => method.w));
  const first = methods[0]?.cards.find(card => card.id === methods[0].entry);
  const top = Math.max(0, call.h / 2 - (first?.y || 0));
  let y = top;
  for (const method of methods) {
    const x = width - method.w;
    for (const card of method.cards) { card.x += x; card.y += y; }
    for (const frame of method.frames) { frame.x += x; frame.y += y; }
    cell.cards.push(...method.cards); cell.frames.push(...method.frames);
    y += method.h + row;
  }
  call.x = width + (methods.length ? gap : 0) + call.w / 2;
  call.y = first?.y || call.h / 2;
  resume.x = call.x; resume.y = call.y + call.h / 2 + row + resume.h / 2;
  cell.cards.push(resume);
  cell.w = width + (methods.length ? gap : 0) + Math.max(call.w, resume.w);
  cell.h = Math.max(call.y + call.h / 2, resume.y + resume.h / 2, methods.length ? y - row : 0);
}
