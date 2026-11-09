/** Filename discovery enables an explicit entry into a separate data layer. */
/** Поздний ответ старого проекта не должен показывать ссылку на чужие файлы. */
export function projectDataMaps(button) {
  let revision = 0;
  return async project => {
    const current = ++revision;
    if (!button) return;
    button.hidden = true;
    if (!project?.id) return;
    try {
      const response = await fetch(`/api/projects/${encodeURIComponent(project.id)}/data-files`);
      if (!response.ok) return;
      const result = await response.json();
      if (revision !== current || !result.files?.length) return;
      button.href = `/data.html?project=${encodeURIComponent(project.id)}`;
      button.textContent = `Посмотреть карту данных (${result.files.length})`;
      button.hidden = false;
    } catch { /* Discovery failure does not interrupt the code map. */ }
  };
}
