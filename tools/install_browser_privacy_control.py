"""Provision Safari native settings credential after installing the reviewed local browser app."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from atlas.browser_privacy_files import private_read, private_write
from atlas.runtime_paths import default_data_dir


def install(data_directory, home):
    """Copy exactly one32-byte credential into the installed Safari extension's own sandbox."""
    container = Path(home) / 'Library/Containers/com.code-atlas.browser.Extension/Data'
    if not container.is_dir():
        raise ValueError('Enable the installed Safari extension once before provisioning settings')
    # Existing ancestors must be real directories; a link cannot redirect a private write.
    for ancestor in (container, *container.parents):
        if ancestor.is_symlink():
            raise ValueError('Private container path must not contain symlinks')
    target = container
    for part in ('Library', 'Application Support', 'Atlas Browser'):
        target = target / part
        if target.is_symlink():
            raise ValueError('Private container path must not contain symlinks')
        target.mkdir(mode=0o700, exist_ok=True)
        if not target.is_dir():
            raise ValueError('Private container path must be a directory')
    token = private_read(Path(data_directory) / 'browser-privacy/control.key', 32)
    if len(token) != 32:
        raise ValueError('Start privacy-enabled Atlas and open browser settings first')
    private_write(target / 'native-control.key', token)
    return {'installed': True, 'secretPrinted': False}


def main():
    """Install only; never select/reduce a privacy mode or change Safari preferences."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    print(install(default_data_dir(), Path.home()))


if __name__ == '__main__':
    main()
