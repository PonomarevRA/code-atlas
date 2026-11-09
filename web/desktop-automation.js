import { activityOperations } from './agent-activity-model.js';

/** Geometry is restricted to the controls that make up the map and history workflow. */
function bounds(element) {
  if (!element || element.hidden || !element.getClientRects().length) return null;
  const { x, y, width, height } = element.getBoundingClientRect();
  return { x, y, width, height };
}

function focusTarget(document, activity) {
  const active = document.activeElement;
  if (activity && active === activity.part('toggle')) return 'activity-launcher';
  if (activity && active === activity.part('query')) return 'history-query';
  if (activity && active && activity.panel.contains(active)) return 'history-panel';
  if (active?.id === 'graph' || active?.id === 'workspace-map-content') return 'map';
  return 'other';
}

const mapScopeStorageKey = 'atlas-desktop-map-scope';

/** Reads only bounded project and snapshot identifiers from this window's prior map. */
export function storedDesktopMapScope(storage) {
  try {
    const stored = storage().getItem(mapScopeStorageKey);
    if (!stored || stored.length > 2048) return {};
    const scope = JSON.parse(stored);
    if (validText(scope?.projectId, 256) && validText(scope?.snapshotId, 256)) {
      return { projectId: scope.projectId, snapshotId: scope.snapshotId };
    }
  } catch { /* The reference page still works with storage disabled. */ }
  return {};
}

/** Captures the prior committed map for returning from a reference page. */
export function rememberDesktopMapScope(storage, readScope) {
  try {
    const { projectId, snapshotId } = readScope();
    if (validText(projectId, 256) && validText(snapshotId, 256)) {
      storage().setItem(mapScopeStorageKey, JSON.stringify({ projectId, snapshotId }));
    } else {
      storage().removeItem(mapScopeStorageKey);
    }
  } catch { /* The reference page still works with storage disabled. */ }
}

function mismatchedScope(command, state) {
  return (command.projectId != null && command.projectId !== state.projectId) ||
    (command.snapshotId != null && command.snapshotId !== state.snapshotId);
}

function validText(value, maximum) {
  return typeof value === 'string' && value.length > 0 && value.length <= maximum;
}

/**
 * The desktop host supplies the transport and checks the current origin. This
 * bridge accepts named Atlas actions and exposes no DOM selectors or user text.
 */
export function createDesktopAutomation({ document, location, activity, readScope, openProject, openNode, returnToMap }) {
  let running = false;
  const documentRevision = globalThis.crypto?.randomUUID?.() ?? String(performance.timeOrigin);

  async function inspect() {
    const scope = readScope?.() || {};
    const historyEvents = activity?.historyPage?.events;
    return {
      projectId: scope.projectId ?? null,
      snapshotId: scope.snapshotId ?? null,
      nodeId: scope.nodeId ?? null,
      route: { path: location.pathname, view: scope.view ?? null },
      loading: Boolean(scope.loading),
      revision: { document: documentRevision, map: scope.revision ?? null },
      history: {
        open: Boolean(activity?.isOpen()),
        mode: activity?.historyPage ? 'history' : 'live',
        busy: Boolean(activity?.historyAbort),
        filtersOpen: Boolean(activity?.filtersOpen),
        resultCount: historyEvents ? activityOperations(historyEvents).length : null,
      },
      focus: { target: focusTarget(document, activity) },
      viewport: {
        width: document.documentElement?.clientWidth ?? null,
        height: document.documentElement?.clientHeight ?? null,
      },
      layout: {
        map: bounds(document.getElementById('graph')),
        leftPanel: bounds(document.getElementById('workspace-left')),
        rightPanel: bounds(document.getElementById('workspace-right')),
        history: bounds(activity?.panel),
        historyQuery: bounds(activity?.part('query')),
        referenceNavigation: bounds(document.querySelector('.atlas-reference-rail')),
      },
    };
  }

  /** Verifies the committed project before reporting success. */
  async function openRequestedProject(command, signal) {
    if (!await openProject(command.projectId, command.snapshotId, signal)) return 'project_not_opened';
    const state = await inspect();
    return mismatchedScope(command, state) ? 'stale_context' : null;
  }

  /** Opens or filters the journal using the same load and focus handlers as the UI. */
  async function showHistory(command, filtering, signal) {
    if (!activity) return 'map_required';
    if (!activity.projectId) return 'project_required';
    if (activity.historyAbort || activity.clearing) return 'history_busy';
    if (filtering && (typeof command.query !== 'string' || command.query.length > 512)) return 'invalid_query';
    if (!activity.isOpen()) activity.action('toggle');
    if (filtering) {
      if (!activity.filtersOpen) activity.action('filters');
      const input = activity.part('query');
      input.value = command.query;
      input.focus({ preventScroll: true });
    }
    return await activity.loadHistory(undefined, { signal }) ? null : 'history_not_loaded';
  }

  /** Dispatches allowlisted commands to existing project, node and journal controllers. */
  async function dispatch(command, signal) {
    const initial = await inspect();
    if (signal.aborted) return 'action_timeout';
    if (command.action === 'open_project') {
      if (!openProject) return 'map_required';
      if (initial.loading) return 'window_busy';
      return mismatchedScope(command, initial) ? openRequestedProject(command, signal) : null;
    }
    if (mismatchedScope(command, initial)) return 'stale_context';
    switch (command.action) {
      case 'open_node': {
        if (!openNode) return 'map_required';
        if (!validText(command.nodeId, 1024)) return 'invalid_node';
        if (!await openNode(command.nodeId, signal)) return 'node_not_opened';
        const state = await inspect();
        if (mismatchedScope(initial, state) || initial.revision.map !== state.revision.map) return 'stale_context';
        return state.nodeId === command.nodeId ? null : 'node_not_opened';
      }
      case 'open_history':
      case 'filter_history': {
        const error = await showHistory(command, command.action === 'filter_history', signal);
        const state = await inspect();
        if (mismatchedScope(initial, state) || initial.revision.map !== state.revision.map) return 'stale_context';
        return error;
      }
      case 'close_history':
        if (!activity) return 'map_required';
        activity.action('close');
        return focusTarget(document, activity) === 'activity-launcher' ? null : 'focus_not_restored';
      case 'return_to_map': {
        const navigation = await returnToMap?.();
        return navigation ?? (focusTarget(document, activity) === 'map' ? null : 'focus_not_restored');
      }
      default:
        return 'unknown_action';
    }
  }

  /** Bounds the command lifetime and reports state after the action has settled. */
  async function execute(command) {
    const result = async outcome => typeof outcome === 'string'
      ? { status: 'error', error: outcome, state: await inspect() }
      : { status: 'ok', ...outcome, state: await inspect() };
    if (!command || typeof command.action !== 'string') return result('invalid_command');
    if (!validText(command.projectId, 256) || !validText(command.snapshotId, 256)) return result('scope_required');
    if (running) return result('window_busy');
    running = true;
    const controller = new AbortController();
    let timer;
    try {
      const timeout = new Promise(resolve => {
        timer = setTimeout(() => {
          resolve('action_timeout');
          controller.abort();
        }, 18000);
      });
      return await result(await Promise.race([dispatch(command, controller.signal), timeout]));
    } catch {
      return await result('action_failed');
    } finally {
      clearTimeout(timer);
      running = false;
    }
  }

  return Object.freeze({ inspect, execute });
}
