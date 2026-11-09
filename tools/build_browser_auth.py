"""Build a small universal macOS user-presence helper using the installed system SDK only."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    """Compile both slices, combine and ad-hoc sign; no authentication prompt is evaluated by this build."""
    source = ROOT / 'packaging/safari-browser/PrivacyConfirmation.swift'
    destination = ROOT / 'vendor/atlas-browser-auth'
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='atlas-browser-auth-build-') as temporary:
        slices = []
        for architecture in ('arm64', 'x86_64'):
            output = Path(temporary) / architecture
            subprocess.run(['xcrun','swiftc','-O','-target',architecture+'-apple-macos14.0',str(source),'-o',str(output)], check=True)
            slices.append(str(output))
        binary = destination / 'confirm'
        subprocess.run(['xcrun','lipo','-create',*slices,'-output',str(binary)],check=True)
        subprocess.run(['codesign','--force','--sign','-',str(binary)],check=True)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    (destination/'manifest.json').write_text(json.dumps({'schemaVersion':1,'sourceSha256':hashlib.sha256(source.read_bytes()).hexdigest(),'binarySha256':digest,'platform':'macos14+','architectures':['arm64','x86_64'],'networkRequired':False},indent=2)+'\n')
    print(digest)


if __name__ == '__main__':
    main()
