import { ensureCanvasVisible } from './canvas-viewport.js';

/** Read the unobscured canvas in local CSS pixels; standalone views use their full SVG. */
function canvasRect(view) {
  return view.getCanvasViewportRect?.() || { x: 0, y: 0,
    width: view.svg.clientWidth, height: view.svg.clientHeight };
}

/** Reveal one disclosure without changing zoom or fitting the entire graph. */
export function revealProgressiveDetails(view, occurrenceId, automatic = false) {
  const entryId = view.progressiveDetails?.get(occurrenceId), entry = view.positions?.get(entryId);
  const caller = view.positions?.get(occurrenceId);
  if (!entry || !caller) return false;
  const canvas = canvasRect(view), { width, height } = canvas, scale = view.camera.scale;
  if (width <= 64 || height <= 64) return false;
  const area = view.progressiveAreas?.get(occurrenceId);
  // A short chooser fits as a whole at the current zoom; large bodies reveal only their entry.
  const chooser = area?.kind === 'candidates' && area.height * scale <= height - 64 ? area : null;
  const callerW = caller.w || caller.headerBounds?.width || caller.width, callerH = caller.h || caller.headerBounds?.height || caller.height;
  const left = Math.min(caller.x - callerW / 2, entry.x - entry.w / 2) * scale + view.camera.x;
  const right = Math.max(caller.x + callerW / 2, entry.x + entry.w / 2) * scale + view.camera.x;
  const top = Math.min(caller.y - callerH / 2, chooser?.y ?? entry.y - entry.h / 2) * scale + view.camera.y;
  const bottom = Math.max(caller.y + callerH / 2, chooser ? chooser.y + chooser.height : entry.y + entry.h / 2) * scale + view.camera.y;
  if (automatic && (right - left > width - 64 || bottom - top > height - 64)) return false;
  const previous = { ...view.camera };
  if (automatic) {
    const minX = canvas.x + 32, maxX = canvas.x + width - 32;
    const minY = canvas.y + 32, maxY = canvas.y + height - 32;
    view.camera.x += right > maxX ? maxX - right : left < minX ? minX - left : 0;
    view.camera.y += bottom > maxY ? maxY - bottom : top < minY ? minY - top : 0;
  } else {
    view.camera.x = canvas.x + width / 2 - entry.x * scale;
    view.camera.y = canvas.y + height / 2 - (chooser ? chooser.y + chooser.height / 2 : entry.y) * scale;
  }
  view.transform();
  if (!globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches) view.viewport.animate?.([
    { transform: `translate(${previous.x}px,${previous.y}px) scale(${scale})` },
    { transform: `translate(${view.camera.x}px,${view.camera.y}px) scale(${scale})` },
  ], { duration: 280, easing: 'cubic-bezier(.2,.8,.2,1)' });
  view.onCameraChange?.(); return true;
}

/** Explicit readable focus changes only the local entry camera and records an exact return position. */
export function focusProgressiveDetails(view, occurrenceId) {
  const entryId = view.progressiveDetails?.get(occurrenceId);
  const entry = view.positions?.get(entryId);
  if (!entry || !view.camera || !view.svg) return false;
  ensureCanvasVisible(view.svg);
  const canvas = canvasRect(view);
  if (canvas.width <= 0 || canvas.height <= 0) return false;
  view.readableFocusTrail ||= [];
  view.readableFocusTrail.push({ occurrenceId, camera: { ...view.camera } });
  if (view.readableFocusTrail.length > 16) view.readableFocusTrail.shift();
  const scale = Math.max(view.camera.scale, 1);
  const area = view.progressiveAreas?.get(occurrenceId);
  let worldY = entry.y, screenY = canvas.y + canvas.height / 2;
  if (area?.kind === 'candidates') {
    if (area.height * scale <= canvas.height - 64) worldY = area.y + area.height / 2;
    else {
      // Keep the first choice near the top so the reader can continue down the inventory.
      const entryHeight = entry.h || entry.headerBounds?.height || entry.height || 0;
      screenY = canvas.y + 32 + entryHeight * scale / 2;
    }
  }
  view.camera = { ...view.camera, scale,
    x: canvas.x + canvas.width / 2 - entry.x * scale,
    y: screenY - worldY * scale };
  view.transform(); view.onCameraChange?.();
  view.onReadableFocusChange?.(view.readableFocusTrail.at(-1));
  return true;
}

/** Restore the latest explicit readable focus; ordinary expansion never adds a return frame. */
export function restoreProgressiveFocus(view, occurrenceId) {
  const frame = view.readableFocusTrail?.at(-1);
  if (!frame || frame.occurrenceId !== occurrenceId) return false;
  view.readableFocusTrail.pop();
  view.camera = { ...frame.camera };
  view.transform(); view.onCameraChange?.();
  view.onReadableFocusChange?.(view.readableFocusTrail.at(-1) || null);
  return true;
}
