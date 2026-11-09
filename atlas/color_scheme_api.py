"""Same-origin theme assets and explicit creation of application-owned declarative mods."""
from fastapi import FastAPI
from fastapi.responses import Response
from .color_scheme_models import ColorScheme
from .color_schemes import scheme_catalog, scheme_styles, create_scheme_package


def register_color_scheme_routes(app: FastAPI, mods):
    """Register palette operations without source reads, graph rebuilds or executable mod imports."""
    @app.get('/api/color-schemes')
    def catalog(safe: bool = False):
        """Return built-ins and only explicitly activated mod revisions."""
        return scheme_catalog(mods, safe=safe)

    @app.get('/api/color-schemes/styles.css')
    def styles(safe: bool = False):
        """Use a real stylesheet so custom palettes work with the existing strict CSP."""
        return Response(scheme_styles(scheme_catalog(mods, safe=safe)), media_type='text/css')

    @app.post('/api/color-schemes', status_code=201)
    def create(scheme: ColorScheme):
        """Save a new disabled JSON-only mod; activation still uses its exact fingerprint."""
        return create_scheme_package(mods, scheme)
