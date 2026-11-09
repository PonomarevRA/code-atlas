"""Kotlin discovery-only profile: no qualified static importer, so keep baseline."""
from pathlib import Path
from hashlib import sha256
import re
import tempfile

from .lsp_base import CapturedLspProvider
from ..semantic_models import SemanticAvailability, SemanticCoverage, SemanticResult
from ..semantic_process import SemanticProcessError, run_bounded
from ..semantic_runtime import ProbeOutcome


class KotlinProvider(CapturedLspProvider):
    """Do not guess official workspace exports or run legacy Gradle/classpath hooks."""
    language, binary = 'kotlin', 'kotlin-lsp'
    known_locations = ('/opt/homebrew/bin/kotlin-lsp', '/usr/local/bin/kotlin-lsp',
                       '~/.local/bin/kotlin-lsp')

    def probe(self, context, cancel):
        """Inspect installed engine identity in an empty directory; never open the analyzed project."""
        candidates = self.candidates()
        if not candidates:
            return ProbeOutcome(SemanticAvailability('missing', 'kotlin-lsp-not-installed'))
        version = None
        with tempfile.TemporaryDirectory(prefix='atlas-kotlin-probe-') as directory:
            workspace = Path(directory)
            for engine in candidates[:3]:
                try:
                    process = run_bounded([str(engine), '--version'], cwd=workspace, cancel=cancel,
                        timeout=2, max_output_bytes=4096, environment=self.environment(workspace))
                    text = (process.stdout + process.stderr).decode('utf-8', errors='replace').strip()
                    if not process.returncode and re.search(r'(?i)kotlin.*\d+\.\d+', text):
                        version = text[:256]
                        break
                except (OSError, SemanticProcessError):
                    continue
        return ProbeOutcome(SemanticAvailability('incompatible', 'kotlin-static-context-unqualified',
                                                  engine_version=version))

    def analyze(self, context, probe, request, cancel):
        """No semantic worker exists yet: return explicit unsupported coverage, never PASS."""
        reason = 'kotlin-static-context-unqualified'
        fingerprint = context.provider_fingerprint or sha256(b'kotlin-local-v1').hexdigest()
        return SemanticResult(context, self.provider_id, fingerprint,
            probe.availability.engine_fingerprint or fingerprint,
            status='unsupported', coverage={'definition': SemanticCoverage('unsupported', reasons=(reason,)),
                                           'calls': SemanticCoverage('unsupported', reasons=(reason,))},
            diagnostics=(reason,))
