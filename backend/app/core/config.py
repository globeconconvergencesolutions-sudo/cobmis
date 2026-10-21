"""Settings: hardware profile from config.yaml, overridden by environment / .env."""

from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import yaml
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
PROFILE_KEYS = (
    "chat_model",
    "sql_model",
    "embed_model",
    "embed_dim",
    "num_ctx",
    "embed_batch_size",
    "retrieval_top_k",
    "rerank_top_n",
    "reranker_enabled",
)


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def load_profile(name: str, config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or REPO_ROOT / "config.yaml"
    profiles = yaml.safe_load(path.read_text(encoding="utf-8"))["profiles"]
    if name not in profiles:
        raise ValueError(f"Unknown profile {name!r}; expected one of {sorted(profiles)}")
    return profiles[name]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="COB_",
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    profile: str = "small"
    config_path: Path = REPO_ROOT / "config.yaml"

    # Profile-driven; None means "take it from config.yaml".
    chat_model: str | None = None
    sql_model: str | None = None
    embed_model: str | None = None
    embed_dim: int | None = None
    num_ctx: int | None = None
    embed_batch_size: int | None = None
    retrieval_top_k: int | None = None
    rerank_top_n: int | None = None
    reranker_enabled: bool | None = None

    # "mock" swaps in app.llm.mock (deterministic, no server) for sandbox/CI only.
    llm_backend: Literal["ollama", "mock"] = "ollama"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_timeout_s: float = 300.0

    db_host: str = "127.0.0.1"
    db_port: int = Field(5433, validation_alias="POSTGRES_PORT")
    db_name: str = Field("cob", validation_alias="POSTGRES_DB")
    app_password: str = "change-me-app"

    api_host: str = "127.0.0.1"
    api_port: int = 8000

    data_dir: Path = REPO_ROOT / "data"

    # Privacy guard: refuse to start if anything points off-box. Only tests disable this.
    enforce_loopback: bool = True

    @model_validator(mode="after")
    def _fill_from_profile(self) -> "Settings":
        prof = load_profile(self.profile, self.config_path)
        for key in PROFILE_KEYS:
            if getattr(self, key) is None:
                setattr(self, key, prof[key])
        return self

    @model_validator(mode="after")
    def _check_loopback(self) -> "Settings":
        if not self.enforce_loopback:
            return self
        bad = []
        if not _is_loopback(urlparse(self.ollama_url).hostname):
            bad.append(f"COB_OLLAMA_URL={self.ollama_url}")
        if not _is_loopback(self.db_host):
            bad.append(f"COB_DB_HOST={self.db_host}")
        if not _is_loopback(self.api_host):
            bad.append(f"COB_API_HOST={self.api_host}")
        if bad:
            raise ValueError("Non-loopback endpoints are not allowed: " + ", ".join(bad))
        return self

    @field_validator("data_dir")
    @classmethod
    def _resolve_data_dir(cls, v: Path) -> Path:
        return v if v.is_absolute() else (REPO_ROOT / v).resolve()

    def db_url(self, user: str = "cob_app", password: str | None = None) -> str:
        pw = password if password is not None else self.app_password
        return f"postgresql+psycopg://{user}:{pw}@{self.db_host}:{self.db_port}/{self.db_name}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
