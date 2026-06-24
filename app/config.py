from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://txnuser:txnpass@localhost:5432/txndb"
    redis_url: str = "redis://localhost:6379/0"
    gemini_api_key: str = ""
    llm_provider: str = "gemini"
    ollama_base_url: str = "http://localhost:11434"
    llm_batch_size: int = 15
    llm_max_retries: int = 3

    domestic_merchants: tuple[str, ...] = ("Swiggy", "Ola", "IRCTC")
    valid_categories: tuple[str, ...] = (
        "Food",
        "Shopping",
        "Travel",
        "Transport",
        "Utilities",
        "Cash Withdrawal",
        "Entertainment",
        "Other",
        "Uncategorised",
    )


settings = Settings()
