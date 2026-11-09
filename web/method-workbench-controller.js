import { MethodWorkbench } from './method-workbench.js';
import { focusCanvasOccurrence } from './canvas-viewport.js';

/** Connect local reading and explicit diagram returns while preserving selection, layout and expansions. */
export function createMethodWorkbench({ root, state, view, onSource, onRefreshMap, checkpoint, commit, sync, status }) {
  let opener = null, originCamera = null, restoreRevision = 0;
  const context = () => ({ projectId: state.project?.id, snapshotId: state.graph.metadata?.snapshotId,
    entryNodeId: state.pathOnGraph?.rootNodeId });
  const reader = new MethodWorkbench(root, {
    getContext: context, getNode: id => state.nodes.get(id), getGraph: () => state.graph,
    getDescription: id => state.pathOnGraph?.snapshotId === context().snapshotId
      ? state.pathOnGraph.steps?.find(step => step.nodeId === id) : null,
    onSource, onRefreshMap,
    onReturnToCall: origin => returnToDiagram(origin, false),
    onReturnToEntry: origin => returnToDiagram(origin, true),
    onChange: (address, options) => { state.readerState = address; state.methodPresentation = 'methods'; sync(); commit(options); },
    onClose: origin => {
      state.readerState = null; state.methodPresentation = origin?.originMode || 'diagram'; sync(); commit();
      if (opener?.isConnected) opener.focus({ preventScroll: true });
    },
  });
  /** Scene occurrences keep the caller/line; raw method actions use their own declaration as origin. */
  function open(node, button) {
    const targets = [...new Set([...(node.implementationItems || []).map(item => item.id),
      ...(node.readTargetIds || []), ...(node.targetIds || []), ...(node.readTargetId ? [node.readTargetId] : [])])];
    if (!targets.length) targets.push(node.rawId || node.id);
    const ownerNodeId = node.sourceOwnerId || node.ownerNodeId || node.rawId || node.id;
    if (!context().snapshotId || !targets.some(id => state.nodes.has(id))) return;
    restoreRevision++; checkpoint(); opener = button || document.activeElement; originCamera = { ...view.camera };
    reader.open({ ...node, ownerNodeId, occurrenceId: node.id || ownerNodeId, targetIds: targets,
      originMode: state.methodPresentation === 'documentation' ? 'documentation' : 'diagram',
      expression: node.sourceExpression || node.sourceStatement || node.label });
    reader.find('diagram').focus({ preventScroll: true });
  }
  /** Restore reading's retained map, then address the concrete call or entry without fitting or expanding. */
  function returnToDiagram(origin, entry) {
    restoreRevision++; checkpoint();
    state.readerState = null; state.methodPresentation = 'diagram'; sync();
    const id = entry ? origin.entryNodeId : origin.occurrenceId;
    const focused = focusCanvasOccurrence(view, id, { camera: originCamera, center: entry });
    if (!focused) status('Место вызова отсутствует в текущем раскрытии. Контекст карты сохранён.', true);
    commit();
  }
  /** Revealing the retained diagram requires no new analysis or camera fitting. */
  function leave() { restoreRevision++; reader.invalidate(); state.readerState = null; }
  /** Navigation restores a pinned local address only after the caller's diagram is ready. */
  async function restore(snapshot, { isCurrent = () => true } = {}) {
    if (!isCurrent()) return;
    if (snapshot.route.reading !== 'methods') { leave(); sync(); return; }
    const revision = ++restoreRevision;
    originCamera = { ...view.camera };
    if (snapshot.reader) await reader.restore(snapshot.reader);
    else {
      const id = snapshot.route.methodFocus || snapshot.route.node || snapshot.route.path;
      if (!state.nodes.has(id)) { leave(); state.methodPresentation = 'diagram'; sync(); return; }
      await reader.open({ id, ownerNodeId: id, occurrenceId: id, targetIds: [id] }, { notify: false });
    }
    if (revision !== restoreRevision) return;
    if (!isCurrent()) { leave(); sync(); return; }
    state.readerState = reader.snapshot(); state.methodPresentation = 'methods'; sync();
  }
  /** Changing repository/revision closes old facts; no stale source response can reopen the panel. */
  function checkContext() {
    const wasActive = reader.active; reader.sync();
    if (wasActive && !reader.active) {
      state.readerState = null; state.methodPresentation = 'diagram';
      status('Снимок изменился: чтение метода закрыто. Откройте метод по текущей карте.', true);
    }
  }
  return { reader, open, leave, restore, checkContext, snapshot: () => reader.snapshot() };
}
