import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI

from app.api.webhook import router as webhook_router

# Load environment variables from .env once at startup
load_dotenv()
# Basic logging config (INFO to stdout)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Reservation Webhook Service", version="0.1.0")
app.include_router(webhook_router, prefix="/api")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "calendar": bool(os.getenv("GOOGLE_CALENDAR_ID"))}
