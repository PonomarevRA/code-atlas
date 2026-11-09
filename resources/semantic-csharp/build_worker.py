"""Build only Atlas's small bridge against existing SDK files; never restore or build a subject project."""
import argparse
import json
from pathlib import Path
import subprocess


def build_worker(dotnet: Path, sdk: Path, output: Path) -> Path:
    """Use the installed compiler directly, avoiding NuGet, MSBuild targets and engine copies."""
    dotnet, sdk, output = dotnet.resolve(), sdk.resolve(), output.resolve()
    root = dotnet.parent
    packs = sorted((root / 'packs' / 'Microsoft.NETCore.App.Ref').glob('8.*/ref/net8.0'))
    if not packs:
        raise ValueError('An installed net8.0 reference pack is required to build the Atlas bridge')
    output.mkdir(parents=True, exist_ok=True)
    target = output / 'Atlas.Semantic.Roslyn.dll'
    sources = Path(__file__).resolve().parent
    references = [*sorted(packs[-1].glob('*.dll')),
                  sdk / 'Roslyn/bincore/Microsoft.CodeAnalysis.dll',
                  sdk / 'Roslyn/bincore/Microsoft.CodeAnalysis.CSharp.dll']
    command = [str(dotnet), str(sdk / 'Roslyn/bincore/csc.dll'), '/noconfig', '/nostdlib+',
               '/target:exe', '/nullable:enable', '/langversion:latest', '/deterministic+',
               '/main:Bootstrap', '/out:' + str(target)]
    command.extend('/reference:' + str(path) for path in references)
    command.extend([str(sources / 'Bootstrap.cs'), str(sources / 'Worker.cs')])
    subprocess.run(command, check=True, timeout=60, cwd=output)
    target.with_suffix('.runtimeconfig.json').write_text(json.dumps({'runtimeOptions': {
        'tfm': 'net8.0', 'rollForward': 'LatestMajor',
        'framework': {'name': 'Microsoft.NETCore.App', 'version': '8.0.0'}}}))
    return target


def main():
    """Require explicit local build inputs; the application never invokes this CLI on map use."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dotnet', type=Path, required=True)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(build_worker(args.dotnet, args.sdk, args.output))


if __name__ == '__main__':
    main()
