from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class RefreshTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        token = request.headers.get("X-Refresh-Token")
        request.state.refresh_token = token
        # TODO: replace stub with refresh token validation and automatic rotation.
        return await call_next(request)
