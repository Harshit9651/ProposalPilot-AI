from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "ProposalPilot AI"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True

    GOOGLE_API_KEY: str = ""

    MODEL_NAME: str = "gemini-2.5-flash"
    EMBEDDING_MODEL: str = "models/text-embedding-004"

    CHROMA_DB: str = "./chroma_db"

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True
    )


settings = Settings()