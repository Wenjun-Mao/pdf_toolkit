from __future__ import annotations

import hmac

from itsdangerous import BadSignature, URLSafeSerializer
from starlette.requests import Request
from starlette.responses import Response

from .settings import Settings, get_settings

SESSION_COOKIE = "pdfkit_session"


def _serializer(settings: Settings) -> URLSafeSerializer:
    return URLSafeSerializer(settings.session_secret, salt="pdfkit-session")


def authenticate(username: str, password: str, settings: Settings | None = None) -> bool:
    active_settings = settings or get_settings()
    return hmac.compare_digest(username, active_settings.admin_username) and hmac.compare_digest(
        password,
        active_settings.admin_password,
    )


def get_session_user(request: Request, settings: Settings | None = None) -> str | None:
    active_settings = settings or get_settings()
    raw_cookie = request.cookies.get(SESSION_COOKIE)
    if not raw_cookie:
        return None
    try:
        payload = _serializer(active_settings).loads(raw_cookie)
    except BadSignature:
        return None
    username = payload.get("username")
    if username != active_settings.admin_username:
        return None
    return username


def set_session_cookie(response: Response, request: Request, settings: Settings | None = None) -> None:
    active_settings = settings or get_settings()
    token = _serializer(active_settings).dumps({"username": active_settings.admin_username})
    secure_cookie = active_settings.secure_cookies_default or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=secure_cookie,
        max_age=60 * 60 * 24 * 14,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE)
