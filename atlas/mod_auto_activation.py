"""Выбор декларативных bundled-анализаторов для одного проекта без изменения настроек."""
from pathlib import PurePosixPath

AUTO_DISABLED = '__autoAnalysisDisabled__'


def auto_eligible(item: dict) -> bool:
    """Только поставляемые хостом analysis-only манифесты применяются автоматически."""
    manifest = item.get('manifest') or {}
    if manifest.get('autoActivate') is False:
        return False
    executable_or_visual = any(manifest.get(key) for key in
        ('frontend', 'grouping', 'workflows', 'theme', 'colorSchemes', 'behavior'))
    return bool(manifest.get('analysis') and not executable_or_visual and
                (item.get('origin') == 'bundled' or manifest.get('autoActivate')))


def activation_selectors(item: dict, catalog: dict) -> dict:
    """Явные selectors приоритетны; старые bundled-языки используют собственные декларации."""
    manifest = item['manifest']
    if manifest.get('autoActivate'):
        return manifest['autoActivate']
    extensions, names, languages = set(), set(), set()
    analysis = manifest['analysis']
    for rule in analysis.get('projectRules', []):
        extensions.update(rule['extensions'])
    for rule in analysis.get('documentProviders', []):
        extensions.update(rule['extensions'])
    for rule in analysis.get('buildRules', []):
        names.update(rule['manifestNames'])
        extensions.update(rule['manifestSuffixes'])
    for rule in [*analysis.get('testFrameworks', []), *analysis.get('entrypointProviders', [])]:
        languages.add(rule['language'])
    for candidate in catalog['mods']:
        if candidate.get('origin') != 'bundled':
            continue
        for rule in (candidate.get('manifest') or {}).get('analysis', {}).get('projectRules', []):
            if rule['language'] in languages:
                extensions.update(rule['extensions'])
    return {'extensions': sorted(extensions), 'manifestNames': sorted(names)}


def selected_analysis_mods(catalog: dict, state: dict, inventory: dict | None) -> list[dict]:
    """Ручное решение/версия приоритетны; зависимости требуют собственного основания."""
    selected = {}
    for item in catalog['mods']:
        if not item.get('manifest', {}).get('analysis') or item.get('status') == 'error':
            continue
        if item.get('enabled'):
            selected[item['id']] = (item, {'mode': 'manual'})
            continue
        if inventory is None or state.get(AUTO_DISABLED) is True or item['id'] in state or not auto_eligible(item):
            continue
        selectors = activation_selectors(item, catalog)
        extensions, names = set(selectors['extensions']), set(selectors['manifestNames'])
        matches = []
        for name in inventory['files']:
            path = PurePosixPath(name)
            if path.suffix.lower() in extensions or path.name in names:
                matches.append(name)
                if len(matches) == 3:
                    break
        if matches:
            selected[item['id']] = (item, {'mode': 'auto', 'matchedFiles': matches,
                'inventoryTruncated': inventory['truncated']})
    while True:
        invalid = [identifier for identifier, (item, _) in selected.items()
                   if not set(item['manifest']['requires']) <= selected.keys()]
        if not invalid:
            break
        for identifier in invalid:
            del selected[identifier]
    return [dict(item, analysisActivation=activation) for _, (item, activation) in sorted(selected.items())]
