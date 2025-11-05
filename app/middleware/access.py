from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.config import get_settings


class AccessControlMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        user_id = request.headers.get("X-User-Id")
        tenant_id = request.headers.get(settings.tenant_header)

        # TODO: enforce subscription/trial strictly (сейчас soft)
        if request.url.path.startswith("/health") or request.url.path.startswith("/openapi.json"):
            return await call_next(request)

        if not user_id:
            # Пока пропускаем, но логируем — в будущем можно вернуть 401/402.
            request.state.access_checked_at = datetime.now(timezone.utc)
            return await call_next(request)

        request.state.access_checked_at = datetime.now(timezone.utc)
        request.state.access_user_id = user_id
        request.state.access_tenant_id = tenant_id
        # В дальнейшем здесь можно добавить проверку в БД через session_scope().
        return await call_next(request)
