from enum import Enum

from app.config import get_settings


class Role(str, Enum):
    admin = "admin"
    manager = "manager"


settings = get_settings()


def get_role(chat_id: int) -> Role | None:
    if chat_id in settings.admin_ids:
        return Role.admin
    if chat_id in settings.manager_ids:
        return Role.manager
    return None


def ensure_admin(chat_id: int) -> None:
    role = get_role(chat_id)
    if role != Role.admin:
        raise PermissionError("Admin privileges required")
