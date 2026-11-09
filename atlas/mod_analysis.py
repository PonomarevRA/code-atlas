"""Fingerprint-pinned declarative analysis registry for one immutable map build."""
from copy import deepcopy
from hashlib import sha256
import json

ANALYSIS_VERSION = 'analysis-rules-v1'
SEMANTIC_VERSION = 'semantic-rules-v1'
SOURCE_CAPABILITIES = ('projectRules', 'buildRules', 'testFrameworks', 'entrypointProviders', 'markupProviders')


def _source_analysis(item):
    """Document-only пакеты не участвуют в source snapshot или его настройках."""
    analysis = item.get('manifest', {}).get('analysis', {})
    return any(analysis.get(name) for name in SOURCE_CAPABILITIES)


def _source_fingerprint(item):
    """Mixed mod меняет source identity только при изменении source деклараций."""
    analysis = item.get('manifest', {}).get('analysis', {})
    if not analysis.get('documentProviders') and not analysis.get('semanticProviders'):
        return item['fingerprint']
    source = {name: analysis.get(name, []) for name in SOURCE_CAPABILITIES}
    payload = json.dumps(source, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    return sha256(payload).hexdigest()


def analysis_settings_fingerprint(mods, catalog: dict | None = None) -> str:
    """Проверяет версии/настройки анализа без чтения репозитория в UI или MCP."""
    catalog = catalog or mods.catalog(internal=True)
    state = mods.state()
    entries = []
    for item in catalog['mods']:
        if not _source_analysis(item):
            continue
        selected = state.get(item['id'], 'auto')
        # Optional analysis content does not alter captured structural source declarations.
        analysis = item.get('manifest', {}).get('analysis', {})
        separate_optional_scope = bool(analysis.get('documentProviders') or analysis.get('semanticProviders'))
        if separate_optional_scope and isinstance(selected, str):
            selected = 'on' if item.get('enabled') else 'changed'
        entries.append((item['id'], _source_fingerprint(item), selected, item.get('origin')))
    payload = {'mods': sorted(entries), 'autoDisabled': state.get('__autoAnalysisDisabled__') is True}
    return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _markers_overlap(left: dict, right: dict) -> bool:
    """Detect exact/exact, suffix/suffix and exact/suffix claims for one language."""
    left_names, right_names = set(left.get('manifestNames', [])), set(right.get('manifestNames', []))
    left_suffixes, right_suffixes = set(left.get('manifestSuffixes', [])), set(right.get('manifestSuffixes', []))
    if left_names & right_names or left_suffixes & right_suffixes:
        return True
    for name in left_names:
        if any(name.lower().endswith(suffix.lower()) for suffix in right_suffixes):
            return True
    for name in right_names:
        if any(name.lower().endswith(suffix.lower()) for suffix in left_suffixes):
            return True
    return False


def _validate_build_providers(rules: dict) -> None:
    """Build claims cannot silently override another build or legacy language provider."""
    owners = {}
    for rule in rules.get('projectRules', []):
        owners.setdefault(rule['language'], []).append(rule)
    for rule in rules.get('buildRules', []):
        for language in rule['languages']:
            for previous in owners.get(language, []):
                if _markers_overlap(previous, rule):
                    from .mod_store import ModError
                    raise ModError(f'Conflicting analysis build providers for {language}')
            owners.setdefault(language, []).append(rule)


def _validate_providers(rules: dict) -> None:
    """Reject overlapping active providers rather than selecting facts by package name order."""
    extensions, annotations = {}, {}
    for rule in rules['projectRules']:
        for extension in rule['extensions']:
            if extension in extensions:
                from .mod_store import ModError
                raise ModError(f'Conflicting analysis project providers for {extension}')
            extensions[extension] = rule['modId']
    for framework in rules['testFrameworks']:
        for rule in framework['annotationRules']:
            key = framework['language'], rule['name']
            if key in annotations:
                from .mod_store import ModError
                raise ModError(f'Conflicting analysis annotation providers for {rule["name"]}')
            annotations[key] = framework['modId']
    strategies = set()
    for provider in [*rules.get('entrypointProviders', []), *rules.get('markupProviders', [])]:
        if provider['strategy'] in strategies:
            from .mod_store import ModError
            raise ModError('Conflicting analysis entrypoint providers')
        strategies.add(provider['strategy'])
    _validate_build_providers(rules)


def active_analysis_rules(mods, *, inventory: dict | None = None) -> dict:
    """Freeze enabled language/library declarations; catalog reads never execute package code."""
    from .mod_auto_activation import selected_analysis_mods
    result = {'version': ANALYSIS_VERSION, 'projectRules': [], 'buildRules': [], 'testFrameworks': [], 'entrypointProviders': [], 'mods': []}
    catalog = mods.catalog(internal=True)
    result['settingsFingerprint'] = analysis_settings_fingerprint(mods, catalog)
    for item in selected_analysis_mods(catalog, mods.state(), inventory):
        analysis = (item.get('manifest') or {}).get('analysis')
        if not analysis or not _source_analysis(item):
            continue
        source_fingerprint = _source_fingerprint(item)
        result['mods'].append({'id': item['id'], 'version': item['manifest']['version'],
                               'fingerprint': source_fingerprint})
        if item['analysisActivation']['mode'] == 'auto':
            result['mods'][-1]['activation'] = item['analysisActivation']
        for name in ('projectRules', 'buildRules', 'testFrameworks', 'entrypointProviders', 'markupProviders'):
            for rule in analysis.get(name, []):
                result.setdefault(name, []).append({**deepcopy(rule), 'modId': item['id'], 'modFingerprint': source_fingerprint})
    _validate_providers(result)
    if not result['entrypointProviders']:
        # Unrelated Java/.NET snapshots retain their existing rules fingerprint.
        result.pop('entrypointProviders')
    if inventory is not None:
        result['fileSelection'] = {key: inventory[key] for key in ('version', 'mode', 'truncated', 'warnings')}
    payload = json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    result['fingerprint'] = sha256(payload).hexdigest()
    return result


def assert_analysis_rules_current(mods, rules: dict, *, inventory: dict | None = None) -> None:
    """Fail publication when activation or package content changed while facts were captured."""
    if active_analysis_rules(mods, inventory=inventory)['fingerprint'] != rules['fingerprint']:
        from .mod_store import ModError
        raise ModError('Analysis mods changed during the map build; rebuild with the current versions')


def _semantic_fingerprint(item: dict) -> str:
    """Hash only a package's semantic declarations; unrelated source rules stay independent."""
    providers = item.get('manifest', {}).get('analysis', {}).get('semanticProviders', [])
    payload = json.dumps(providers, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    return sha256(payload).hexdigest()


def semantic_settings_fingerprint(mods, catalog: dict | None = None) -> str:
    """Identify semantic choices without invalidating the immutable Graphify source map."""
    catalog = catalog or mods.catalog(internal=True)
    state = mods.state()
    entries = []
    for item in catalog['mods']:
        if not item.get('manifest', {}).get('analysis', {}).get('semanticProviders'):
            continue
        selected = state.get(item['id'], 'auto')
        # Manual package pins remain honored by selection; the baseline has a separate identity.
        if isinstance(selected, str) and selected != 'auto':
            selected = 'on' if item.get('enabled') else 'changed'
        entries.append((item['id'], _semantic_fingerprint(item), selected, item.get('origin')))
    payload = {'mods': sorted(entries), 'autoDisabled': state.get('__autoAnalysisDisabled__') is True}
    return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def active_semantic_rules(mods, *, inventory: dict | None = None) -> dict:
    """Freeze optional engine declarations; competing strategies for a language fail explicitly."""
    from .mod_auto_activation import selected_analysis_mods
    from .mod_store import ModError

    catalog = mods.catalog(internal=True)
    result = {'version': SEMANTIC_VERSION, 'semanticProviders': [], 'mods': [],
              'settingsFingerprint': semantic_settings_fingerprint(mods, catalog)}
    claims = set()
    # Producer and reader may enumerate the same graph files in different orders.
    # Canonicalize before the bounded first-three activation evidence is chosen.
    selected_inventory = inventory
    if inventory is not None:
        selected_inventory = {**inventory, 'files': sorted(set(inventory['files']))}
    for item in selected_analysis_mods(catalog, mods.state(), selected_inventory):
        providers = item.get('manifest', {}).get('analysis', {}).get('semanticProviders', [])
        if not providers:
            continue
        fingerprint = _semantic_fingerprint(item)
        result['mods'].append({'id': item['id'], 'version': item['manifest']['version'],
                               'fingerprint': fingerprint, 'activation': item['analysisActivation']})
        for provider in providers:
            language = provider['language']
            if language in claims:
                raise ModError(f'Conflicting semantic providers for {language}')
            claims.add(language)
            result['semanticProviders'].append({**deepcopy(provider), 'modId': item['id'],
                                                'modFingerprint': fingerprint})
    payload = json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    result['fingerprint'] = sha256(payload).hexdigest()
    return result


def assert_semantic_rules_current(mods, rules: dict, *, inventory: dict | None = None) -> None:
    """Reject an optional publication when its selected providers changed during analysis."""
    if active_semantic_rules(mods, inventory=inventory)['fingerprint'] != rules['fingerprint']:
        from .mod_store import ModError
        raise ModError('Semantic mods changed during analysis; refresh the optional enrichment')
