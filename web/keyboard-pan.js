const NORMAL_STEP = 64;
const FAST_STEP = 192;
const EDITABLE_TAGS = new Set(['input', 'textarea', 'select']);

/** Проверяет поле ввода и редактируемых родителей, включая содержимое foreignObject внутри SVG. */
function editableTarget(target, map) {
  if (target.isContentEditable) return true;
  let current = target;
  while (current) {
    const tag = String(current.tagName || '').toLowerCase();
    if (EDITABLE_TAGS.has(tag)) return true;
    const editable = current.getAttribute?.('contenteditable');
    if (editable != null && editable !== 'false') return true;
    if (current.getAttribute?.('role') === 'textbox') return true;
    if (current === map) break;
    current = current.parentElement;
  }
  return false;
}

/**
 * Возвращает смещение содержимого карты в экранных пикселях для стрелок внутри сфокусированной карты.
 * Направление соответствует перемещению обзора: ArrowRight сдвигает содержимое влево.
 * Shift ускоряет шаг втрое; Alt/Ctrl/Meta, поля ввода и уже обработанные события остаются владельцу.
 * Функция не меняет DOM, камеру или событие и возвращает null для неподходящей команды.
 */
export function keyboardPanDelta(event, map) {
  if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return null;
  const target = event.target;
  if (!target || (target !== map && !map.contains?.(target)) || editableTarget(target, map)) return null;
  const step = event.shiftKey ? FAST_STEP : NORMAL_STEP;
  switch (event.key) {
    case 'ArrowLeft': return { x: step, y: 0 };
    case 'ArrowRight': return { x: -step, y: 0 };
    case 'ArrowUp': return { x: 0, y: step };
    case 'ArrowDown': return { x: 0, y: -step };
    default: return null;
  }
}
