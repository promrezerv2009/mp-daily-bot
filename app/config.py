from functools import lru_cache
from typing import List, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    tz: str = Field("Europe/Moscow", alias="TZ")

    telegram_bot_token: str = Field("", alias="TELEGRAM_BOT_TOKEN")
    admin_ids: List[int] = Field(default_factory=list, alias="ADMIN_IDS")
    manager_ids: List[int] = Field(default_factory=list, alias="MANAGER_IDS")
    bot_recipients: List[int] = Field(default_factory=list, alias="BOT_RECIPIENTS")

    db_url: str = Field(..., alias="DB_URL")

    ozon_client_id: Optional[str] = Field(None, alias="OZON_CLIENT_ID")
    ozon_api_key: Optional[str] = Field(None, alias="OZON_API_KEY")
    wb_token: Optional[str] = Field(None, alias="WB_TOKEN")

    # 🔹 новое поле для уровня логов
    cors_origins: List[str] = Field(default_factory=list, alias="CORS_ORIGINS")
    tenant_header: str = Field("X-Tenant-ID", alias="TENANT_HEADER")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    billing_provider: str = Field("stub", alias="BILLING_PROVIDER")
    free_trial_days: int = Field(7, alias="FREE_TRIAL_DAYS")

    n8n_user: Optional[str] = Field(None, alias="N8N_USER")
    n8n_pass: Optional[str] = Field(None, alias="N8N_PASS")

    # 🔹 таймзона для планировщика
    scheduler_timezone: str = Field("Europe/Moscow", alias="SCHEDULER_TZ")

    # 🔹 новые поля базовых URL для клиентов API
    ozon_api_url: str = Field("https://api-seller.ozon.ru", alias="OZON_API_URL")
    wb_api_url: str = Field("https://statistics-api.wildberries.ru", alias="WB_API_URL")
    api_base_url: str = Field("http://app:8000", alias="API_BASE_URL")


    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @field_validator("log_level", mode="before")
    @classmethod
    def _norm_level(cls, v):
        if not v:
            return "INFO"
        return str(v).upper()

    @field_validator("scheduler_timezone", mode="before")
    @classmethod
    def _norm_sched_tz(cls, v):
        return (str(v).strip() if v else "Europe/Moscow")
        # cron для ежедневного обновления (по умолчанию 06:00 каждый день)
    scheduler_cron: str = Field("0 6 * * *", alias="SCHEDULER_CRON")

    @field_validator("scheduler_cron", mode="before")
    @classmethod
    def _norm_cron(cls, v):
        return (str(v).strip() if v else "0 6 * * *")

    @field_validator("admin_ids", "manager_ids", "bot_recipients", mode="before")
    @classmethod
    def _split_ids(cls, v):
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        return v

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v):
        if isinstance(v, str):
            if not v.strip():
                return []
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v or []



@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
