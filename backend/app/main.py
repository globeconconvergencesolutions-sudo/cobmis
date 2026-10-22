from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin_routes import router as admin_router
from app.api.ask_routes import router as ask_router
from app.api.auth_routes import router as auth_router
from app.api.catalog_routes import router as catalog_router
from app.api.conversations_routes import router as conversations_router
from app.api.documents_routes import router as documents_router
from app.api.health import router as health_router
from app.core.config import get_settings


def create_app() -> FastAPI:
    get_settings()  # fail fast on bad config / non-loopback endpoints
    app = FastAPI(title="COB County Budget Intelligence Agent", docs_url="/docs", redoc_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:3000",
            "http://localhost:3000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(ask_router)
    app.include_router(catalog_router)
    app.include_router(documents_router)
    app.include_router(conversations_router)
    app.include_router(admin_router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run("app.main:app", host=s.api_host, port=s.api_port)
