import json
import os

from config import ADMIN_ID, INVITE_CODE_FILE, USERS_FILE


def load_users() -> dict:
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    return {}


def save_users(users: dict) -> None:
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def get_invite_code() -> str:
    if os.path.exists(INVITE_CODE_FILE):
        with open(INVITE_CODE_FILE, "r") as f:
            return f.read().strip()
    return ""


def set_invite_code(code: str) -> None:
    with open(INVITE_CODE_FILE, "w") as f:
        f.write(code)


def is_authorized(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    users = load_users()
    return str(user_id) in users
