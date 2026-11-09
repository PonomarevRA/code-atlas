/**
 * Объясняет отсутствие raw-графа, сохраняя выбранную регистрацию и различая неудачу/сборку/пустой снимок.
 * @param {HTMLElement} root Существующая область с h2, p и одной native кнопкой.
 * @param {object} context hasGraph/loading/project/error; systemContext указывает собственную панель архитектуры.
 * @returns {void} Меняет только текст/видимость. Действие rebuild всегда требует явного нажатия.
 */
export function renderMapEmptyState(root, {hasGraph, loading, project, error, systemContext = false}) {
  root.hidden = hasGraph || loading || systemContext;
  if (root.hidden) return;
  let title = 'Выберите проект';
  let text = 'Откройте сохранённый проект слева или добавьте папку. Затем начните с обзора проекта.';
  let label = 'Добавить папку проекта';
  let action = 'add';
  const noSnapshot=project && (project.snapshotId===null || project.snapshotAvailable===false || (project.status==='failed'&&!project.snapshotId));
  if(project && noSnapshot){
    title=project.status==='failed'?'Карту проекта не удалось построить':project.snapshotId?'Снимок проекта недоступен':'Карта проекта ещё не построена';
    text=project.status==='failed' ? 'Проект уже добавлен. '+(project.error||error||'Построение завершилось ошибкой.')+' Доступного снимка нет.'
      :'Регистрация и сохранённый путь проекта доступны. Постройте карту, чтобы исследовать код.';
    label='Построить карту по сохранённому пути';action='rebuild';
  } else if (project && error) {
    title = 'Не удалось открыть карту';text = error;label = 'Повторить загрузку';action = 'retry';
  } else if (project) {
    title = 'В сохранённой карте пока нет узлов';
    text = 'Снимок открыт, но моделируемые узлы отсутствуют. Это не доказывает отсутствие исходного кода.';
    label = 'Выбрать другой проект';action = 'choose';
  }
  root.querySelector('h2').textContent = title;
  root.querySelector('p').textContent = text;
  root.querySelector('button').textContent = label;
  root.dataset.action = action;
}
