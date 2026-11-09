"""Build an ad-hoc host/WidgetKit extension without installing or registering it."""

import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys


def build_environment(use_xcode):
    """Use installed CLT only for the basic route; preserve explicit developer selection."""
    environment = dict(os.environ)
    clt = Path('/Library/Developer/CommandLineTools')
    if not use_xcode and (clt / 'usr/bin/swiftc').is_file():
        environment.setdefault('DEVELOPER_DIR', str(clt))
    return environment


def sign_bundle(root, output):
    """Ad-hoc sign the sandboxed extension before sealing and verifying its container."""
    extension = output / 'Contents/PlugIns/AGRequests.appex'
    for target in (extension, output):
        arguments = ['codesign', '--force', '--sign', '-']
        if target.suffix == '.appex':
            arguments += ['--entitlements', str(root / 'Widget.entitlements')]
        subprocess.run(arguments + [str(target)], check=True)
    subprocess.run(
        ['codesign', '--verify', '--deep', '--strict', str(output)], check=True
    )


def build_with_xcode(root, output, environment, configurable):
    """Build the generated project so full Xcode can extract optional AppIntent metadata."""
    from generate_project import generate

    generate(root, configurable=configurable)
    arguments = [
        'xcodebuild', '-project', str(root / 'AGRequests.xcodeproj'),
        '-scheme', 'AGRequestsWidgets', '-configuration', 'Release',
        '-derivedDataPath', str(root / 'build/xcode'),
        'CODE_SIGNING_ALLOWED=NO', 'build',
    ]
    subprocess.run(arguments, env=environment, check=True)
    built = root / 'build/xcode/Build/Products/Release/AG Requests Widgets.app'
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(built, output, symlinks=True)


def write_metadata(output):
    """Write unchanged host and WidgetKit identities for the direct Swift build."""
    host = {
        'CFBundlePackageType': 'APPL',
        'CFBundleExecutable': 'AGRequestsHost',
        'CFBundleIdentifier': 'io.github.ponomarevra.agdesktop.widgets',
        'CFBundleName': 'AG Requests Widgets',
        'CFBundleVersion': '1',
        'CFBundleShortVersionString': '0.1.0',
        'LSMinimumSystemVersion': '14.0',
        'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True},
    }
    extension = {
        **host,
        'CFBundlePackageType': 'XPC!',
        'CFBundleExecutable': 'AGRequests',
        'CFBundleIdentifier': host['CFBundleIdentifier'] + '.requests',
        'NSExtension': {'NSExtensionPointIdentifier': 'com.apple.widgetkit-extension'},
    }
    (output / 'Contents/Info.plist').write_bytes(plistlib.dumps(host))
    extension_folder = output / 'Contents/PlugIns/AGRequests.appex'
    (extension_folder / 'Contents/Info.plist').write_bytes(plistlib.dumps(extension))


def build_with_swift(root, output, environment, configurable):
    """Compile actual native sources using the selected local SDK and architecture."""
    sdk = subprocess.check_output(
        ['xcrun', '--sdk', 'macosx', '--show-sdk-path'],
        env=environment, text=True,
    ).strip()
    architecture = os.uname().machine
    common = [
        'xcrun', 'swiftc', '-sdk', sdk,
        '-target', f'{architecture}-apple-macos14.0',
        '-module-cache-path', str(root / 'build/module-cache'), '-parse-as-library',
    ]
    extension = output / 'Contents/PlugIns/AGRequests.appex'
    (extension / 'Contents/MacOS').mkdir(parents=True, exist_ok=True)
    (output / 'Contents/MacOS').mkdir(parents=True, exist_ok=True)
    widget_source = 'RequestsWidget.swift' if configurable else 'StaticWidgets.swift'
    extension_arguments = common + [
        '-application-extension', str(root / 'Sources/Snapshot.swift'),
        str(root / 'Sources/RequestsView.swift'), str(root / 'Sources' / widget_source),
        '-o', str(extension / 'Contents/MacOS/AGRequests'),
    ]
    subprocess.run(extension_arguments, env=environment, check=True)
    host_arguments = common + [
        str(root / 'Sources/Host.swift'), '-o', str(output / 'Contents/MacOS/AGRequestsHost'),
    ]
    subprocess.run(host_arguments, env=environment, check=True)
    write_metadata(output)


def main():
    """Build native binaries; OS registration and widget refresh remain separate checks."""
    if sys.platform != 'darwin':
        raise SystemExit('Build the macOS target on macOS')
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--configurable', action='store_true',
        help='Compile AppIntent variant; full Xcode metadata is required for configuration',
    )
    parser.add_argument(
        '--xcode', action='store_true',
        help='Use full Xcode to extract optional AppIntent metadata',
    )
    options = parser.parse_args()
    root = Path(__file__).resolve().parent
    output = root / 'build/AG Requests Widgets.app'
    environment = build_environment(options.xcode)
    if options.xcode:
        build_with_xcode(root, output, environment, options.configurable)
        sign_bundle(root, output)
        print(output)
        return
    build_with_swift(root, output, environment, options.configurable)
    sign_bundle(root, output)
    if options.configurable:
        note = 'AppIntent compile only; full Xcode metadata required'
    else:
        note = 'basic StaticConfiguration; OS registration/refresh tested separately'
    print(f'{output} ({note})')


if __name__ == '__main__':
    main()
