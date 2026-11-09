"""Preview or install the complete portable Atlas skills without replacing local edits."""
import argparse
import json
import os
from pathlib import Path
import tempfile

SKILLS = ('code-atlas', 'code-atlas-engines')
CLIENT_ROOTS = {'codex': '.agents/skills', 'cursor': '.cursor/skills',
                'claude': '.claude/skills', 'deepseek': '.dsh/skills', 'generic': '.agents/skills'}


def read_files(folder: Path) -> dict[str, bytes]:
    """Read a bounded self-contained skill; redirected resources are rejected."""
    if not folder.is_dir() or folder.is_symlink():
        raise ValueError(f'Expected a regular skill directory: {folder}')
    files = {}
    for path in sorted(folder.rglob('*')):
        if path.is_symlink():
            raise ValueError(f'Skill contains a symlink: {path}')
        if path.is_file():
            if len(files) >= 64 or path.stat().st_size > 128 * 1024:
                raise ValueError(f'Skill exceeds resource limits: {folder}')
            files[path.relative_to(folder).as_posix()] = path.read_bytes()
    if 'SKILL.md' not in files:
        raise ValueError(f'SKILL.md is missing: {folder}')
    return files


def install_skills(source: Path, destination: Path, names=SKILLS, *, apply=False) -> list[dict]:
    """Validate every conflict before writing; complete folders survive a retry after partial failure."""
    source, destination = source.resolve(), destination.resolve()
    if destination.is_relative_to(source):
        raise ValueError('Choose an installation directory outside the source skills')
    if not names or len(set(names)) != len(names) or any(name not in SKILLS for name in names):
        raise ValueError('Choose distinct supported Atlas skills')
    bundles, plan = {}, []
    for name in names:
        files = read_files(source / name)
        target = destination / name
        present = target.exists() or target.is_symlink()
        if present and read_files(target) != files:
            raise ValueError(f'Preserve local edits; skill already differs: {target}')
        bundles[name] = files
        plan.append({'skill': name, 'destination': str(target), 'action': 'present' if present else 'install'})
    if not apply or all(item['action'] == 'present' for item in plan):
        return plan

    destination.mkdir(parents=True, exist_ok=True)
    lock = destination / '.code-atlas-skills.lock'
    with lock.open('x') as ownership:
        identity = os.fstat(ownership.fileno()).st_ino
    try:
        with tempfile.TemporaryDirectory(prefix='.atlas-skills-', dir=destination) as temporary:
            staging = Path(temporary)
            for item in plan:
                if item['action'] == 'present':
                    continue
                folder = staging / item['skill']
                folder.mkdir()
                for relative, content in bundles[item['skill']].items():
                    path = folder / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
            for item in plan:
                if item['action'] == 'present':
                    continue
                target = Path(item['destination'])
                if target.exists() or target.is_symlink():
                    raise ValueError(f'Installation target appeared concurrently: {target}')
                # Publish a complete folder. Earlier published skills remain valid on retry.
                os.rename(staging / item['skill'], target)
    finally:
        if lock.stat().st_ino == identity:
            lock.unlink()
    return plan


def main():
    """Preview by default; --apply copies only selected skills into the chosen client root."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', choices=CLIENT_ROOTS, default='generic')
    parser.add_argument('--project-root', type=Path, default=Path.cwd())
    parser.add_argument('--destination', type=Path, help='Explicit skill root for any other client')
    parser.add_argument('--skill', action='append', choices=SKILLS)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    project = args.project_root.expanduser().resolve()
    target = args.destination.expanduser() if args.destination else Path(CLIENT_ROOTS[args.client])
    if not target.is_absolute():
        target = project / target
    try:
        plan = install_skills(Path(__file__).resolve().parents[1] / 'skills', target,
                              args.skill or SKILLS, apply=args.apply)
    except (OSError, ValueError) as error:
        parser.exit(1, f'{error}\n')
    print(json.dumps({'applied': args.apply, 'client': args.client, 'skills': plan}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
