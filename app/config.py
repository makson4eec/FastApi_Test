import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./payments.db"
    webhook_secret: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("DATABASE_URL", "sqlite:///./payments.db"),
            webhook_secret=os.getenv("WEBHOOK_SECRET") or None,
        )
