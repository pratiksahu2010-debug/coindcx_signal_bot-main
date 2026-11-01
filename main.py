"""
CoinDCX Modular Crypto Signal Bot v2.0
Render Free Tier Optimized — Signal-only system.
Never places orders. Never executes trades.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from utils.logger import logger, setup_logger
from app.health import router as health_router
from app.lifespan import lifespan

setup_logger()

app = FastAPI(
    title="CoinDCX Signal Bot",
    version="2.0.0",
    description="Modular signal-only crypto bot optimized for Render Free Tier",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(health_router)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return {}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        workers=1,
        log_level=settings.log_level.lower(),
    )