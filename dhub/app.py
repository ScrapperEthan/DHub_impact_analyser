from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, Annotated

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints

from .llm import LLMError
from .store import TERMINAL_STATUSES, RunStore

if TYPE_CHECKING:
    from .config import Settings


STATIC_DIR = Path(__file__).with_name("static")
LOGGER = logging.getLogger(__name__)
FAILED_MESSAGE = "The analysis did not produce a report. See the trace for the completed steps."
RUN_NOT_FOUND = "Analysis run not found."
MAX_CHAT_TURNS = 12
MAX_CHAT_CONTEXT_CHARS = 32_000
EVENT_STREAM_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
EVENT_STREAM_TYPE = "application/x-ndjson"
UserText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]


class RunRequest(BaseModel):
    requirement: UserText


class ChatTurn(BaseModel):
    user: UserText
    assistant: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=12_000)]


class ChatRequest(BaseModel):
    message: UserText
    history: list[ChatTurn] = Field(default_factory=list, max_length=MAX_CHAT_TURNS)

    def messages(self) -> list[dict[str, str]]:
        messages = [{"role": "user", "content": self.message}]
        chars = len(self.message)
        for turn in reversed(self.history):
            chars += len(turn.user) + len(turn.assistant)
            if chars > MAX_CHAT_CONTEXT_CHARS:
                break
            messages.extend((
                {"role": "assistant", "content": turn.assistant},
                {"role": "user", "content": turn.user},
            ))
        return messages[::-1]


def _execute_run(settings: Settings, store: RunStore, run_id: str, requirement: str) -> None:
    if not store.start_run(run_id):
        return
    try:
        from .analyzer import TIME_LIMIT, analyze

        status, report, detail = analyze(
            settings,
            requirement,
            lambda event: store.append_trace(run_id, event),
            lambda snapshot: store.set_snapshot(run_id, snapshot),
        )
        if status not in TERMINAL_STATUSES or (status != "failed" and not isinstance(report, dict)):
            raise ValueError("Invalid analyzer result")
        if status == "failed" and detail == TIME_LIMIT:
            store.finish_run(run_id, status, None, TIME_LIMIT, "ANALYSIS_TIMEOUT")
        elif status == "failed":
            store.finish_run(run_id, status, None, FAILED_MESSAGE, "ANALYSIS_FAILED")
        elif status == "partial" and detail:
            store.finish_run(run_id, status, report, "The analysis is partial. See the report and trace for details.", "ANALYSIS_INCOMPLETE")
        else:
            store.finish_run(run_id, status, report, None)
    except Exception as exc:
        LOGGER.error("Analysis failed for run %s (%s)", run_id, type(exc).__name__)
        store.finish_run(run_id, "failed", None, FAILED_MESSAGE, "ANALYSIS_FAILED")


def _event_line(event: dict) -> str:
    return json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n"


def _chat_failure(exc: Exception) -> tuple[int, str]:
    from .analyzer import TIME_LIMIT

    if isinstance(exc, LLMError):
        if str(exc) == TIME_LIMIT:
            return 504, TIME_LIMIT
        return 502, f"The agent could not reply. {exc}"
    LOGGER.error("Chat failed (%s)", type(exc).__name__)
    return 500, "Chat is temporarily unavailable. Please try again."


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

    @application.get("/chat", response_class=FileResponse, include_in_schema=False)
    def chat_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "chat.html", media_type="text/html")

    @application.get("/api/health")
    def health(request: Request) -> dict:
        config = request.app.state.config
        return {
            "status": "ok", "data_mode": config.data_mode,
            "chat_mode": "offline" if config.llm.provider == "offline" else "online",
        }

    @application.post("/api/chat")
    async def chat(payload: ChatRequest, request: Request, response: Response) -> dict[str, str]:
        from .analyzer import answer_chat

        state = request.app.state
        try:
            reply = await asyncio.get_running_loop().run_in_executor(
                state.executor, answer_chat, state.config, payload.messages(),
            )
        except Exception as exc:
            status, detail = _chat_failure(exc)
            raise HTTPException(status_code=status, detail=detail) from None
        response.headers["Cache-Control"] = "no-store"
        return {"reply": reply}

    @application.post("/api/chat/stream", response_class=StreamingResponse)
    async def stream_chat(payload: ChatRequest, request: Request) -> StreamingResponse:
        from .analyzer import answer_chat

        state = request.app.state
        messages = payload.messages()

        async def events():
            loop = asyncio.get_running_loop()
            queue = asyncio.Queue()
            disconnected = Event()

            def publish(event: dict) -> None:
                if not disconnected.is_set():
                    loop.call_soon_threadsafe(queue.put_nowait, event)

            def work() -> None:
                try:
                    reply = answer_chat(state.config, messages, publish)
                except Exception as exc:
                    status, detail = _chat_failure(exc)
                    publish({"type": "chat.error", "status": status, "detail": detail})
                else:
                    publish({"type": "chat.completed", "reply": reply})

            future = loop.run_in_executor(state.executor, work)
            try:
                while True:
                    event = await queue.get()
                    yield _event_line(event)
                    if event["type"] in {"chat.completed", "chat.error"}:
                        return
            finally:
                disconnected.set()
                future.cancel()

        return StreamingResponse(events(), media_type=EVENT_STREAM_TYPE, headers=EVENT_STREAM_HEADERS)

    @application.post("/api/runs", status_code=202)
    def create_run(payload: RunRequest, request: Request) -> dict:
        state = request.app.state
        run_id = state.store.create_run(payload.requirement, state.config.data_mode)
        try:
            state.executor.submit(_execute_run, state.config, state.store, run_id, payload.requirement)
        except RuntimeError:
            message = "The analysis service is shutting down."
            state.store.finish_run(run_id, "failed", None, message, "SERVICE_STOPPING")
            raise HTTPException(status_code=503, detail=message) from None
        return {"run_id": run_id, "status": "queued"}

    @application.get("/api/runs/{run_id}")
    def get_run(run_id: str, request: Request, response: Response) -> dict:
        run = request.app.state.store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=RUN_NOT_FOUND)
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
            raise HTTPException(status_code=404, detail=RUN_NOT_FOUND)
        response.headers["Cache-Control"] = "no-store"
        return {"events": events, "next_after_seq": events[-1]["seq"] if events else after_seq}

    @application.get("/api/runs/{run_id}/stream", response_class=StreamingResponse)
    async def stream_run(
        run_id: str,
        request: Request,
        after_seq: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
    ) -> StreamingResponse:
        store = request.app.state.store
        initial = await asyncio.to_thread(store.get_event_batch, run_id, after_seq)
        if initial is None:
            raise HTTPException(status_code=404, detail=RUN_NOT_FOUND)

        async def events():
            cursor = after_seq
            batch = initial
            while batch is not None:
                status, rows = batch
                for event in rows:
                    cursor = event["seq"]
                    yield _event_line(event)
                    if event["type"] == "run.finished":
                        return
                if not rows:
                    if status in TERMINAL_STATUSES:
                        return
                    await asyncio.sleep(0.25)
                batch = await asyncio.to_thread(store.get_event_batch, run_id, cursor)

        return StreamingResponse(events(), media_type=EVENT_STREAM_TYPE, headers=EVENT_STREAM_HEADERS)

    return application


app = create_app()
