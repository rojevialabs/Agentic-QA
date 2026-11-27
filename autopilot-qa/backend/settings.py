"""Backend configuration, read from environment variables and backend/.env."""
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "autopilot_qa"

    ollama_url: str = "http://localhost:11434"
    llm_backend: str = "ollama"
    analyzer_model: str = "llama3.1:8b"
    generator_model: str = "qwen2.5-coder:7b"
    healer_model: str = "llama3.1:8b"
    healing_confidence_threshold: float = Field(0.80, ge=0.0, le=1.0)
    healer_prompt_version: str = "v1"  # v2 = explicit label comparison (higher precision, lower recall)

    auth_service_url: str = "http://localhost:4001"
    catalog_service_url: str = "http://localhost:4002"
    cart_service_url: str = "http://localhost:4003"
    order_service_url: str = "http://localhost:4004"
    payment_service_url: str = "http://localhost:4005"

    artifacts_dir: Path = BACKEND_DIR.parent / "artifacts"
    tests_generated_dir: Path = BACKEND_DIR.parent / "tests-generated"
    reports_dir: Path = BACKEND_DIR.parent / "reports"
    dashboard_origin: str = "http://localhost:5173"

    def service_urls(self) -> dict[str, str]:
        return {
            "auth": self.auth_service_url,
            "catalog": self.catalog_service_url,
            "cart": self.cart_service_url,
            "order": self.order_service_url,
            "payment": self.payment_service_url,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
