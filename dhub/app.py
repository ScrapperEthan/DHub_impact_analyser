from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StringConstraints

from .store import TERMINAL_STATUSES, RunStore

if TYPE_CHECKING:
    from .config import Settings


STATIC_DIR = Path(__file__).with_name("static")
LOGGER = logging.getLogger(__name__)
FAILED_MESSAGE = "本次分析未生成报告。请查看 Trace 中的已执行步骤。"


class RunRequest(BaseModel):
    requirement: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]


def _execute_run(settings: Settings, store: RunStore, run_id: str, requirement: str) -> None:
    if not store.start_run(run_id):
        return
    try:
        from .analyzer import analyze

        status, report, detail = analyze(
            settings,
            requirement,
            lambda event: store.append_trace(run_id, event),
            lambda snapshot: store.set_snapshot(run_id, snapshot),
        )
        if status not in TERMINAL_STATUSES or (status != "failed" and not isinstance(report, dict)):
            raise ValueError("Invalid analyzer result")
        if status == "failed":
            store.finish_run(run_id, status, None, FAILED_MESSAGE, "ANALYSIS_FAILED")
        elif status == "partial" and detail:
            store.finish_run(run_id, status, report, "部分分析未完成，详情见报告与 Trace。", "ANALYSIS_INCOMPLETE")
        else:
            store.finish_run(run_id, status, report, None)
    except Exception as exc:
        LOGGER.error("Analysis failed for run %s (%s)", run_id, type(exc).__name__)
        store.finish_run(run_id, "failed", None, FAILED_MESSAGE, "ANALYSIS_FAILED")


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        config = settings
        if config is None:
            from .config import Settings

            config = Settings.from_env()
        store = RunStore(config.db_path)
        store.recover_unfinished()
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dhub")
        application.state.config = config
        application.state.store = store
        application.state.executor = executor
        try:
            yield
        finally:
            await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)
            store.recover_unfinished()

    application = FastAPI(title="DHub Process Impact Analyser", lifespan=lifespan)
    application.mount("/static", StaticFiles(directory=STATIC_DIR, check_dir=False), name="static")

    @application.get("/", response_class=FileResponse, include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    @application.get("/api/health")
    def health(request: Request) -> dict:
        return {"status": "ok", "data_mode": request.app.state.config.data_mode}

    @application.post("/api/runs", status_code=202)
    def create_run(payload: RunRequest, request: Request) -> dict:
        state = request.app.state
        run_id = state.store.create_run(payload.requirement, state.config.data_mode)
        try:
            state.executor.submit(_execute_run, state.config, state.store, run_id, payload.requirement)
        except RuntimeError:
            message = "分析服务正在停止。"
            state.store.finish_run(run_id, "failed", None, message, "SERVICE_STOPPING")
            raise HTTPException(status_code=503, detail=message) from None
        return {"run_id": run_id, "status": "queued"}

    @application.get("/api/runs/{run_id}")
    def get_run(run_id: str, request: Request, response: Response) -> dict:
        run = request.app.state.store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="找不到该运行。")
        response.headers["Cache-Control"] = "no-store"
        return run

    @application.get("/api/runs/{run_id}/events")
    def get_events(
        run_id: str,
        request: Request,
        response: Response,
        after_seq: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
    ) -> dict:
        events = request.app.state.store.get_events(run_id, after_seq)
        if events is None:
            raise HTTPException(status_code=404, detail="找不到该运行。")
        response.headers["Cache-Control"] = "no-store"
        return {"events": events, "next_after_seq": events[-1]["seq"] if events else after_seq}

    return application


app = create_app()
