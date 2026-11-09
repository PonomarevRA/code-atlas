"""Независимый fingerprint document rules: discovery/auto не запускают импорт."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import PurePosixPath

from .mod_auto_activation import selected_analysis_mods, AUTO_DISABLED
from .mod_store import ModError

DOCUMENT_RULES_VERSION = 'document-rules-v1'


def _digest(value):
    """Каноническое представление исключает порядок JSON keys из идентичности."""
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    return sha256(payload).hexdigest()


def document_settings_fingerprint(mods, catalog=None):
    """Следит только за документными декларациями и их ручной активацией."""
    catalog, state = catalog or mods.catalog(internal=True), mods.state()
    entries = []
    for item in catalog['mods']:
        providers = item.get('manifest', {}).get('analysis', {}).get('documentProviders', [])
        if providers:
            entries.append([item['id'], item['fingerprint'], state.get(item['id'], 'auto'), providers])
    return _digest({'mods': sorted(entries), 'autoDisabled': state.get(AUTO_DISABLED) is True})


def active_document_rules(mods, *, inventory=None):
    """Фиксирует parser декларации отдельно от Graphify/source analysis snapshot."""
    catalog = mods.catalog(internal=True)
    result = {'version': DOCUMENT_RULES_VERSION, 'documentProviders': [], 'mods': [],
              'settingsFingerprint': document_settings_fingerprint(mods, catalog)}
    strategies = set()
    for item in selected_analysis_mods(catalog, mods.state(), inventory):
        providers = item['manifest'].get('analysis', {}).get('documentProviders', [])
        if not providers:
            continue
        result['mods'].append({'id': item['id'], 'version': item['manifest']['version'],
                               'fingerprint': item['fingerprint'], 'activation': item['analysisActivation']})
        for rule in providers:
            if rule['strategy'] in strategies:
                raise ModError('Conflicting document providers for strategy ' + rule['strategy'])
            strategies.add(rule['strategy'])
            result['documentProviders'].append({**deepcopy(rule), 'modId': item['id'],
                                                 'modFingerprint': item['fingerprint']})
    result['fingerprint'] = _digest(result)
    return result


def assert_document_rules_current(mods, rules, *, inventory=None):
    """Не публикует результат, если document mods изменились во время разбора."""
    if active_document_rules(mods, inventory=inventory)['fingerprint'] != rules['fingerprint']:
        raise ModError('Document mods changed during build; select current declarations')


def select_document_rule(rules, path, *, strategy=None, context=None):
    """Применяет frozen declarations; YAML/Helm различаются доказанным chart context."""
    path, context = PurePosixPath(str(path)), context or {}
    candidates = [rule for rule in rules['documentProviders'] if path.suffix.lower() in rule['extensions']]
    if strategy is not None:
        candidates = [rule for rule in candidates if rule['strategy'] == strategy]
    strategies = {rule['strategy'] for rule in candidates}
    if strategies == {'document-yaml-v1', 'document-helm-v1'}:
        selected = 'document-helm-v1' if context.get('chartContext') or path.name == 'Chart.yaml' else 'document-yaml-v1'
        candidates = [rule for rule in candidates if rule['strategy'] == selected]
    elif not context.get('chartContext') and path.name != 'Chart.yaml' and path.suffix.lower() != '.tpl':
        # A Helm-only declaration cannot silently replace a manually disabled generic YAML provider.
        candidates = [rule for rule in candidates if rule['strategy'] != 'document-helm-v1']
    if len(candidates) > 1:
        raise ModError('Conflicting document providers require an explicit profile')
    return deepcopy(candidates[0]) if candidates else None


def select_document_provider(rules, path, context=None, *, strategy=None):
    """Совместимый entry point выбора declaration из frozen active rules, без parse."""
    return select_document_rule(rules, path, strategy=strategy, context=context)
