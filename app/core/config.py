from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    data_dir: Path = ROOT / "data"
    db_filename: str = "engine.db"
    dev_origin: str = "http://localhost:5173"

    @property
    def db_path(self) -> Path:
        return self.data_dir / self.db_filename


settings = Settings()
