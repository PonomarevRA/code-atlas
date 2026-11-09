"""Application palette catalog and data-only mod creation, independent from source maps."""
import json
from .color_scheme_models import ColorScheme, TOKEN_NAMES
from .mod_models import ModManifest
from .mod_store import ModError, MAX_MODS
from .runtime_paths import resource_root


def scheme_catalog(mods, *, safe=False) -> dict:
    """Resolve active declarations without importing scripts; base palettes supply omitted tokens."""
    path = resource_root() / 'web' / 'color-schemes.json'
    builtins = [ColorScheme.model_validate(item).model_dump() for item in json.loads(path.read_text(encoding='utf-8'))]
    bases = {item['id']: item for item in builtins}
    schemes = [{**item, 'origin': 'builtin'} for item in builtins]
    if not safe:
        for mod in mods.catalog()['mods']:
            if not mod.get('enabled'):
                continue
            for declaration in mod['manifest'].get('colorSchemes', []):
                scheme = ColorScheme.model_validate(declaration).model_dump()
                schemes.append({**scheme, 'id': f"mod:{mod['id']}:{scheme['id']}",
                                'origin': mod['id'], 'fingerprint': mod['fingerprint'],
                                'tokens': {**bases[scheme['base']]['tokens'], **scheme['tokens']}})
    return {'schemes': schemes, 'tokens': list(TOKEN_NAMES)}


def scheme_styles(catalog: dict) -> str:
    """Emit only validated palette declarations in a same-origin stylesheet allowed by strict CSP."""
    rules = []
    for item in catalog['schemes']:
        # Both identifier components come from validated IDs, never labels or CSS selectors.
        selector = f':root[data-color-scheme="{item["id"]}"]'
        if item['origin'] == 'builtin':
            selector += f', :root[data-color-mode="{item["base"]}"]'
        declarations = [f'--atlas-{token}:{value}' for token, value in item['tokens'].items()]
        declarations += [f'--accent:var(--atlas-accent)', '--line:var(--atlas-border)', '--muted:var(--atlas-muted)',
                         f'color-scheme:{item["base"]}']
        rules.append(selector + '{' + ';'.join(declarations) + '}')
    # Exact custom selection wins over its inherited base, regardless of catalog ordering.
    return '\n'.join(rules)


def create_scheme_package(mods, scheme: ColorScheme) -> dict:
    """Create a new disabled JSON-only mod; never overwrite another package or activate code."""
    identifier = 'theme-' + scheme.id
    if len(identifier) > 48:
        raise ModError('Для имени схемы используйте не более 42 символов')
    with mods.lock:
        catalog = mods.catalog()
        if catalog['truncated'] or len(catalog['mods']) >= MAX_MODS:
            raise ModError('Каталог модов заполнен')
        if mods.directory.is_symlink():
            raise ModError('Каталог модов не может быть символической ссылкой')
        if any(item['id'] == identifier for item in catalog['mods']):
            raise ModError('Мод с таким именем уже существует. Выберите другое имя.')
        mods.directory.mkdir(parents=True, exist_ok=True)
        destination = mods.directory / identifier
        if destination.exists() or destination.is_symlink():
            raise ModError('Каталог уже существует. Выберите другое имя.')
        manifest = ModManifest(apiVersion=1, id=identifier, name=scheme.label, version='1.0.0',
                               description='Пользовательская цветовая схема Atlas', colorSchemes=[scheme])
        destination.mkdir()  # Exclusive creation also protects against a concurrent creator.
        try:
            with (destination / 'mod.json').open('x', encoding='utf-8') as stream:
                stream.write(manifest.model_dump_json(exclude_defaults=True, indent=2))
        except OSError:
            # Remove only our empty directory; leave any unexpected concurrent contents intact.
            if not any(destination.iterdir()):
                destination.rmdir()
            raise
        return next(item for item in mods.catalog()['mods'] if item['id'] == identifier)
