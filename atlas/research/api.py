"""Thin local HTTP interface for opt-in research workflows; no project writes or LLM endpoints."""
from fastapi import Query
from fastapi.responses import JSONResponse, Response
from .models import ResearchError, StartResearch


def register_research_routes(app, service):
    """Register bounded jobs, immutable Markdown and explicit freshness without loading executable mods."""
    @app.exception_handler(ResearchError)
    async def research_error(request, error):
        return JSONResponse({'error': str(error)}, status_code=error.status)

    @app.get('/api/research/capabilities')
    def capabilities():
        return service.capabilities()

    @app.get('/api/research/runs')
    def history(projectId: str = Query(min_length=1, max_length=128)):
        return {'runs': service.records.history(projectId)}

    @app.post('/api/research/runs', status_code=202)
    def start(body: StartResearch):
        return service.start(body)

    @app.get('/api/research/runs/{identifier}')
    def read(identifier: str):
        return {**service.records.get(identifier), 'evidence': service.records.evidence(identifier)}

    @app.post('/api/research/runs/{identifier}/cancel')
    def cancel(identifier: str):
        return service.cancel(identifier)

    @app.post('/api/research/runs/{identifier}/resume', status_code=202)
    def resume(identifier: str):
        return service.resume(identifier)

    @app.get('/api/research/runs/{identifier}/freshness')
    def freshness(identifier: str):
        return service.freshness(identifier)

    @app.get('/api/research/runs/{identifier}/document.md')
    def markdown(identifier: str):
        run = service.records.get(identifier)
        if not run['document']:
            raise ResearchError('Документ ещё не создан.', 409)
        return Response(run['document']['markdown'], media_type='text/markdown; charset=utf-8',
                        headers={'Content-Disposition': f'attachment; filename="atlas-research-{identifier}.md"', 'Cache-Control': 'no-store'})
