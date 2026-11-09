"""Поставка модулей с повторным dependency closure и счётом требований."""

from collections import defaultdict

from .model import VERSION, dependency_components, prepare_problem, validate_assignment


def evaluate_delivery(problem, assignment, *, require_scc=True):
    """
    Считает полностью реализованные требования для каждой заданной комплектации.

    Module expansion и dependency expansion повторяются до fixed point:
    дополнительные файлы включённого модуля тоже могут требовать зависимости.
    Это явно более строгая модель, чем однопроходная последовательность COST
    в опубликованном псевдокоде; цель — число требований, не binary size или Q.
    """
    problem = prepare_problem(problem)
    assignment = validate_assignment(problem, assignment, require_scc=require_scc)
    modules, dependencies = defaultdict(set), defaultdict(set)
    for file, module in assignment.items():
        modules[module].add(file)
    for dependent, prerequisite in problem.dependencies:
        dependencies[dependent].add(prerequisite)
    requirements = {key: set(files) for key, files in problem.requirements}
    configurations, total = [], 0
    for key, required in problem.configurations:
        useful = set()
        for requirement in required:
            useful.update(requirements[requirement])
        delivered = set(useful)
        pending = list(sorted(useful))
        while pending:
            file = pending.pop()
            expansion = modules[assignment[file]] | dependencies[file]
            for extra in sorted(expansion - delivered):
                delivered.add(extra)
                pending.append(extra)
        implemented = sorted(requirement for requirement, files in requirements.items() if files <= delivered)
        unused = sorted(set(implemented) - set(required))
        total += len(implemented)
        configurations.append({'id': key, 'requiredRequirements': list(required),
                               'deliveredFiles': sorted(delivered),
                               'deliveredModules': sorted({assignment[file] for file in delivered}),
                               'implementedRequirements': implemented, 'unrequestedRequirements': unused,
                               'implementedCount': len(implemented)})
    return {'version': VERSION, 'problemHash': problem.problem_hash,
            'assignment': assignment, 'objective': total,
            'objectiveKind': 'sum-implemented-requirements-over-configurations',
            'direction': 'dependent-to-prerequisite', 'closurePolicy': 'module-and-dependency-fixed-point',
            'sccRequired': require_scc,
            'assignmentSccConsistent': all(len({assignment[file] for file in group}) == 1 for group in dependency_components(problem)),
            'feasibilityScope': 'SCC-indivisible-main-model' if require_scc else 'raw-file-ablation-SCC-constraint-relaxed',
            'configurations': configurations,
            'limitations': ['Declared synthetic input only; no real product benefit qualification.',
                            'Requirement implementation is all mapped files, not a binary size or graph-quality metric.']}
