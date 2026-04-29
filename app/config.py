from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ebay_client_id: str = ""
    ebay_client_secret: str = ""
    ebay_sandbox: bool = False
    database_url: str = "sqlite:////data/ebay_monitor.db"

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    @property
    def ebay_api_base(self) -> str:
        return "https://api.sandbox.ebay.com" if self.ebay_sandbox else "https://api.ebay.com"


settings = Settings()
