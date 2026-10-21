from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    s = get_settings()
    return create_engine(s.db_url(), pool_pre_ping=True, pool_size=5, connect_args={"connect_timeout": 5})
