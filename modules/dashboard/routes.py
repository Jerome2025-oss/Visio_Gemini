"""Routes HTTP du dashboard Visio Gemini."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from modules.analyse.orchestrator import run_batch
from modules.analyse.results import AnalysisResult
from modules.config import load_app_config
from modules.dashboard.store import add_run, latest
from modules.selection.builders import build_manual_requests

router = APIRouter()

# Routes en ``def`` sync (pas ``async def``) : FastAPI les exécute dans un thread pool.
# ``run_batch()`` appelle Playwright sync ; dans une coroutine asyncio cela lève
# « Sync API inside the asyncio loop ». Ne pas convertir en async sans migrer la capture.


def _normalize_timeframe(timeframe: str) -> str:
    label = timeframe.strip()
    if label.lower() == "1d":
        return "1D"
    return label


def _results_context(
    request: Request,
    *,
    results: list[AnalysisResult] | None = None,
    run_error: str | None = None,
) -> dict:
    app_config = load_app_config()
    return {
        "request": request,
        "results": results if results is not None else latest(),
        "run_error": run_error,
        "captures_dir": app_config.paths.captures,
        "agents_config": app_config.agents,
    }


def _render_results(
    request: Request,
    *,
    results: list[AnalysisResult] | None = None,
    run_error: str | None = None,
) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "_results.html",
        _results_context(request, results=results, run_error=run_error),
    )


@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    app_config = load_app_config()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "results": latest(),
            "run_error": None,
            "captures_dir": app_config.paths.captures,
            "agents_config": app_config.agents,
            "timeframes": ["15m", "1h", "4h", "1d"],
        },
    )


@router.get("/results", response_class=HTMLResponse)
def results_fragment(request: Request) -> HTMLResponse:
    return _render_results(request)


@router.post("/run", response_class=HTMLResponse)
def run_analysis(
    request: Request,
    symbol: Annotated[str, Form()],
    timeframe: Annotated[str, Form()],
    agents: Annotated[list[str] | None, Form()] = None,
) -> HTMLResponse:
    token = symbol.strip().upper()
    tf = _normalize_timeframe(timeframe)
    if not agents:
        selected_agents: list[str] = []
    elif isinstance(agents, str):
        selected_agents = [agents]
    else:
        selected_agents = list(agents)

    if not token:
        return _render_results(request, results=[], run_error="Symbole requis.")
    if not selected_agents:
        return _render_results(
            request,
            results=[],
            run_error="Sélectionnez au moins un agent.",
        )

    try:
        requests = build_manual_requests(token, tf, agents=selected_agents)
        results = run_batch(requests)
        add_run(results)
        return _render_results(request, results=results)
    except Exception as exc:
        return _render_results(request, results=[], run_error=str(exc))


def register_png_url_filter(templates, captures_dir: Path) -> None:
    """Filtre Jinja2 : chemin PNG absolu → URL /captures/..."""

    def png_url(path: Path | str | None) -> str:
        if not path:
            return ""
        try:
            rel = Path(path).resolve().relative_to(captures_dir.resolve())
            return f"/captures/{rel.as_posix()}"
        except (ValueError, TypeError, OSError):
            return ""

    templates.env.filters["png_url"] = png_url
