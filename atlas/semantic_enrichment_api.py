"""UI and MCP share the application's optional local-engine coordinator."""
from fastapi import Query
from pydantic import BaseModel, Field
from typing import Literal

from .semantic_engine_settings import FIELDS
from .semantic_engine_discovery import engine_diagnostics, semantic_mod, settings_contract
from .store import StoreError


class EnrichmentInput(BaseModel):
    """A pinned map and bounded raw nodes; source paths and commands are never accepted."""
    snapshotId: str = Field(min_length=1, max_length=128)
    nodeIds: list[str] = Field(default_factory=list, max_length=16)
    force: bool = Field(default=False, strict=True)
    model_config = {'extra': 'forbid'}


class EngineSettingsInput(BaseModel):
    """Explicit paths override discovery; empty fields restore automatic mode."""
    paths: dict[str, str] = Field(default_factory=dict, max_length=4)
    mode: Literal['replace', 'patch'] = 'replace'
    model_config = {'extra': 'forbid'}


def register_semantic_enrichment_routes(app, service, store):
    """Expose local-engine status/settings and selected-node requests without requiring an agent."""
    def selected_mod(mod_id):
        """Only installed declarative semantic mods have engine path settings."""
        return semantic_mod(store, mod_id)

    @app.get('/api/semantic-engines/settings')
    def engine_settings():
        """Return path preferences only; this read neither searches nor starts engines."""
        try:
            return {'version': 1, 'mods': service.settings.read(), 'fields': FIELDS,
                    'defaultMode': 'automatic', 'agentRequired': False,
                    'configuration': settings_contract(),
                    'profiles': [{'modId': item['id'], 'providers': item['manifest']['analysis']['semanticProviders']}
                                 for item in store.mods.catalog()['mods']
                                 if item.get('manifest', {}).get('analysis', {}).get('semanticProviders')]}
        except ValueError as error:
            raise StoreError(str(error), 409) from error

    @app.post('/api/mods/{mod_id}/semantic-engine-settings')
    def save_engine_settings(mod_id: str, body: EngineSettingsInput):
        """Persist a reversible explicit choice; compatibility is checked against a selected map."""
        declaration = selected_mod(mod_id)
        try:
            paths = service.settings.save(mod_id, declaration['strategy'], body.paths, mode=body.mode)
        except ValueError as error:
            raise StoreError(str(error), 400) from error
        return {'modId': mod_id, 'paths': paths, 'mode': 'explicit' if paths else 'automatic', 'scope': 'host-global'}

    @app.get('/api/mods/{mod_id}/semantic-engine-diagnostics')
    def diagnostics(mod_id: str, projectId: str | None = Query(default=None, min_length=1, max_length=128)):
        """Find existing path candidates on request; compatibility and saving remain explicit steps."""
        return engine_diagnostics(store, service.settings, mod_id, projectId)

    @app.get('/api/projects/{project_id}/semantic-enrichment')
    def status(project_id: str, snapshotId: str = Query(min_length=1, max_length=128)):
        """Actual UI use schedules only due checks; there is no daily idle task."""
        return service.use(project_id, snapshotId)

    @app.post('/api/projects/{project_id}/semantic-enrichment')
    def request(project_id: str, body: EnrichmentInput):
        """Queue bounded selected-node work or an explicit environment recheck."""
        if any(not identifier or len(identifier) > 4096 for identifier in body.nodeIds):
            raise StoreError('Некорректный идентификатор узла.', 400)
        return service.use(project_id, body.snapshotId, body.nodeIds, force=body.force)
