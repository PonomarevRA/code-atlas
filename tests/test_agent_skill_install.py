"""Complete skill publication preserves client paths and user-owned local edits."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.install_agent_skills import CLIENT_ROOTS, SKILLS, install_skills, read_files

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('client', CLIENT_ROOTS)
def test_client_preview_apply_and_repeat(tmp_path, client):
    """Preview writes nothing, application copies complete bundles, and repeat is idempotent."""
    command = [sys.executable, str(ROOT / 'scripts/install_agent_skills.py'),
               '--client', client, '--project-root', str(tmp_path / 'project')]
    preview = subprocess.run(command, check=True, capture_output=True, text=True)
    result = json.loads(preview.stdout)
    assert not result['applied'] and not (tmp_path / 'project').exists()
    target = tmp_path / 'project' / CLIENT_ROOTS[client]
    assert {row['destination'] for row in result['skills']} == {str(target / name) for name in SKILLS}

    subprocess.run([*command, '--apply'], check=True, capture_output=True, text=True)
    for name in SKILLS:
        assert read_files(target / name) == read_files(ROOT / 'skills' / name)
    stamps = {p: p.stat().st_mtime_ns for p in target.rglob('*') if p.is_file()}
    repeated = subprocess.run([*command, '--apply'], check=True, capture_output=True, text=True)
    assert all(row['action'] == 'present' for row in json.loads(repeated.stdout)['skills'])
    assert stamps == {p: p.stat().st_mtime_ns for p in target.rglob('*') if p.is_file()}


def test_conflict_is_checked_before_other_skill_publication(tmp_path):
    """A local engine skill edit leaves both itself and the missing main skill untouched."""
    target = tmp_path / 'skills'
    edited = target / 'code-atlas-engines'
    edited.mkdir(parents=True)
    (edited / 'SKILL.md').write_text('local changes')
    with pytest.raises(ValueError, match='Preserve local edits'):
        install_skills(ROOT / 'skills', target, apply=True)
    assert (edited / 'SKILL.md').read_text() == 'local changes'
    assert not (target / 'code-atlas').exists()
    assert not (target / '.code-atlas-skills.lock').exists()


def test_failed_second_publication_is_retryable(tmp_path, monkeypatch):
    """A completed skill remains valid when publishing the next folder fails."""
    target = tmp_path / 'skills'
    original = os.rename

    def fail_second(source, destination):
        """Inject a failure only at the second complete-folder publication."""
        if Path(destination).name == 'code-atlas-engines':
            raise OSError('publication interrupted')
        return original(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, 'rename', fail_second)
        with pytest.raises(OSError, match='publication interrupted'):
            install_skills(ROOT / 'skills', target, apply=True)
    assert read_files(target / 'code-atlas') == read_files(ROOT / 'skills/code-atlas')
    assert not (target / 'code-atlas-engines').exists()
    assert not (target / '.code-atlas-skills.lock').exists()
    install_skills(ROOT / 'skills', target, apply=True)
    assert read_files(target / 'code-atlas-engines') == read_files(ROOT / 'skills/code-atlas-engines')


def test_concurrent_installer_lock_preserves_existing_owner(tmp_path):
    """A second installer cannot publish while another owns the installation root."""
    target = tmp_path / 'skills'
    target.mkdir()
    lock = target / '.code-atlas-skills.lock'
    lock.write_text('another owner')
    with pytest.raises(FileExistsError):
        install_skills(ROOT / 'skills', target, apply=True)
    assert lock.read_text() == 'another owner'
    assert not (target / 'code-atlas').exists()


def test_generic_explicit_destination(tmp_path):
    """Unknown clients can select their own root without assuming an IDE directory."""
    destination = tmp_path / 'custom' / 'instruction-packs'
    command = [sys.executable, str(ROOT / 'scripts/install_agent_skills.py'),
               '--client', 'generic', '--destination', str(destination), '--skill', 'code-atlas-engines', '--apply']
    subprocess.run(command, check=True, capture_output=True, text=True)
    assert read_files(destination / 'code-atlas-engines') == read_files(ROOT / 'skills/code-atlas-engines')
    assert not (destination / 'code-atlas').exists()
