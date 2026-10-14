from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker
from starlette.concurrency import run_in_threadpool

from app.api import payment_error_response, router
from app.config import Settings
from app.db import initialize_database, make_engine
from app.services import PaymentError


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url)
        application.state.engine = engine
        application.state.session_factory = sessionmaker(engine, expire_on_commit=False)
        try:
            await run_in_threadpool(initialize_database, engine)
            yield
        finally:
            engine.dispose()

    application = FastAPI(title="Kvitto payments", version="1.0.0", lifespan=lifespan)
    application.state.settings = settings
    application.add_exception_handler(PaymentError, payment_error_response)
    application.include_router(router)
    return application


app = create_app()
