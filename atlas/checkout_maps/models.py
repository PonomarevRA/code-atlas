"""One temporary session owns one store/health cache and its access leases."""

from dataclasses import dataclass
from pathlib import Path

TTL_SECONDS = 3600
MAX_SESSIONS = 8
SCHEMA = 'atlas-checkout-maps/v1'
MARKER = '.atlas-checkout-map.json'


@dataclass
class CheckoutEntry:
    """Internal host handle; metadata callers receive independent scalar copies."""
    session_id: str
    path: Path
    record: dict
    deadline: float
    store: object
    health: object
    leases: int = 0
    state: str = 'active'
    cleanup_error: str | None = None

    @property
    def project_id(self):
        """Select this session's own project identity, never a canonical registration."""
        return self.record['projectId']
