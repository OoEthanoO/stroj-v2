"""FastAPI application: wires the routers, static files and the judge pool."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import auth, config, db
from .api import (
    routes_admin,
    routes_auth,
    routes_contests,
    routes_problems,
    routes_submissions,
)
from .judge import worker
from .judge import sandbox as sandbox_module
from .judge.sandbox import isolation_mode, privilege_drop_target

log = logging.getLogger("stroj")
WEB_DIR = Path(__file__).resolve().parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    config.ensure_dirs()
    config.protect_data_dir()
    db.init_db()

    if privilege_drop_target() is None:
        log.warning(
            "submissions will run as the judge's own account: they can read and "
            "write everything the judge can, including the database. Run the "
            "container as root with a %r account present so privileges can be "
            "dropped per submission.",
            sandbox_module.RUNNER_USER,
        )

    username, password = auth.ensure_admin()
    if password:
        log.warning(
            "created the initial admin account %r with password %r "
            "— change it or set STROJ_ADMIN_PASSWORD",
            username,
            password,
        )

    if config.USE_SANDBOX:
        mode = isolation_mode()
        if mode == "none":
            log.warning(
                "STROJ_SANDBOX is on but no isolation mechanism is usable here: "
                "submissions run with rlimits and RSS monitoring only, with NO "
                "network or filesystem isolation. Do not expose this to untrusted "
                "users."
            )
        elif mode == "unshare-net":
            log.warning(
                "isolation: network namespaces only. Submissions cannot reach the "
                "network, but nothing stops them writing anywhere this process can. "
                "Run the judge in a disposable container."
            )
        else:
            log.info("isolation: %s", mode)

    if config.START_WORKERS:
        worker.start_pool()
    try:
        yield
    finally:
        worker.stop_pool()
        db.close()


app = FastAPI(
    title="stroj",
    description="A self-hosted online judge with contests.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(routes_auth.router)
app.include_router(routes_problems.router)
app.include_router(routes_submissions.router)
app.include_router(routes_contests.router)
app.include_router(routes_admin.router)


@app.get("/healthz", include_in_schema=False)
def healthz():
    pending = db.one(
        "SELECT COUNT(*) AS n FROM submissions WHERE verdict IN ('PENDING', 'JUDGING')"
    )["n"]
    return {"ok": True, "queue": pending}


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB_DIR / "index.html")
