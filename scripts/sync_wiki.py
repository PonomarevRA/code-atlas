"""Публикация только docs/wiki в отдельный Git wiki; вызывается явно или CI."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit


def validate_remote(remote: str) -> str:
    """Принимает HTTPS/SSH wiki без секретов в URL и без локального назначения."""
    if any(ord(char) < 32 for char in remote) or not remote.endswith('.wiki.git'):
        raise ValueError('Укажите URL wiki-репозитория, заканчивающийся на .wiki.git')
    if re.fullmatch(r'git@[A-Za-z0-9.-]+:[A-Za-z0-9_./-]+\.wiki\.git', remote):
        return remote
    parsed = urlsplit(remote)
    if parsed.scheme not in {'https', 'ssh'} or not parsed.hostname or parsed.query or parsed.fragment or parsed.password:
        raise ValueError('Поддерживаются HTTPS/SSH wiki без credentials/query/fragment')
    if parsed.username and not (parsed.scheme == 'ssh' and parsed.username == 'git'):
        raise ValueError('Используйте WIKI_TOKEN или Git credential manager вместо credentials в URL')
    return remote


def run_git(arguments: list[str], cwd: Path, environment: dict) -> str:
    """Не выводит stderr Git: credential helper может включить секрет в сообщение."""
    result = subprocess.run(['git', '-c', 'core.hooksPath=' + os.devnull, *arguments],
                            cwd=cwd, env=environment, capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise RuntimeError('Git wiki завершился с ошибкой. Проверьте URL, инициализацию wiki и права credentials.')
    return result.stdout.strip()


def sync(source: Path, remote: str, *, publish: bool = False) -> int:
    """Обновляет управляемые Markdown-страницы, сохраняя посторонние файлы wiki."""
    remote = validate_remote(remote)
    pages = sorted(source.glob('*.md'))
    if not pages or any(path.is_symlink() for path in pages):
        raise ValueError('Нет обычных Markdown-страниц в docs/wiki')
    if not publish:
        print('Проверено страниц:', len(pages), '— для публикации передайте --publish')
        return len(pages)
    environment = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    environment.update(GIT_TERMINAL_PROMPT='0', GIT_CONFIG_COUNT='2',
                       GIT_CONFIG_KEY_0='protocol.ext.allow', GIT_CONFIG_VALUE_0='never',
                       GIT_CONFIG_KEY_1='protocol.file.allow', GIT_CONFIG_VALUE_1='never')
    with tempfile.TemporaryDirectory(prefix='code-atlas-wiki-') as temporary:
        work = Path(temporary)
        if environment.get('WIKI_TOKEN'):
            # Token остаётся в окружении; на диск пишется только скрипт чтения env.
            askpass = work / 'askpass.sh'
            askpass.write_text('#!/bin/sh\ncase "$1" in *Username*) printf "%s\\n" "${WIKI_USERNAME:-oauth2}";; *) printf "%s\\n" "$WIKI_TOKEN";; esac\n')
            askpass.chmod(0o700)
            environment['GIT_ASKPASS'] = str(askpass)
        checkout = work / 'wiki'
        run_git(['clone', '--', remote, str(checkout)], work, environment)
        manifest = checkout / '.code-atlas-wiki.json'
        if manifest.is_symlink():
            raise ValueError('Wiki содержит ссылку вместо служебного манифеста')
        previous = json.loads(manifest.read_text()) if manifest.exists() else []
        if not isinstance(previous, list) or any(not isinstance(name, str) for name in previous):
            raise ValueError('Неверный манифест управляемых страниц wiki')
        names = {page.name for page in pages}
        # Удаляем только страницы, ранее опубликованные этим же механизмом.
        for name in previous:
            if isinstance(name, str) and name not in names and Path(name).name == name and name.endswith('.md'):
                (checkout / name).unlink(missing_ok=True)
        for page in pages:
            destination = checkout / page.name
            if destination.is_symlink():
                raise ValueError('Wiki содержит недопустимую ссылку вместо страницы')
            if destination.exists() and page.name not in previous and destination.read_bytes() != page.read_bytes():
                raise ValueError('Страница wiki уже существует вне управляемого набора: ' + page.name)
            shutil.copyfile(page, destination)
        manifest.write_text(json.dumps(sorted(names), ensure_ascii=False) + '\n')
        run_git(['add', '--', '*.md', '.code-atlas-wiki.json'], checkout, environment)
        if not run_git(['diff', '--cached', '--name-only'], checkout, environment):
            print('Wiki уже актуальна')
            return len(pages)
        run_git(['-c', 'user.name=Code Atlas Docs', '-c', 'user.email=code-atlas-docs@localhost',
                 'commit', '-m', 'Update Code Atlas documentation'], checkout, environment)
        run_git(['push', 'origin', 'HEAD'], checkout, environment)
        print('Опубликовано страниц:', len(pages))
        return len(pages)


def main() -> None:
    """По умолчанию проверяет пакет; сеть и push разрешаются только --publish."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--remote', default=os.environ.get('WIKI_GIT_URL'))
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parent.parent / 'docs/wiki')
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    if not args.remote:
        parser.error('Укажите --remote или WIKI_GIT_URL')
    sync(args.source, args.remote, publish=args.publish)


if __name__ == '__main__':
    main()
