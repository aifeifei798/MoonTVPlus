"""Auth routes — `src/app/api/{login,register,logout,change-password}/route.ts`.

Paths and response bodies are kept identical so the existing client libs
(`db.client.ts` posts `{username, password}` to `/api/login` and expects
`{ok: true}` / `{error: ...}`) work without edits.
"""

from __future__ import annotations

from fastapi import Request, Response

from ..auth import clear_auth_cookie, generate_auth_cookie, get_auth_info_from_cookie, set_auth_cookie
from ..config import get_config
from ..env import PASSWORD, USERNAME, STORAGE_TYPE, env, env_raw
from ..routes import json_response
from ..storage.db import Db


# ---------------------------------------------------------------------------
# POST /api/login
# ---------------------------------------------------------------------------


async def login(request: Request) -> Response:
    try:
        body = await request.json()
        username = body.get("username")
        password = body.get("password")

        # 数据库 / redis 模式——校验用户名并尝试连接数据库
        if not username or not isinstance(username, str):
            return json_response({"error": "用户名不能为空"}, 400)
        if not password or not isinstance(password, str):
            return json_response({"error": "密码不能为空"}, 400)

        env_username = env_raw(USERNAME)
        env_password = env_raw(PASSWORD)

        # 可能是站长，直接读环境变量
        if username == env_username and password == env_password:
            response = json_response({"ok": True})
            return set_auth_cookie(response, username, password, "owner", False)
        if username == env_username:
            return json_response({"error": "用户名或密码错误"}, 401)

        config = await get_config()
        user = next((u for u in (config.get("UserConfig") or {}).get("Users") or [] if u.get("username") == username), None)
        if user and user.get("banned"):
            return json_response({"error": "用户被封禁"}, 401)

        try:
            if not await Db.verify_user(username, password):
                return json_response({"error": "用户名或密码错误"}, 401)

            response = json_response({"ok": True})
            return set_auth_cookie(response, username, password, (user or {}).get("role") or "user", False)
        except Exception as error:  # noqa: BLE001
            print("数据库验证失败", error)
            return json_response({"error": "数据库错误"}, 500)
    except Exception as error:  # noqa: BLE001
        print("登录接口异常", error)
        return json_response({"error": "服务器错误"}, 500)


# ---------------------------------------------------------------------------
# POST /api/register
# ---------------------------------------------------------------------------


async def register(request: Request) -> Response:
    try:
        config = await get_config()
        if not (config.get("UserConfig") or {}).get("AllowRegister"):
            return json_response({"error": "当前未开放注册"}, 400)

        body = await request.json()
        username = body.get("username")
        password = body.get("password")

        if not username or not isinstance(username, str):
            return json_response({"error": "用户名不能为空"}, 400)
        if not password or not isinstance(password, str):
            return json_response({"error": "密码不能为空"}, 400)

        if username == env_raw(USERNAME):
            return json_response({"error": "用户已存在"}, 400)

        try:
            if await Db.check_user_exist(username):
                return json_response({"error": "用户已存在"}, 400)

            await Db.register_user(username, password)

            user_config = config.get("UserConfig") or {}
            user_config.setdefault("Users", []).append({"username": username, "role": "user"})
            await Db.save_admin_config(config)

            response = json_response({"ok": True})
            return set_auth_cookie(response, username, None, None, False)
        except Exception as error:  # noqa: BLE001
            print("数据库注册失败", error)
            return json_response({"error": "数据库错误"}, 500)
    except Exception as error:  # noqa: BLE001
        print("注册接口异常", error)
        return json_response({"error": "服务器错误"}, 500)


# ---------------------------------------------------------------------------
# POST /api/logout
# ---------------------------------------------------------------------------


async def logout(request: Request) -> Response:
    return clear_auth_cookie(json_response({"ok": True}))


# ---------------------------------------------------------------------------
# POST /api/change-password
# ---------------------------------------------------------------------------


async def change_password(request: Request) -> Response:
    if env(STORAGE_TYPE, "sqlite") == "localstorage":
        return json_response({"error": "不支持本地存储模式修改密码"}, 400)

    try:
        body = await request.json()
        new_password = body.get("newPassword")

        auth_info = get_auth_info_from_cookie(request)
        if not auth_info or not auth_info.get("username"):
            return json_response({"error": "Unauthorized"}, 401)

        if not new_password or not isinstance(new_password, str):
            return json_response({"error": "新密码不得为空"}, 400)

        username = auth_info["username"]
        if username == env_raw(USERNAME):
            return json_response({"error": "站长不能通过此接口修改密码"}, 403)

        try:
            await Db.change_password(username, new_password)
            return json_response({"ok": True})
        except Exception as error:  # noqa: BLE001
            print("修改密码失败:", error)
            return json_response({"error": "修改密码失败", "details": str(error)}, 500)
    except Exception as error:  # noqa: BLE001
        print("修改密码接口异常:", error)
        return json_response({"error": "服务器错误"}, 500)


ROUTES = [
    ("/api/login", login, ["POST"]),
    ("/api/register", register, ["POST"]),
    ("/api/logout", logout, ["POST"]),
    ("/api/change-password", change_password, ["POST"]),
]
