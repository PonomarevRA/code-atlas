const SUCCESS_DURATION = 8000;

/** Временный результат экспорта не закрывается, пока человек читает ссылку мышью или клавиатурой. */
export function createExportNotice(status, timers = globalThis) {
  let timer = null, transient = false;
  const cancel = () => { if (timer !== null) timers.clearTimeout(timer); timer = null; };
  const hide = () => { cancel(); transient = false; status.hidden = true; status.textContent = ''; };

  /** Новый таймер относится только к последнему успешному экспорту; ошибки остаются на экране. */
  function resume() {
    cancel();
    if (!transient || status.hidden || status.matches?.(':hover, :focus-within')) return;
    timer = timers.setTimeout(hide, SUCCESS_DURATION);
  }

  /** Явное закрытие доступно также для ошибок и не зависит от автоматического таймера. */
  function addClose() {
    const close = status.ownerDocument.createElement('button');
    close.type = 'button'; close.textContent = '×';
    close.setAttribute('aria-label', 'Закрыть уведомление об экспорте');
    close.addEventListener('click', hide);
    status.append(close);
  }

  status.addEventListener('pointerenter', cancel);
  status.addEventListener('pointerleave', resume);
  status.addEventListener('focusin', cancel);
  status.addEventListener('focusout', resume);
  return {
    start() { cancel(); transient = false; status.hidden = false; },
    success() { transient = true; addClose(); resume(); },
    error() { cancel(); transient = false; addClose(); },
  };
}
