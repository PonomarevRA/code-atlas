"""Check MCP update prerequisites without touching an installed process or store."""

import asyncio
from copy import deepcopy
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from atlas.agent_mcp import create_mcp
from atlas.process_lock import ProcessLock


def surface_hash(tools: list[dict]) -> str:
    """
    Hash exact tool descriptors conservatively, independent of listing order.

    Descriptor equality cannot prove behavior, core HTTP compatibility or state
    migration. This is an inspection primitive, not an update authorization.
    """
    ordered = sorted(tools, key=lambda tool: tool["name"])
    encoded = json.dumps(ordered, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


async def inspect_surface() -> dict:
    """Read the real local SDK registration without opening HTTP or stdio."""
    server = create_mcp()
    tools = [tool.model_dump(mode="json", exclude_none=True) for tool in await server.list_tools()]
    stable = surface_hash(tools) == surface_hash(list(reversed(tools)))
    changed = deepcopy(tools)
    changed[0]["inputSchema"]["description"] = "Changed contract fixture"
    rejected = surface_hash(tools) != surface_hash(changed)
    return {"toolCount": len(tools), "surfaceHash": surface_hash(tools),
            "listingOrderIndependent": stable, "changedDescriptorDetected": rejected,
            "scope": "exact descriptors only; semantic compatibility remains unverified"}


def inspect_store_coexistence() -> dict:
    """
    Attempt a second source ProcessLock only in an owned temporary directory.

    A same-store second core must not bypass the existing single-owner rule.
    No live server lock, catalog, executable or client is read or modified.
    """
    code = (
        "from pathlib import Path; import sys; from atlas.process_lock import ProcessLock; "
        "lock=ProcessLock(Path(sys.argv[1])); lock.__enter__(); lock.__exit__(None,None,None)"
    )
    with tempfile.TemporaryDirectory(prefix="atlas224-feasibility-") as directory:
        target = Path(directory) / "server.lock"
        with ProcessLock(target):
            blocked = subprocess.run(
                [sys.executable, "-c", code, str(target)], cwd=ROOT,
                text=True, capture_output=True, timeout=10,
            )
        released = subprocess.run(
            [sys.executable, "-c", code, str(target)], cwd=ROOT,
            text=True, capture_output=True, timeout=10,
        )
    expected_failure = "Этот каталог данных открыт другим процессом Code Atlas"
    return {"secondOwnerRejected": blocked.returncode != 0 and expected_failure in blocked.stderr,
            "ownerAfterReleaseAccepted": released.returncode == 0,
            "sameStoreParallelCoresSupported": False,
            "scope": "source ProcessLock, disposable fixture; not a native deployment test"}


def run() -> dict:
    """Combine measured prerequisites with explicit unresolved implementation gates."""
    surface = asyncio.run(inspect_surface())
    storage = inspect_store_coexistence()
    if not all((surface["listingOrderIndependent"], surface["changedDescriptorDetected"],
                storage["secondOwnerRejected"], storage["ownerAfterReleaseAccepted"])):
        raise RuntimeError("A feasibility probe failed")
    return {
        "schemaVersion": "atlas-mcp-update-feasibility/v1",
        "probeStatus": "pass", "seamlessCoreUpdateQualified": False,
        "sdkVersion": version("mcp"), "surface": surface, "storage": storage,
        "gates": [
            {"id": "core-contract", "status": "unqualified",
             "needs": "Versioned internal HTTP/semantic/storage compatibility, beyond descriptor equality."},
            {"id": "core-coexistence", "status": "not-supported-by-current-topology",
             "needs": "Separate read-only compute workers or a reviewed single-writer state handoff; never bypass server.lock."},
            {"id": "inflight-drain", "status": "not-implemented",
             "needs": "Generation leases and demonstrated cancel/progress/job/handle ownership."},
            {"id": "native-readers", "status": "separate-required-qualification",
             "needs": "Exact frozen bytes and old-reader proof; a source fixture is insufficient."},
            {"id": "ld-leiden", "status": "not-implemented",
             "needs": "Explicit hierarchy/refined-parent/aggregate delta state and a full objective oracle."},
        ],
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
