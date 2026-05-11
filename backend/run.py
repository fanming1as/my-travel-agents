"""启动脚本"""

import os

import uvicorn
from app.config import get_settings


if __name__ == "__main__":
    settings = get_settings()

    uvicorn.run(
        "app.api.main:app",
        host=settings.host,
        port=settings.port,
        reload=os.getenv("UVICORN_RELOAD", "false").lower() in {"1", "true", "yes"},
        log_level=settings.log_level.lower(),
    )
