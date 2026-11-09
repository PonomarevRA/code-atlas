/** Следит за серверной задачей: сетевой сбой не означает её завершение и не разрешает повторный build. */
export async function monitorJob(job, { read, isCurrent, onState, onTransientError, wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds)) }) {
  let failures = 0;
  while (isCurrent(job.id)) {
    let current;
    try {
      current = await read(job.id);
      failures = 0;
    } catch (error) {
      if (!isCurrent(job.id)) return;
      failures++;
      const delay = Math.min(15000, 1500 * 2 ** Math.min(failures, 4));
      onTransientError(error, delay);
      await wait(delay);
      continue;
    }
    if (!isCurrent(job.id)) return;
    await onState(current);
    if (current.status === 'completed' || current.status === 'failed') return;
    await wait(1500);
  }
}
