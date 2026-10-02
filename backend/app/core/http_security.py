"""Small ASGI boundary: private responses and bounded JSON bodies for sign-in,
sign-up, verification and guest access.

Avoid BaseHTTPMiddleware so cancellation and request-scoped actor context
retain Starlette's normal behavior (including the upload crash tests).
"""
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse

# Public POST endpoints that create sessions or accounts (app/api/auth.py).
ACCOUNT_POST_PATHS = frozenset({"/api/auth/login", "/api/auth/register", "/api/auth/verify-email",
                                "/api/auth/resend-code", "/api/auth/guest"})


class HTTPProtection:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def private_send(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "same-origin"
            await send(message)

        if scope["path"] in ACCOUNT_POST_PATHS and scope["method"] == "POST":
            headers = Headers(scope=scope)
            if headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
                return await JSONResponse({"detail": "Sign-in requires application/json."}, status_code=415)(scope, receive, private_send)
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > 16_384:
                    return await JSONResponse({"detail": "Sign-in request is too large."}, status_code=413)(scope, receive, private_send)
                if not message.get("more_body", False):
                    break
            sent = False

            async def buffered_receive():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            return await self.app(scope, buffered_receive, private_send)
        await self.app(scope, receive, private_send)
