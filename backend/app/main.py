from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import JSONResponse

from app.api.auth import CSRF_HEADER, authenticate
from app.api.routes import router
from app.core.actor import install_event_hook
from app.core.config import settings
from app.core.http_security import HTTPProtection

# Phase 12: every route requires a signed-in operator unless it is public
# (health checks and login); see app/api/auth.py.
app = FastAPI(title="GTMFlow AI API", version="0.1.0", dependencies=[Depends(authenticate)],
              docs_url=None, redoc_url=None, openapi_url=None)
install_event_hook()

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", CSRF_HEADER],
)

app.include_router(router)
app.add_middleware(HTTPProtection)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
    # FastAPI normally echoes invalid inputs (including a login password).
    # Keep actionable locations/messages, never the submitted values.
    return JSONResponse(status_code=422, content={"detail": [
        {key: item[key] for key in ("type", "loc", "msg") if key in item}
        for item in error.errors()
    ]})


# FastAPI's built-in documentation routes bypass application dependencies.
# Explicit routes inherit the same authentication as the rest of the API.
@app.get("/openapi.json", include_in_schema=False)
def openapi_schema():
    return app.openapi()


@app.get("/docs", include_in_schema=False)
def swagger_docs():
    return get_swagger_ui_html(openapi_url="/openapi.json", title="GTMFlow API")


@app.get("/redoc", include_in_schema=False)
def redoc_docs():
    return get_redoc_html(openapi_url="/openapi.json", title="GTMFlow API")
