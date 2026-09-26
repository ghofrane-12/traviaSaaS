from pydantic_settings import BaseSettings
from typing import Optional
from dotenv import load_dotenv
import os

if os.getenv("K_SERVICE") is None:
    load_dotenv(override=True)
class Settings(BaseSettings):
    # Base de données
    DATABASE_URL: str

    FIREBASE_CREDENTIALS_PATH: Optional[str] = None

    BREVO_SMTP_HOST:     str = "smtp-relay.brevo.com"
    BREVO_SMTP_PORT:     int = 587
    BREVO_SMTP_USER:     str = ""
    BREVO_SMTP_PASSWORD: str = ""
    FRONTEND_URL:        str = "http://localhost:4200"
    BREVO_API_KEY: str = ""

    # APIs externes
    OPENAI_API_KEY: str
    GEMINI_API_KEY: str = ""
    MISTRAL_API_KEY: str = ""
    UNSPLASH_ACCESS_KEY: str = ""
    # Facebook Messenger
    FB_VERIFY_TOKEN: str = "projetpfe"
    FB_PAGE_ACCESS_TOKEN: str = ""
    FB_APP_ID: str = ""          
    FB_APP_SECRET: str = ""      
    FB_REDIRECT_URI: str = "" 

    # CORS
    ALLOWED_ORIGINS: list[str] = [
    "http://localhost:4200",
    "https://travel-saas-pfe.web.app",  
    ]

    UPLOAD_DIR: str = "/app/files"
    GCS_MODELS_PATH: str = os.getenv(
        "GCS_MODELS_PATH",
        "/app/gcs_models/files"
    )
    ng_app_api_url: str = "http://localhost:8000"

    class Config:
        env_file = ".env" if os.getenv("K_SERVICE") is None else None
        extra = "ignore"

settings = Settings()