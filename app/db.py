from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.models import Tariff

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INITIAL_TARIFFS = (
    {"title": "basic", "price": 990_000},
    {"title": "standard", "price": 1_990_000},
    {"title": "premium", "price": 2_990_000},
)


def make_engine(database_url: str) -> Engine:
    if not database_url.startswith("sqlite:///") or database_url.endswith(":memory:"):
        raise ValueError("Use a file SQLite URL: sqlite:///./payments.db")
    engine = create_engine(database_url, connect_args={"check_same_thread": False, "timeout": 10})

    @event.listens_for(engine, "connect")
    def configure_connection(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def initialize_database(engine: Engine) -> None:
    # Alembic is the only schema owner, including a plain uvicorn startup.
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    with Session(engine) as session, session.begin():
        session.execute(
            insert(Tariff)
            .values(INITIAL_TARIFFS)
            .on_conflict_do_nothing(index_elements=[Tariff.title])
        )
