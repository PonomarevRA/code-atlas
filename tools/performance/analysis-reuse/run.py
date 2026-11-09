"""Audit the existing AnalysisContexts contract using immutable fixture values."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from threading import Event

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from atlas.analysis_contexts import AnalysisContexts, AnalysisContextError
from atlas.analysis_section_service import parser_identity


def audit() -> dict:
    """
    Count factories and test source/provider/scope/lifetime fences without RSS claims.

    These fixtures exercise residency semantics, not application workload speedup.
    Generic contexts trust their caller's key; cached_parser supplies source bytes
    and parser identity in the real source path.
    """
    now, calls = [0], []
    contexts = AnalysisContexts(clock=lambda: now[0], idle_seconds=20)
    identifier = contexts.open("p", "s", "settings1")["contextId"]
    provider = parser_identity("fixture.py")

    def read(source, engine):
        """Ask the existing manager for one immutable source/provider tuple."""
        def factory():
            """Count an actual cache miss in this small controlled probe."""
            calls.append((source, engine))
            return ("captured", source, engine)
        return contexts.value(identifier, "p", "s", "settings1",
                              ("syntax", "fixture.py", source, engine), factory, lambda _: (20, 1))

    try:
        cold = read("hash1", provider)
        warm = read("hash1", provider)
        assert cold == warm and len(calls) == 1
        assert read("hash2", provider) != cold and len(calls) == 2
        assert read("hash1", ("different-provider",)) != cold and len(calls) == 3
        for snapshot, settings in (("different-snapshot", "settings1"), ("s", "settings2")):
            try:
                contexts.check(identifier, "p", snapshot, settings)
            except AnalysisContextError as error:
                assert error.status == 409
            else:
                raise AssertionError("Changed scope was accepted")
        now[0] = 21
        try:
            contexts.check(identifier, "p", "s", "settings1")
        except AnalysisContextError:
            pass
        else:
            raise AssertionError("Idle context did not expire")
    finally:
        contexts.close()

    contexts = AnalysisContexts(max_context_bytes=8)
    identifier = contexts.open("p", "s")["contextId"]
    oversized_calls = []
    try:
        for _ in range(2):
            contexts.value(identifier, "p", "s", "", "oversized",
                           lambda: oversized_calls.append(1) or "large", lambda _: (9, 0))
        assert len(oversized_calls) == 2
        def retired_factory():
            """Model cancellation by retiring ownership before publication."""
            contexts.release(identifier, "p", "s")
            return "late"
        try:
            contexts.value(identifier, "p", "s", "", "late", retired_factory, lambda _: (4, 0))
        except AnalysisContextError:
            pass
        else:
            raise AssertionError("Late result resurrected a retired context")
    finally:
        contexts.close()

    contexts = AnalysisContexts()
    identifier = contexts.open("p", "s")["contextId"]
    entered, release = Event(), Event()
    shared_calls = []
    def shared_factory():
        """Hold one owner so concurrent readers contend on the same key."""
        shared_calls.append(1)
        entered.set()
        assert release.wait(5)
        return ("immutable",)
    def concurrent_read():
        """Read through the public manager without touching registry internals."""
        return contexts.value(identifier, "p", "s", "", "shared", shared_factory, lambda _: (4, 0))
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(concurrent_read) for _ in range(4)]
            assert entered.wait(5)
            release.set()
            assert [future.result(timeout=5) for future in futures] == [("immutable",)] * 4
        assert len(shared_calls) == 1
    finally:
        release.set()
        contexts.close()
    return {"schemaVersion": "atlas-analysis-reuse-audit/v1", "status": "pass",
            "coldWarmFactories": 1, "sourceProviderFactories": len(calls),
            "concurrentReaders": 4, "concurrentFactories": len(shared_calls),
            "oversizedFactories": len(oversized_calls), "scopeMismatchRejected": True,
            "idleExpiryRejected": True, "retiredPublicationRejected": True,
            "parserIdentity": provider, "storage": "existing AnalysisContexts only",
            "limits": "Source/JSON/AST charges are not a measured process RSS limit; no workload speedup claimed."}


if __name__ == "__main__":
    print(json.dumps(audit(), ensure_ascii=False, indent=2))
