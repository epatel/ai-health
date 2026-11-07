"""FastAPI dashboard: `aihealth serve --repo PATH`.

Pages read the scan history DB (store/); "Scan now" runs a scan of the repo
and saves it. Everything is server-rendered (Jinja2); charts are ECharts
from a pinned CDN build (with a Subresource Integrity hash), fed with JSON
embedded in the page.

There is no authentication: the dashboard is meant for loopback. Requests
must name an allowed host (DNS rebinding can't read the API from another
site), "Scan now" rejects cross-site posts (CSRF), and pages can't be
framed (clickjacking).
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from aihealth import __version__, store
from aihealth.config import load_config
from aihealth.engine import scan
from aihealth.scoring import band
from aihealth.web import views

HERE = Path(__file__).parent
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
ALL_INTERFACES = frozenset({"0.0.0.0", "::", ""})
SECURITY_HEADERS = {
    "Content-Security-Policy": "frame-ancestors 'none'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}


def allowed_hosts(host: str) -> list[str]:
    """Host header values to accept when serving on ``host``: loopback names
    and the host itself; anything when bound to all interfaces (no name to
    check against: `serve` warns about that)."""
    if host in ALL_INTERFACES:
        return ["*"]
    return sorted(LOOPBACK | {host})


def same_origin(request: Request) -> bool:
    """A browser request from the dashboard itself. Browsers send `Origin`
    on form posts; without it, `Sec-Fetch-Site` still tells a cross-site
    request. Neither header (curl, scripts): allowed."""
    origin = request.headers.get("origin")
    if origin is None:
        return request.headers.get("sec-fetch-site", "none") in ("same-origin", "none")
    return origin == f"{request.url.scheme}://{request.headers.get('host', '')}"


def create_app(repo: str | Path, db: str | Path | None = None, host: str = "127.0.0.1") -> FastAPI:
    repo = Path(repo).resolve()
    db_path = Path(db) if db else store.default_db_path(repo)
    app = FastAPI(title="aihealth", version=__version__, docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts(host))

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["band"] = band
    templates.env.globals.update(repo_name=repo.name, version=__version__)

    def conn() -> Iterator[sqlite3.Connection]:
        c = store.connect(db_path)
        try:
            yield c
        finally:
            c.close()

    # FastAPI resolves this alias at registration time; no `from __future__
    # import annotations` in this module, or it would look for it in globals
    Conn = Annotated[sqlite3.Connection, Depends(conn)]

    def resolve_scan(c: sqlite3.Connection, scan_id: int | None) -> int | None:
        return scan_id if scan_id is not None else store.latest_scan_id(c)

    def page(request: Request, name: str, **ctx) -> HTMLResponse:
        return templates.TemplateResponse(request, name, ctx)

    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request, c: Conn, scan: int | None = None):
        scan_id = resolve_scan(c, scan)
        if scan_id is None:
            return page(request, "empty.html", db=str(db_path))
        data = views.overview(c, scan_id)
        if data is None:
            raise HTTPException(404, f"scan {scan_id} not found")
        return page(request, "overview.html", scans=store.list_scans(c), **data)

    @app.get("/treemap", response_class=HTMLResponse)
    def treemap(request: Request, c: Conn, scan: int | None = None):
        scan_id = resolve_scan(c, scan)
        data = views.overview(c, scan_id) if scan_id is not None else None
        if data is None:
            return RedirectResponse("/", status_code=303)
        tree = views.treemap_tree(data["files"])
        return page(request, "treemap.html", scans=store.list_scans(c), tree=tree, **data)

    @app.get("/file", response_class=HTMLResponse)
    def file(request: Request, path: str, c: Conn, scan: int | None = None):
        scan_id = resolve_scan(c, scan)
        data = views.file_view(c, scan_id, path) if scan_id is not None else None
        if data is None:
            raise HTTPException(404, f"{path} not in scan {scan_id}")
        limits = load_config(repo).section("thresholds")
        return page(request, "file.html", scan_id=scan_id, limits=limits, **data)

    @app.post("/scan")
    def scan_now(request: Request, c: Conn):
        if not same_origin(request):
            raise HTTPException(403, "cross-site request")
        config = load_config(repo)
        scan_id = store.save_scan(c, scan(repo, config), config)
        return RedirectResponse(f"/?scan={scan_id}", status_code=303)

    # --- json api (same data as the pages) ---------------------------------------

    @app.get("/api/scans")
    def api_scans(c: Conn, limit: int = 50):
        return store.list_scans(c, limit)

    @app.get("/api/scans/{scan_id}")
    def api_scan(scan_id: int, c: Conn):
        data = store.get_scan(c, scan_id)
        if data is None:
            raise HTTPException(404, "scan not found")
        return {**data, "smells": store.smell_summary(c, scan_id)}

    @app.get("/api/scans/{scan_id}/file")
    def api_file(scan_id: int, path: str, c: Conn):
        data = store.get_file(c, scan_id, path)
        if data is None:
            raise HTTPException(404, "file not found in scan")
        return data

    @app.get("/api/trend")
    def api_trend(c: Conn, path: str | None = None):
        return JSONResponse(store.file_trend(c, path) if path else store.project_trend(c))

    return app


def serve(repo: str | Path, db: str | Path | None, host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(create_app(repo, db, host), host=host, port=port, log_level="warning")
