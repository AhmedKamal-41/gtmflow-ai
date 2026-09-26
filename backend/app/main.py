from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import CSRF_HEADER, authenticate
from app.api.routes import router
from app.core.actor import install_event_hook
from app.core.config import settings

# Phase 12: every route requires a signed-in operator unless it is public
# (health checks and login); see app/api/auth.py.
app = FastAPI(title="GTMFlow AI API", version="0.1.0", dependencies=[Depends(authenticate)])
install_event_hook()

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", CSRF_HEADER],
)

app.include_router(router)
