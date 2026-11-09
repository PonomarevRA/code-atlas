import { colorSchemes, colorSchemePicker } from './color-scheme.js';
import { requestJson } from './bounded-request.js';
import { el } from './dom.js';
const themes = colorSchemes(); await themes.ready;
const form = document.getElementById('scheme-editor'), fields = document.getElementById('scheme-colors');
const status = document.getElementById('scheme-status');
await colorSchemePicker(document.getElementById('scheme-selection'));
const labels = {background:'Фон приложения',surface:'Панели',surfaceAlt:'Вложенные блоки',text:'Основной текст',muted:'Второстепенный текст',border:'Границы',accent:'Акцент',accentSoft:'Акцентный фон',onAccent:'Текст на акценте',focus:'Клавиатурный фокус',canvas:'Фон карты',dot:'Сетка карты',card:'Фон карточки',cardHeader:'Заголовок карточки',cardBorder:'Граница карточки',edge:'Связи',selection:'Выделенная карточка',control:'Фон условия',controlText:'Текст условия',waiting:'Фон ожидания',waitingText:'Текст ожидания',warning:'Предупреждение',warningSoft:'Фон предупреждения',error:'Ошибка',errorSoft:'Фон ошибки',keyword:'Ключевые слова',string:'Строки',number:'Числа',comment:'Комментарии',code:'Код и имена',type:'Типы'};

/** Copy a resolved palette into editable controls; switching the application does not discard a draft. */
function copySelected() {
  const scheme = themes.selected;
  if (!scheme.tokens) { status.textContent = 'Каталог схем недоступен. Перезагрузите страницу.'; return; }
  form.elements.base.value = scheme.base;
  fields.replaceChildren();
  for (const [token, value] of Object.entries(scheme.tokens)) {
    const label = el('label', labels[token] || token), input = el('input');
    input.type = 'color'; input.name = token; input.value = value;
    label.append(input); fields.append(label);
  }
}

/** The exported package contains JSON declarations only; the host validates again on installation. */
function draft() {
  const tokens = {};
  for (const input of fields.querySelectorAll('input')) tokens[input.name] = input.value;
  return { id: form.elements.schemeId.value.trim(), label: form.elements.schemeLabel.value.trim(), base: form.elements.base.value, tokens };
}

/** Create a new disabled package, explicitly activate its fingerprint, then select its new palette. */
async function save(event) {
  event.preventDefault();
  const button = document.getElementById('scheme-save'); button.disabled = true;
  try {
    const scheme = draft();
    const mod = await requestJson('/api/color-schemes', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(scheme) });
    await requestJson(`/api/mods/${encodeURIComponent(mod.id)}/activation`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({enabled:true,fingerprint:mod.fingerprint}) });
    await themes.refresh(); themes.apply({ id:`mod:${mod.id}:${scheme.id}` });
    try { localStorage.setItem('atlas-mods-revision', String(Date.now())); } catch { /* Other pages can reload manually. */ }
    status.textContent = `Схема «${scheme.label}» включена. Мод сохранён в ${mod.id}/mod.json.`;
  } catch (error) { status.textContent = error.message; }
  finally { button.disabled = false; }
}

/** Download an editable standalone mod manifest without sending it to an external service. */
function download() {
  if (!form.reportValidity()) return;
  const scheme = draft(), manifest = {apiVersion:1,id:'theme-'+scheme.id,name:scheme.label,version:'1.0.0',colorSchemes:[scheme]};
  const url = URL.createObjectURL(new Blob([JSON.stringify(manifest,null,2)+'\n'], {type:'application/json'}));
  const link = el('a'); link.href = url; link.download = 'mod.json'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
form.addEventListener('submit', save);
document.getElementById('scheme-copy').addEventListener('click', copySelected);
document.getElementById('scheme-download').addEventListener('click', download);
copySelected();
