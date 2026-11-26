import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
MAX_RUN_TIMEOUT = 300
LOCAL_RESPONSES_BASE_URL = "http://127.0.0.1:4141/v1"


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "offline"
    base_url: str = ""
    api_key: str = field(default="", repr=False)
    model: str = ""
    max_tokens: int | None = None
    timeout: float = 40
    reasoning_effort: str = ""
    allow_nonloopback: bool = False


@dataclass(frozen=True)
class Settings:
    data_mode: str = "demo"
    db_path: Path = ROOT / ".local" / "runs.sqlite3"
    manifest_path: Path | None = None
    repositories: list[dict] = field(default_factory=list)
    codegraph_adapter: str = ""
    run_timeout: float = MAX_RUN_TIMEOUT
    llm: LLMConfig = field(default_factory=LLMConfig)

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(ROOT / ".env", interpolate=False)
        env = os.environ

        def llm_value(name: str, default: str = "") -> str:
            return env.get(f"DHUB_LLM_{name}", env.get(f"LLM_{name}", default)).strip()

        mode = env.get("DHUB_DATA_MODE", "demo").strip().lower()
        if mode not in {"demo", "project"}:
            raise ValueError("DHUB_DATA_MODE must be demo or project")
        provider = llm_value("PROVIDER", "offline" if mode == "demo" else "openai_chat").lower()
        if mode == "project" and provider == "offline":
            raise ValueError("Project mode requires a configured LLM provider")
        base_url = llm_value("BASE_URL")
        model = llm_value("MODEL")
        if provider == "openai_chat" and not (base_url and model):
            raise ValueError("Set LLM_BASE_URL and LLM_MODEL for openai_chat")
        if provider == "local_responses" and not model:
            raise ValueError("Set DHUB_LLM_MODEL or LLM_MODEL for local_responses")
        manifest = env.get("DHUB_CONFLUENCE_MANIFEST", "").strip()
        repo_config = env.get("DHUB_REPOS_FILE", "").strip()
        repositories = []
        if mode == "project" and repo_config:
            config_path = ROOT / repo_config
            repositories = json.loads(config_path.read_text(encoding="utf-8-sig"))
            if not isinstance(repositories, list):
                raise ValueError("DHUB_REPOS_FILE must contain a JSON array")
            ids = set()
            for entry in repositories:
                if not isinstance(entry, dict) or not all(
                    isinstance(entry.get(key), str) and entry[key].strip() for key in ("repo_id", "path")
                ):
                    raise ValueError("Every repository requires a repo_id and path")
                if entry["repo_id"] in ids or not isinstance(entry.get("ref", "HEAD"), str):
                    raise ValueError("Use unique repo_id values and string refs")
                ids.add(entry["repo_id"])
                entry["path"] = str((config_path.parent / entry["path"]).resolve())
        run_timeout = float(env.get("DHUB_RUN_TIMEOUT", str(MAX_RUN_TIMEOUT)))
        timeout = float(llm_value("TIMEOUT", "40"))
        token_limit = llm_value("MAX_TOKENS")
        max_tokens = int(token_limit) if token_limit else None
        if not (1 <= run_timeout <= 3600 and 1 <= timeout <= 600) or (max_tokens is not None and max_tokens < 1):
            raise ValueError("Check DHUB_RUN_TIMEOUT, LLM_TIMEOUT and LLM_MAX_TOKENS")
        return cls(
            data_mode=mode,
            db_path=ROOT / env.get("DHUB_DB_PATH", ".local/runs.sqlite3"),
            manifest_path=ROOT / manifest if manifest else None,
            repositories=repositories,
            codegraph_adapter=env.get("DHUB_CODEGRAPH_ADAPTER", "").strip(),
            run_timeout=min(run_timeout, MAX_RUN_TIMEOUT),
            llm=LLMConfig(
                provider=provider, base_url=base_url, api_key=llm_value("API_KEY"), model=model,
                max_tokens=max_tokens, timeout=timeout, reasoning_effort=llm_value("REASONING_EFFORT"),
                allow_nonloopback=llm_value("ALLOW_NONLOOPBACK").lower() in {"1", "true", "yes", "on"},
            ),
        )
