"""Point d'entrée FastAPI — dashboard Visio Gemini (:8004)."""

from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from modules.config import load_app_config
from modules.dashboard.btc_price import get_market_spot
from modules.dashboard.routes import register_png_url_filter, router

DASHBOARD_DIR = Path(__file__).resolve().parent


def create_app() -> FastAPI:
    app_config = load_app_config()
    templates = Jinja2Templates(directory=str(DASHBOARD_DIR / "templates"))
    register_png_url_filter(templates, app_config.paths.captures)
    templates.env.globals["market_spot"] = get_market_spot

    app = FastAPI(title="Visio Gemini Dashboard")
    app.state.templates = templates
    app.state.captures_dir = app_config.paths.captures

    app.mount(
        "/static",
        StaticFiles(directory=str(DASHBOARD_DIR / "static")),
        name="static",
    )
    app.mount(
        "/captures",
        StaticFiles(directory=str(app_config.paths.captures)),
        name="captures",
    )
    app.include_router(router)
    return app


app = create_app()


if __name__ == "__main__":
    cfg = load_app_config()
    uvicorn.run(
        "modules.dashboard.app:app",
        host=cfg.dashboard.host,
        port=cfg.dashboard.port,
        reload=False,
    )
