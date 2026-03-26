from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PDFKIT_",
        extra="ignore",
    )

    app_name: str = "PDF Kit"
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    data_dir: Path = Path("data")
    redis_url: str = "redis://redis:6379/0"
    database_url: str = "sqlite:///data/pdfkit.db"
    queue_name: str = "pdfkit"
    job_timeout_seconds: int = 3600
    max_upload_mb: int = 512
    admin_username: str = "admin"
    admin_password: str = "change-me"
    require_login: bool = False
    session_secret: str = "change-me-session-secret"
    secure_cookies_default: bool = False
    run_jobs_inline: bool = False
    preview_width_px: int = 240
    scan_default_strength: float = 0.65
    scan_default_white_point: int = 242
    scan_default_contrast: float = 1.05
    result_ttl_hours: int = 24
    admin_password_secret_file: Path = Path("/run/secrets/pdfkit_admin_password")
    require_login_secret_file: Path = Path("/run/secrets/pdfkit_require_login")
    session_secret_file: Path = Path("/run/secrets/pdfkit_session_secret")

    @model_validator(mode="after")
    def load_docker_secrets(self) -> "Settings":
        self.admin_password = self._read_secret_file(
            self.admin_password_secret_file,
            fallback=self.admin_password,
        )
        self.require_login = self._read_secret_bool(
            self.require_login_secret_file,
            fallback=self.require_login,
        )
        self.session_secret = self._read_secret_file(
            self.session_secret_file,
            fallback=self.session_secret,
        )
        return self

    @computed_field  # type: ignore[misc]
    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @computed_field  # type: ignore[misc]
    @property
    def results_dir(self) -> Path:
        return self.data_dir / "results"

    @computed_field  # type: ignore[misc]
    @property
    def previews_dir(self) -> Path:
        return self.data_dir / "previews"

    @computed_field  # type: ignore[misc]
    @property
    def contact_sheets_dir(self) -> Path:
        return self.data_dir / "contact_sheets"

    @staticmethod
    def _read_secret_file(secret_path: Path, fallback: str) -> str:
        if not secret_path.exists():
            return fallback
        secret_value = secret_path.read_text(encoding="utf-8").strip()
        return secret_value or fallback

    @staticmethod
    def _read_secret_bool(secret_path: Path, fallback: bool) -> bool:
        if not secret_path.exists():
            return fallback
        secret_value = secret_path.read_text(encoding="utf-8").strip().lower()
        if not secret_value:
            return fallback
        if secret_value in {"1", "true", "yes", "on"}:
            return True
        if secret_value in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"Invalid boolean secret value in {secret_path}: {secret_value!r}")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
