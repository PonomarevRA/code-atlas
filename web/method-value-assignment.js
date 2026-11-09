const MEMBER_PATH = /^[A-Za-z_$][\w$]*(?:(?:\?\.|\.)[A-Za-z_$][\w$]*)*$/;

/** Keep fallback calls, mutations and composite expressions as explicit source steps. */
function simpleFallback(expression) {
  return MEMBER_PATH.test(expression) || /^(?:\[\]|-?\d+(?:\.\d+)?[mMdDfF]?|"(?:[^"\\]|\\.)*")$/.test(expression);
}

/** Match only a simple binding and nullable member path; operators or calls outside this grammar stay separate. */
function assignmentParts(step) {
  if (step?.kind !== 'operation' || step.incomplete) return null;
  const source = step.sourceStatement || step.expression || step.label || '';
  const match = source.match(/^((?:(?:var|let|const)\s+)?[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*=\s*(.*?)\s*;?$/);
  if (!match) return null;
  const split = match[2].split('??');
  if (split.length > 2) return null;
  const path = split[0].trim(), fallback = split[1]?.trim();
  if (!MEMBER_PATH.test(path) || !path.includes('?.') || (fallback !== undefined && !simpleFallback(fallback))) return null;
  return { source, path, fallback, binding: match[1] };
}

/** Only two captured value arms prove this step has no separately represented call or unknown action. */
function nullableValues(step) {
  if (step?.kind !== 'branch' || !['?.', '??'].includes(step.controlKind)
    || step.incomplete || step.arms?.length !== 2) return null;
  const values = [];
  for (const arm of step.arms) {
    if (arm.exception || arm.steps?.length !== 1 || arm.steps[0].kind !== 'value' || arm.steps[0].incomplete) return null;
    values.push({ expression: arm.steps[0].expression || arm.steps[0].label,
      line: arm.steps[0].line, reuse: Boolean(arm.steps[0].reuseResult) });
  }
  return values;
}

/** Fold a complete member-only ?. / ?? chain and its same-line assignment, retaining the assignment's original index. */
export function compactValueAssignment(steps, start) {
  const branches = []; let end = start;
  while (end < steps.length && branches.length <= 12 && steps[end].line === steps[start].line && nullableValues(steps[end])) {
    branches.push(steps[end++]);
  }
  if (!branches.length || branches.length > 12 || steps[start].line <= 0 || steps[end]?.line !== steps[start].line) return null;
  const assignment = assignmentParts(steps[end]);
  if (!assignment) return null;
  const path = assignment.path.replaceAll('?.', '.');
  const members = assignment.path.split('?.');
  let accesses = 0, coalescing = false, fallbackLine = steps[end].line;
  for (const branch of branches) {
    const values = nullableValues(branch);
    if (branch.controlKind === '?.') {
      if (coalescing) return null;
      const selected = values.find(value => value.reuse), empty = values.find(value => !value.reuse);
      const prefix = selected?.expression?.replaceAll('?.', '.');
      const condition = branch.label?.match(/^(.*?)\s*!=\s*null$/)?.[1]?.replaceAll('?.', '.');
      const expectedCondition = members.slice(0, accesses + 1).join('.');
      if (empty?.expression !== 'null' || !prefix || condition !== expectedCondition
        || !(path === prefix || path.startsWith(prefix + '.'))
        || !prefix.startsWith(condition + '.')) return null;
      accesses++;
    } else {
      if (coalescing || assignment.fallback === undefined || branch.label !== assignment.path) return null;
      const selected = values.find(value => value.reuse), fallback = values.find(value => !value.reuse);
      if (selected?.expression !== assignment.path || fallback?.expression !== assignment.fallback) return null;
      coalescing = true; fallbackLine = fallback.line;
    }
  }
  if (accesses !== assignment.path.split('?.').length - 1 || coalescing !== (assignment.fallback !== undefined)) return null;
  const choices = [{ label: coalescing ? 'значение не null' : accesses > 1 ? 'объекты заданы' : 'объект задан',
    expression: path, line: steps[end].line },
    { label: coalescing ? 'null → запасное значение' : 'объект null', expression: assignment.fallback ?? 'null', line: fallbackLine }];
  const step = { ...steps[end], kind: 'branch', controlKind: 'value-selection', label: assignment.source,
    assignmentSelection: true, assignmentTarget: assignment.binding, sourceStatement: assignment.source,
    arms: choices.map(choice => ({ label: choice.label, steps: [{ kind: 'value', expression: choice.expression, line: choice.line }] })) };
  return { index: end, step };
}
