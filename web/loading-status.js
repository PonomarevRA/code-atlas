import {subscribeLoading} from './loading-state.js';

const mounted = new WeakMap();
const messages = {
  ru: {name:'Atlas загружает данные', more:'ещё', pause:'Без анимации', continuation:'Уже загруженные данные доступны.'},
  en: {name:'Atlas is loading data', more:'more', pause:'Pause animation', continuation:'Loaded content remains available.'},
};

/**
 * Монтирует один неблокирующий статус для документа; повторный mount безопасен.
 *
 * document — владелец DOM; delayMs сглаживает быстрые запросы, не задерживая их.
 * Используются нативный indeterminate progress и видимый role=status. Ни фокус,
 * ни доступность имеющегося содержимого не меняются. Возвращает dispose().
 */
export function mountLoadingStatus(document, {delayMs = 120} = {}) {
  if (mounted.has(document)) return mounted.get(document);
  if (!document?.body) return {dispose() {}};
  const view = document.defaultView;
  const style = document.createElement('link');
  style.rel = 'stylesheet';
  style.href = new URL('./loading-status.css', import.meta.url).href;
  style.dataset.atlasLoadingStyle = '';
  document.head.append(style);
  const root = document.createElement('aside');
  root.className = 'atlas-loading-status';
  root.dataset.atlasLoadingStatus = '';
  root.hidden = true;
  root.setAttribute('data-navigation-ignore', '');
  const progress = document.createElement('progress');
  progress.className = 'atlas-loading-status__progress';
  progress.setAttribute('aria-hidden', 'true');
  // value намеренно отсутствует: длительность и проценты операции неизвестны.
  const text = document.createElement('span');
  text.setAttribute('role', 'status');
  text.setAttribute('aria-live', 'polite');
  text.setAttribute('aria-atomic', 'true');
  const pause = document.createElement('button');
  pause.type = 'button';
  pause.className = 'atlas-loading-status__pause';
  pause.setAttribute('aria-pressed', 'false');
  root.append(progress, text, pause);
  document.body.append(root);
  let timer = null, disposed = false, paused = false, current = {count:0};
  let previousFocus = document.activeElement;
  const rememberFocus = event => {if (!root.contains?.(event.target)) previousFocus = event.target;};
  document.addEventListener('focusin', rememberFocus);
  pause.addEventListener('click', () => {
    paused = !paused;
    root.dataset.motion = paused ? 'paused' : 'playing';
    pause.setAttribute('aria-pressed', String(paused));
  });

  /** Перерисовывает только подпись; карта, фокус и камера принадлежат своим контроллерам. */
  function render() {
    if (disposed || !current.count) return;
    const locale = document.documentElement.lang?.startsWith('en') ? 'en' : 'ru';
    const dictionary = messages[locale];
    const label = typeof current.label === 'string' ? current.label : current.label[locale] || current.label.ru;
    const message = label + (current.count > 1 ? ` · +${current.count - 1} ${dictionary.more}` : '');
    if (text.textContent !== message) text.textContent = message;
    root.setAttribute('aria-label', dictionary.name);
    root.title = dictionary.continuation;
    pause.textContent = dictionary.pause;
    root.dataset.count = String(current.count);
    root.hidden = false;
  }

  /** Скрывает завершённую работу немедленно, не имитируя дальнейшую загрузку. */
  function update(snapshot) {
    current = snapshot;
    if (!snapshot.count) {
      view.clearTimeout(timer); timer = null;
      if (root.contains?.(document.activeElement) && previousFocus?.isConnected) previousFocus.focus({preventScroll:true});
      root.hidden = true;
      root.dataset.count = '0';
      text.textContent = '';
    } else if (!root.hidden) render();
    else if (timer === null) timer = view.setTimeout(() => {timer = null; render();}, delayMs);
  }
  const unsubscribe = subscribeLoading(update);
  const localize = () => {if (!root.hidden) render();};
  const onHide = event => {if (!event.persisted) api.dispose();};
  document.addEventListener('atlas-locale-change', localize);
  view.addEventListener('pagehide', onHide);
  const api = {
    /** Освобождает только собственные DOM, таймер и подписку; не отменяет работу приложения. */
    dispose() {
      if (disposed) return;
      disposed = true;
      view.clearTimeout(timer);
      unsubscribe();
      document.removeEventListener('atlas-locale-change', localize);
      document.removeEventListener('focusin', rememberFocus);
      view.removeEventListener('pagehide', onHide);
      root.remove(); style.remove(); mounted.delete(document);
    },
  };
  mounted.set(document, api);
  return api;
}
