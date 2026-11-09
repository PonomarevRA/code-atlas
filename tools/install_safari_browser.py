"""Install a verified local Safari build silently and retire only its transaction rollback."""
import argparse
from pathlib import Path
import plistlib
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from desktop_support.obsolete_apps import CleanupRefused, bundle_identity, inspect_payload, remove_obsolete, verify_signature

BUNDLE_ID = 'com.code-atlas.browser'
EXTENSION = 'Contents/PlugIns/Atlas Browser Extension.appex'
LSREGISTER = '/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister'


def file_owners(path):
    """Return current owner PIDs; failed or incomplete lsof checks refuse replacement."""
    result = subprocess.run(['/usr/sbin/lsof', '-t', '+D', str(path)], capture_output=True, text=True, timeout=30)
    if result.returncode not in (0, 1) or result.stderr.strip():
        raise CleanupRefused('Cannot establish application file owners')
    return result.stdout.split()


def register_application(path):
    """Register only the canonical containing app and extension; never launch Safari or its settings."""
    subprocess.run([LSREGISTER, '-f', str(path)], check=True, capture_output=True, timeout=30)
    subprocess.run(['/usr/bin/pluginkit', '-a', str(path / EXTENSION)], check=True, capture_output=True, timeout=30)


def copy_application(source, destination):
    """Preserve the signed macOS bundle metadata in a fresh transaction directory."""
    subprocess.run(['/usr/bin/ditto', str(source), str(destination)], check=True, capture_output=True, timeout=120)


def install(source, destination, *, owners=file_owners, qualify=verify_signature,
            register=register_application, copy=copy_application):
    """Refuse live/foreign/data-bearing targets; restore the old app if publish or registration fails."""
    source, destination = Path(source).absolute(), Path(destination).absolute()
    source_identity = bundle_identity(source)
    extension_info = source / EXTENSION / 'Contents/Info.plist'
    if (source_identity['bundleId'] != BUNDLE_ID or extension_info.resolve() != extension_info
            or not extension_info.is_file() or not 0 < extension_info.stat().st_size <= 65536):
        raise CleanupRefused('Unexpected Safari build identity')
    info = plistlib.loads((source / 'Contents/Info.plist').read_bytes())
    extension = plistlib.loads(extension_info.read_bytes())
    if info.get('CFBundlePackageType') != 'APPL' or extension.get('CFBundleIdentifier') != BUNDLE_ID + '.Extension':
        raise CleanupRefused('Unexpected Safari bundle metadata')
    if destination.suffix != '.app':
        raise CleanupRefused('Destination must be an application bundle')
    inspect_payload(source)
    if source == destination or source in destination.parents or destination in source.parents:
        raise CleanupRefused('Source and destination must be separate application trees')
    if destination.is_symlink() or destination.resolve() != destination:
        raise CleanupRefused('Redirected application destination')
    qualify(source)
    previous = None
    if destination.exists():
        previous = bundle_identity(destination)
        if previous['bundleId'] != BUNDLE_ID:
            raise CleanupRefused('Foreign application destination')
        inspect_payload(destination)
        if owners(destination):
            raise CleanupRefused('Quit Atlas Browser and close Safari before updating; a resource is in use')
    destination.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    staging = destination.with_name('.atlas-browser-install-' + token + '.app')
    rollback = destination.with_name('Atlas Browser.previous-' + token + '.app')
    moved = published = False
    published_identity = None
    staging_identity = None
    try:
        copy(source, staging)
        staged = bundle_identity(staging)
        staging_identity = staged
        qualify(staging)
        if staged['bundleId'] != BUNDLE_ID or staged['binarySha256'] != source_identity['binarySha256']:
            raise CleanupRefused('Staged application differs from verified build')
        if previous:
            if bundle_identity(destination) != previous or owners(destination):
                raise CleanupRefused('Current application changed or became active during staging')
            destination.rename(rollback)
            moved = True
        elif destination.exists() or destination.is_symlink():
            raise CleanupRefused('Application destination appeared during staging')
        staging.rename(destination)
        published = True
        published_identity = bundle_identity(destination)
        qualify(destination)
        register(destination)
    except BaseException:
        if published:
            if bundle_identity(destination) != published_identity or owners(destination):
                raise CleanupRefused('Published app changed or became active; both it and the rollback are retained')
            replacement = rollback if moved else source
            remove_obsolete(published_identity, replacement, [destination.parent], owners, qualify=qualify)
        if moved:
            rollback.rename(destination)
            register(destination)
        raise
    finally:
        if staging.exists():
            try:
                if staging_identity is None:
                    raise CleanupRefused('Incomplete staging identity')
                remove_obsolete(staging_identity, source, [destination.parent], owners, qualify=qualify)
            except (CleanupRefused, OSError, subprocess.SubprocessError) as error:
                raise CleanupRefused('Uncertain or active staging retained at ' + str(staging)) from error
    report = {'installed': str(destination), 'browserLaunched': False, 'previousRetained': None}
    if moved:
        try:
            remove_obsolete(dict(previous, path=str(rollback)), destination, [destination.parent], owners, qualify=qualify)
        except (CleanupRefused, OSError, subprocess.SubprocessError) as error:
            # A newly acquired owner is not grounds for rolling back a valid installed app.
            report['previousRetained'] = str(rollback)
            report['retentionReason'] = str(error)
    return report


def main():
    """Expose explicit quiet installation for generated builds without any browser/UI automation."""
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--destination', type=Path, default=Path.home() / 'Applications/Atlas Browser.app')
    args = parser.parse_args()
    print(json.dumps(install(args.source, args.destination), ensure_ascii=False))


if __name__ == '__main__':
    main()
