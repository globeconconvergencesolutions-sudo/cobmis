from fastapi import FastAPI

from app.api.health import router as health_router
from app.core.config import get_settings


def create_app() -> FastAPI:
    get_settings()  # fail fast on bad config / non-loopback endpoints
    app = FastAPI(title="COB County Budget Intelligence Agent", docs_url="/docs", redoc_url=None)
    app.include_router(health_router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run("app.main:app", host=s.api_host, port=s.api_port)
