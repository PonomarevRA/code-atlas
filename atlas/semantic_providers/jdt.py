"""Optional JDT-LS candidate references over static source-only Eclipse metadata."""
from pathlib import Path
import os
import re
import tempfile
import xml.etree.ElementTree as ET

from .lsp_base import CapturedLspProvider, read_context
from ..semantic_lsp import LspSession
from ..semantic_process import discover_executable, run_bounded


class JdtProvider(CapturedLspProvider):
    """Never import Maven/Gradle or run analyzed builders/annotation processors."""
    language, binary = 'java', 'jdtls'
    known_locations = ('/opt/homebrew/bin/jdtls', '/usr/local/bin/jdtls', '~/.local/bin/jdtls')
    settings = {'java': {'autobuild': {'enabled': False},
        'import': {'maven': {'enabled': False}, 'gradle': {'enabled': False,
            'wrapper': {'enabled': False}, 'annotationProcessing': {'enabled': False}}},
        'configuration': {'updateBuildConfiguration': 'disabled'},
        'project': {'referencedLibraries': []}, 'server': {'launchMode': 'Standard'}}}

    @staticmethod
    def _source_roots(root):
        """Reject external classpaths, linked projects and arbitrary classpath containers."""
        payload = read_context(root / '.classpath')
        if b'<!' in payload:
            raise ValueError('Unsupported XML metadata')
        try:
            document = ET.fromstring(payload)
        except ET.ParseError as error:
            raise ValueError('Invalid Java classpath XML') from error
        if document.tag != 'classpath' or len(document) > 32:
            raise ValueError('Invalid bounded Java classpath')
        sources = []
        for entry in document:
            kind, path = entry.get('kind'), entry.get('path', '')
            if kind == 'src':
                if Path(path).is_absolute() or '..' in Path(path).parts:
                    raise ValueError('External Java source root')
                (root / path).resolve().relative_to(root.resolve())
                sources.append(path)
            elif kind == 'con' and path.startswith('org.eclipse.jdt.launching.JRE_CONTAINER'):
                continue
            elif kind == 'output':
                continue
            else:
                raise ValueError('Unqualified Java dependency closure')
        if not sources:
            raise ValueError('Missing Java source roots')
        return sources

    def context_assets(self, root):
        """Require a real JDK21+ and a safe static classpath before any JDT launch."""
        self._source_roots(root)
        candidates = [Path(os.environ['JAVA_HOME']) / 'bin/java'] if os.environ.get('JAVA_HOME') else []
        java = discover_executable('java', project_candidates=candidates)
        if java is None:
            raise ValueError('JDK is missing')
        release = java.resolve().parents[1] / 'release'
        text = read_context(release).decode('utf-8')
        version = re.search(r'JAVA_VERSION="(\d+)', text)
        if version is None or int(version[1]) < 21:
            raise ValueError('JDT requires JDK21+')
        process = run_bounded([str(java), '-version'], cwd=root, timeout=3, max_output_bytes=4096)
        if process.returncode:
            raise ValueError('JDK is unavailable')
        return root / '.classpath', java, release

    def prepare_context(self, root, workspace, contents):
        """Write host-owned Eclipse metadata; original builders, factories and preferences are not copied."""
        roots = self._source_roots(root)
        for path in contents:
            if not any(not source or Path(source) in Path(path).parents for source in roots):
                raise ValueError('Captured Java source outside static source roots')
        project = ET.Element('projectDescription')
        ET.SubElement(project, 'name').text = 'AtlasCaptured'
        ET.SubElement(project, 'buildSpec')
        natures = ET.SubElement(project, 'natures')
        ET.SubElement(natures, 'nature').text = 'org.eclipse.jdt.core.javanature'
        (workspace / '.project').write_bytes(ET.tostring(project))
        classpath = ET.Element('classpath')
        for source in roots:
            ET.SubElement(classpath, 'classpathentry', kind='src', path=source)
        ET.SubElement(classpath, 'classpathentry', kind='con',
                      path='org.eclipse.jdt.launching.JRE_CONTAINER')
        ET.SubElement(classpath, 'classpathentry', kind='output', path='.atlas-output')
        (workspace / '.classpath').write_bytes(ET.tostring(classpath))
        preferences = workspace / '.settings'
        preferences.mkdir()
        (preferences / 'org.eclipse.jdt.core.prefs').write_text(
            'eclipse.preferences.version=1\norg.eclipse.jdt.core.compiler.processAnnotations=disabled\n')
        return ('jdt-engine-and-classpath-closure-not-qualified', 'maven-gradle-builders-and-processors-disabled')

    def command(self, engine, workspace):
        """Keep JDT workspace/configuration caches out of both the project and installation."""
        return [str(engine), '-data', str(workspace / '.jdt-data'),
                '-configuration', str(workspace / '.jdt-configuration')]

    def initialization_options(self):
        """Disable build importers in the initial handshake, before workspace discovery."""
        return {'settings': self.settings, 'bundles': []}

    def probe_version(self, engine, root, cancel):
        """The JDT wrapper has no stable version flag: require its real read-only LSP handshake."""
        with tempfile.TemporaryDirectory(prefix='atlas-jdt-probe-') as directory:
            workspace = Path(directory)
            self.prepare_context(root, workspace, {})
            with LspSession(self.command(engine, workspace), workspace, cancel, timeout=7,
                            settings=self.settings, environment=self.environment(workspace)) as session:
                info = session.initialize(workspace, self.initialization_options()).get('serverInfo', {})
                return str(info.get('name', '')) + ' ' + str(info.get('version', ''))

    def version_supported(self, text):
        """Unknown engines and versionless wrappers never qualify as a JDT backend."""
        return re.search(r'(?i)(?:jdt|java).*\b1\.\d+', text) is not None
