"""`src/app/api/admin/{config,reset,config_file,user,group,source,category,site,subscription,tvbox}/route.ts`.

These are the routes the admin panel (`src/app/admin/page.tsx`) drives: every
one of them reads the whole admin config, mutates it in place, and writes it
back through `Db.save_admin_config` (upstream's `setAdminConfig`), so the
stored blob is whatever the panel sent, not a reconstruction. The action
vocabulary, the error sentences, and the status codes are all part of the
panel's UI — a 404 vs a 400 is the difference between "分类不存在" and a
generic failure tile — so they are copied rather than tidied.

Two things are shared here instead of being repeated fifteen times the way
upstream repeated them:

  * `_localstorage_guard()` — the `storageType === 'localstorage'` opening
    every route has. It is unreachable with the ported backends (PLAN.md),
    but keeping it means a deployment that does set
    `NEXT_PUBLIC_STORAGE_TYPE=localstorage` still gets the same 400.
  * `_operator_role()` — the "站长 or admin user entry?" ladder. The routes
    disagree on the status (401 everywhere except `/api/admin/tvbox`'s POST,
    which uses 403, and `/api/admin/config_file`, which uses 403), so the
    helper returns the role and each route picks its own code.

`/api/admin/subscription` and `/api/admin/tvbox` export both a GET and a POST
upstream; here they are two handlers registered on the same path, which is
how `moontv.main` turns the pair into method-specific routes.
"""

from __future__ import annotations

import base64
import json
import math
import secrets
import time

from fastapi import Request

from ..auth import get_auth_info_from_cookie
from ..config import config_self_check, drop_config_api_site, drop_config_custom_category, get_config, reset_config
from ..env import PASSWORD, STORAGE_TYPE, TVBOX_ENABLED, USERNAME, env, env_raw
from ..fetch import fetch_with_timeout
from ..request_origin import get_request_origin
from ..routes import find_user, json_response
from ..storage.db import Db
from ..utils import encode_uri_component, js_number

LOCALSTORAGE_ERROR = "不支持本地存储进行管理员配置"

USER_ACTIONS = (
    "add",
    "ban",
    "unban",
    "setAdmin",
    "cancelAdmin",
    "setAllowRegister",
    "changePassword",
    "deleteUser",
)
GROUP_ACTIONS = ("create", "delete", "rename", "setSources", "assignUsers", "removeUsers")
CATEGORY_ACTIONS = ("add", "disable", "enable", "delete", "sort")
SOURCE_ACTIONS = ("add", "disable", "enable", "delete", "sort", "batchDisable", "batchEnable", "batchDelete")
SUBSCRIPTION_ACTIONS = ("update", "import", "check")

# `typeof x === 'string'` / `'number'` / `'boolean'`, which is what the site
# route's parameter validation is written in. `bool` is a subclass of `int` in
# Python but a distinct `typeof` in JS, so every numeric check has to exclude it.
_JS_STRING = str
_JS_NUMBER = (int, float)


def _js_string(value) -> bool:
    return isinstance(value, _JS_STRING)


def _js_number_defined(value) -> bool:
    return isinstance(value, _JS_NUMBER) and not isinstance(value, bool)


def _js_boolean(value) -> bool:
    return isinstance(value, bool)


def _localstorage_guard() -> dict | None:
    """The 400 every admin route starts with, or `None` to keep going."""
    if env(STORAGE_TYPE, "sqlite") == "localstorage":
        return {"error": LOCALSTORAGE_ERROR}
    return None


def _operator_role(admin_config: dict, username: str) -> str | None:
    """`owner` / `admin` / `None` — the站长 check plus the user-entry check."""
    if username == env_raw(USERNAME):
        return "owner"
    entry = find_user(admin_config, username)
    if not entry or entry.get("role") != "admin" or entry.get("banned"):
        return None
    return "admin"


async def _save_admin_config(admin_config) -> None:
    """`if (storage && typeof setAdminConfig === 'function') await ...`."""
    await Db.save_admin_config(admin_config)


# ---------------------------------------------------------------------------
# GET /api/admin/config
# ---------------------------------------------------------------------------


async def admin_config(request: Request):
    gated = _localstorage_guard()
    if gated:
        return json_response(gated, 400)

    auth_info = get_auth_info_from_cookie(request)
    if not auth_info or not auth_info.get("username"):
        return json_response({"error": "Unauthorized"}, 401)
    username = auth_info["username"]

    try:
        config = await get_config()
        result = {"Role": "owner", "Config": config}
        if username == env_raw(USERNAME):
            result["Role"] = "owner"
        else:
            user = find_user(config, username)
            if user and user.get("role") == "admin" and not user.get("banned"):
                result["Role"] = "admin"
            else:
                return json_response({"error": "你是管理员吗你就访问？"}, 401)
        return json_response(result, headers={"Cache-Control": "no-store"})
    except Exception as error:  # noqa: BLE001
        print("获取管理员配置失败:", error)
        return json_response({"error": "获取管理员配置失败", "details": str(error)}, 500)


# ---------------------------------------------------------------------------
# GET /api/admin/reset
# ---------------------------------------------------------------------------


async def admin_reset(request: Request):
    gated = _localstorage_guard()
    if gated:
        return json_response(gated, 400)

    auth_info = get_auth_info_from_cookie(request)
    if not auth_info or not auth_info.get("username"):
        return json_response({"error": "Unauthorized"}, 401)

    if auth_info["username"] != env_raw(USERNAME):
        return json_response({"error": "仅支持站长重置配置"}, 401)

    try:
        await reset_config()
        return json_response({"ok": True}, headers={"Cache-Control": "no-store"})
    except Exception as error:  # noqa: BLE001
        return json_response({"error": "重置管理员配置失败", "details": str(error)}, 500)


# ---------------------------------------------------------------------------
# POST /api/admin/config_file
# ---------------------------------------------------------------------------


async def config_file(request: Request):
    gated = _localstorage_guard()
    if gated:
        return json_response(gated, 400)

    auth_info = get_auth_info_from_cookie(request)
    if not auth_info or not auth_info.get("username"):
        return json_response({"error": "Unauthorized"}, 401)
    username = auth_info["username"]

    try:
        admin_config_dict = await get_config()
        if username != env_raw(USERNAME):
            user = find_user(admin_config_dict, username)
            if not user or user.get("role") != "admin" or user.get("banned"):
                return json_response({"error": "权限不足，只有管理员可以修改配置文件"}, 403)

        body = await request.json()
        raw = body.get("configFile")
        if not raw or not isinstance(raw, str):
            return json_response({"error": "配置文件内容不能为空"}, 400)

        try:
            json.loads(raw)
        except Exception:  # noqa: BLE001 — the TS `JSON.parse` catch
            return json_response({"error": "配置文件格式错误，请检查 JSON 语法"}, 400)

        admin_config_dict["ConfigFile"] = raw
        await _save_admin_config(admin_config_dict)
        return json_response({"success": True, "message": "配置文件更新成功"})
    except Exception as error:  # noqa: BLE001
        print("更新配置文件失败:", error)
        return json_response({"error": "更新配置文件失败", "details": str(error)}, 500)


# ---------------------------------------------------------------------------
# POST /api/admin/user
# ---------------------------------------------------------------------------


async def user(request: Request):
    gated = _localstorage_guard()
    if gated:
        return json_response(gated, 400)

    try:
        body = await request.json()

        auth_info = get_auth_info_from_cookie(request)
        if not auth_info or not auth_info.get("username"):
            return json_response({"error": "Unauthorized"}, 401)
        username = auth_info["username"]

        target_username = body.get("targetUsername")
        target_password = body.get("targetPassword")
        allow_register = body.get("allowRegister")
        action = body.get("action")

        if not action or action not in USER_ACTIONS:
            return json_response({"error": "参数格式错误"}, 400)
        if action != "setAllowRegister" and not target_username:
            return json_response({"error": "缺少目标用户名"}, 400)
        if (
            action not in ("setAllowRegister", "changePassword", "deleteUser")
            and username == target_username
        ):
            return json_response({"error": "无法对自己进行此操作"}, 400)

        admin_config_dict = await get_config()
        operator_role = _operator_role(admin_config_dict, username)
        if operator_role is None:
            return json_response({"error": "权限不足"}, 401)

        target_entry = find_user(admin_config_dict, target_username)
        if target_entry and target_entry.get("role") == "owner" and action != "changePassword":
            return json_response({"error": "无法操作站长"}, 400)

        is_target_admin = (target_entry or {}).get("role") == "admin"

        if action == "setAllowRegister":
            if not _js_boolean(allow_register):
                return json_response({"error": "参数格式错误"}, 400)
            admin_config_dict.setdefault("UserConfig", {})["AllowRegister"] = allow_register
        else:
            match action:
                case "add":
                    if target_entry:
                        return json_response({"error": "用户已存在"}, 400)
                    if not target_password:
                        return json_response({"error": "缺少目标用户密码"}, 400)
                    await Db.register_user(target_username, target_password)
                    users = admin_config_dict.setdefault("UserConfig", {}).setdefault("Users", [])
                    users.append({"username": target_username, "role": "user"})
                    target_entry = users[-1]
                case "ban":
                    if not target_entry:
                        return json_response({"error": "目标用户不存在"}, 404)
                    if is_target_admin and operator_role != "owner":
                        return json_response({"error": "仅站长可封禁管理员"}, 401)
                    target_entry["banned"] = True
                case "unban":
                    if not target_entry:
                        return json_response({"error": "目标用户不存在"}, 404)
                    if is_target_admin and operator_role != "owner":
                        return json_response({"error": "仅站长可操作管理员"}, 401)
                    target_entry["banned"] = False
                case "setAdmin":
                    if not target_entry:
                        return json_response({"error": "目标用户不存在"}, 404)
                    if target_entry.get("role") == "admin":
                        return json_response({"error": "该用户已是管理员"}, 400)
                    if operator_role != "owner":
                        return json_response({"error": "仅站长可设置管理员"}, 401)
                    target_entry["role"] = "admin"
                case "cancelAdmin":
                    if not target_entry:
                        return json_response({"error": "目标用户不存在"}, 404)
                    if target_entry.get("role") != "admin":
                        return json_response({"error": "目标用户不是管理员"}, 400)
                    if operator_role != "owner":
                        return json_response({"error": "仅站长可取消管理员"}, 401)
                    target_entry["role"] = "user"
                case "changePassword":
                    if not target_entry:
                        return json_response({"error": "目标用户不存在"}, 404)
                    if not target_password:
                        return json_response({"error": "缺少新密码"}, 400)
                    if target_entry.get("role") == "owner":
                        return json_response({"error": "无法修改站长密码"}, 401)
                    if is_target_admin and operator_role != "owner" and username != target_username:
                        return json_response({"error": "仅站长可修改其他管理员密码"}, 401)
                    await Db.change_password(target_username, target_password)
                case "deleteUser":
                    if not target_entry:
                        return json_response({"error": "目标用户不存在"}, 404)
                    if username == target_username:
                        return json_response({"error": "不能删除自己"}, 400)
                    if is_target_admin and operator_role != "owner":
                        return json_response({"error": "仅站长可删除管理员"}, 401)
                    await Db.delete_user(target_username)
                    users = admin_config_dict.setdefault("UserConfig", {}).setdefault("Users", [])
                    index = next(
                        (i for i, u in enumerate(users) if u.get("username") == target_username),
                        -1,
                    )
                    if index > -1:
                        users.pop(index)
                case _:
                    return json_response({"error": "未知操作"}, 400)

        await _save_admin_config(admin_config_dict)
        return json_response({"ok": True}, headers={"Cache-Control": "no-store"})
    except Exception as error:  # noqa: BLE001
        print("用户管理操作失败:", error)
        return json_response({"error": "用户管理操作失败", "details": str(error)}, 500)


# ---------------------------------------------------------------------------
# POST /api/admin/group
# ---------------------------------------------------------------------------


async def group(request: Request):
    gated = _localstorage_guard()
    if gated:
        return json_response(gated, 400)

    try:
        body = await request.json()

        auth_info = get_auth_info_from_cookie(request)
        if not auth_info or not auth_info.get("username"):
            return json_response({"error": "Unauthorized"}, 401)
        username = auth_info["username"]

        admin_config_dict = await get_config()
        if _operator_role(admin_config_dict, username) is None:
            return json_response({"error": "权限不足"}, 401)

        action = body.get("action")
        if not action:
            return json_response({"error": "参数格式错误"}, 400)

        user_config = admin_config_dict.setdefault("UserConfig", {})
        groups = user_config.setdefault("Groups", [])
        users = user_config.setdefault("Users", [])

        match action:
            case "create":
                name = body.get("name")
                source_keys = body.get("sourceKeys")
                if not name:
                    return json_response({"error": "缺少分组名称"}, 400)
                if any(g.get("name") == name for g in groups):
                    return json_response({"error": "分组已存在"}, 400)
                groups.append({"name": name, "sourceKeys": source_keys if isinstance(source_keys, list) else []})
            case "delete":
                name = body.get("name")
                if not name:
                    return json_response({"error": "缺少分组名称"}, 400)
                index = next((i for i, g in enumerate(groups) if g.get("name") == name), -1)
                if index == -1:
                    return json_response({"error": "分组不存在"}, 404)
                groups.pop(index)
                # 同步清除用户上的该组标记
                for u in users:
                    if u.get("group") == name:
                        u.pop("group", None)
            case "rename":
                name = body.get("name")
                new_name = body.get("newName")
                if not name or not new_name:
                    return json_response({"error": "缺少分组名称"}, 400)
                if any(g.get("name") == new_name for g in groups):
                    return json_response({"error": "新分组名已存在"}, 400)
                target_group = next((g for g in groups if g.get("name") == name), None)
                if not target_group:
                    return json_response({"error": "分组不存在"}, 404)
                target_group["name"] = new_name
                for u in users:
                    if u.get("group") == name:
                        u["group"] = new_name
            case "setSources":
                name = body.get("name")
                source_keys = body.get("sourceKeys")
                if not name or not isinstance(source_keys, list):
                    return json_response({"error": "参数格式错误"}, 400)
                target_group = next((g for g in groups if g.get("name") == name), None)
                if not target_group:
                    return json_response({"error": "分组不存在"}, 404)
                target_group["sourceKeys"] = source_keys
            case "assignUsers":
                name = body.get("name")
                names = body.get("users")
                if not name or not isinstance(names, list):
                    return json_response({"error": "参数格式错误"}, 400)
                target_group = next((g for g in groups if g.get("name") == name), None)
                if not target_group:
                    return json_response({"error": "分组不存在"}, 404)
                picked = set(names)
                for u in users:
                    if u.get("username") in picked:
                        u["group"] = name
            case "removeUsers":
                names = body.get("users")
                if not isinstance(names, list):
                    return json_response({"error": "参数格式错误"}, 400)
                picked = set(names)
                for u in users:
                    if u.get("username") in picked:
                        u.pop("group", None)
            case _:
                return json_response({"error": "未知操作"}, 400)

        await _save_admin_config(admin_config_dict)
        return json_response({"ok": True}, headers={"Cache-Control": "no-store"})
    except Exception as error:  # noqa: BLE001
        print("分组管理操作失败:", error)
        return json_response({"error": "分组管理操作失败", "details": str(error)}, 500)


# ---------------------------------------------------------------------------
# POST /api/admin/source
# ---------------------------------------------------------------------------


async def source(request: Request):
    gated = _localstorage_guard()
    if gated:
        return json_response(gated, 400)

    try:
        body = await request.json()

        auth_info = get_auth_info_from_cookie(request)
        if not auth_info or not auth_info.get("username"):
            return json_response({"error": "Unauthorized"}, 401)
        username = auth_info["username"]

        action = body.get("action")
        if not username or not action or action not in SOURCE_ACTIONS:
            return json_response({"error": "参数格式错误"}, 400)

        admin_config_dict = await get_config()
        if _operator_role(admin_config_dict, username) is None:
            return json_response({"error": "权限不足"}, 401)

        sources = admin_config_dict.setdefault("SourceConfig", [])

        match action:
            case "add":
                key = body.get("key")
                name = body.get("name")
                api = body.get("api")
                detail = body.get("detail")
                if not key or not name or not api:
                    return json_response({"error": "缺少必要参数"}, 400)
                if any(s.get("key") == key for s in sources):
                    return json_response({"error": "该源已存在"}, 400)
                sources.append(
                    {
                        "key": key,
                        "name": name,
                        "api": api,
                        "detail": detail,
                        "from": "custom",
                        "disabled": False,
                    }
                )
            case "disable" | "enable":
                key = body.get("key")
                if not key:
                    return json_response({"error": "缺少 key 参数"}, 400)
                entry = next((s for s in sources if s.get("key") == key), None)
                if not entry:
                    return json_response({"error": "源不存在"}, 404)
                entry["disabled"] = action == "disable"
            case "delete":
                key = body.get("key")
                if not key:
                    return json_response({"error": "缺少 key 参数"}, 400)
                index = next((i for i, s in enumerate(sources) if s.get("key") == key), -1)
                if index == -1:
                    return json_response({"error": "源不存在"}, 404)
                # Upstream refused these with 该源不可删除 and batchDelete skipped
                # them while still answering {"ok":true}; both now do what the panel
                # promises, which needs the ConfigFile pruned too (PLAN.md, change 17).
                sources.pop(index)
                drop_config_api_site(admin_config_dict, [key])
            case "sort":
                order = body.get("order")
                if not isinstance(order, list):
                    return json_response({"error": "排序列表格式错误"}, 400)
                remaining = {s.get("key"): s for s in sources}
                new_list = []
                for key in order:
                    item = remaining.pop(key, None)
                    if item is not None:
                        new_list.append(item)
                # 未在 order 中的保持原顺序
                new_list.extend(item for item in sources if item.get("key") in remaining)
                admin_config_dict["SourceConfig"] = new_list
            case "batchDisable" | "batchEnable":
                keys = body.get("keys")
                if not isinstance(keys, list) or len(keys) == 0:
                    return json_response({"error": "缺少 keys 参数或列表为空"}, 400)
                disabled = action == "batchDisable"
                for key in keys:
                    entry = next((s for s in sources if s.get("key") == key), None)
                    if entry is not None:
                        entry["disabled"] = disabled
            case "batchDelete":
                keys = body.get("keys")
                if not isinstance(keys, list) or len(keys) == 0:
                    return json_response({"error": "缺少 keys 参数或列表为空"}, 400)
                for key in keys:
                    index = next((i for i, s in enumerate(sources) if s.get("key") == key), -1)
                    if index != -1:
                        sources.pop(index)
                drop_config_api_site(admin_config_dict, keys)
            case _:
                return json_response({"error": "未知操作"}, 400)

        await _save_admin_config(admin_config_dict)
        return json_response({"ok": True}, headers={"Cache-Control": "no-store"})
    except Exception as error:  # noqa: BLE001
        print("视频源管理操作失败:", error)
        return json_response({"error": "视频源管理操作失败", "details": str(error)}, 500)


# ---------------------------------------------------------------------------
# POST /api/admin/category
# ---------------------------------------------------------------------------


async def category(request: Request):
    gated = _localstorage_guard()
    if gated:
        return json_response(gated, 400)

    try:
        body = await request.json()

        auth_info = get_auth_info_from_cookie(request)
        if not auth_info or not auth_info.get("username"):
            return json_response({"error": "Unauthorized"}, 401)
        username = auth_info["username"]

        action = body.get("action")
        if not username or not action or action not in CATEGORY_ACTIONS:
            return json_response({"error": "参数格式错误"}, 400)

        admin_config_dict = await get_config()
        if _operator_role(admin_config_dict, username) is None:
            return json_response({"error": "权限不足"}, 401)

        categories = admin_config_dict.setdefault("CustomCategories", [])

        def _match(entry):
            return entry.get("query") == body.get("query") and entry.get("type") == body.get("type")

        match action:
            case "add":
                name = body.get("name")
                type_ = body.get("type")
                query = body.get("query")
                if not name or not type_ or not query:
                    return json_response({"error": "缺少必要参数"}, 400)
                if any(c.get("query") == query and c.get("type") == type_ for c in categories):
                    return json_response({"error": "该分类已存在"}, 400)
                categories.append(
                    {"name": name, "type": type_, "query": query, "from": "custom", "disabled": False}
                )
            case "disable" | "enable":
                if not body.get("query") or not body.get("type"):
                    return json_response({"error": "缺少 query 或 type 参数"}, 400)
                entry = next((c for c in categories if _match(c)), None)
                if not entry:
                    return json_response({"error": "分类不存在"}, 404)
                entry["disabled"] = action == "disable"
            case "delete":
                if not body.get("query") or not body.get("type"):
                    return json_response({"error": "缺少 query 或 type 参数"}, 400)
                index = next((i for i, c in enumerate(categories) if _match(c)), -1)
                if index == -1:
                    return json_response({"error": "分类不存在"}, 404)
                categories.pop(index)
                drop_config_custom_category(admin_config_dict, [(body.get("query"), body.get("type"))])
            case "sort":
                order = body.get("order")
                if not isinstance(order, list):
                    return json_response({"error": "排序列表格式错误"}, 400)
                remaining = {f"{c.get('query')}:{c.get('type')}": c for c in categories}
                new_list = []
                for key in order:
                    item = remaining.pop(key, None)
                    if item is not None:
                        new_list.append(item)
                new_list.extend(
                    item for item in categories if f"{item.get('query')}:{item.get('type')}" in remaining
                )
                admin_config_dict["CustomCategories"] = new_list
            case _:
                return json_response({"error": "未知操作"}, 400)

        await _save_admin_config(admin_config_dict)
        return json_response({"ok": True}, headers={"Cache-Control": "no-store"})
    except Exception as error:  # noqa: BLE001
        print("分类管理操作失败:", error)
        return json_response({"error": "分类管理操作失败", "details": str(error)}, 500)


# ---------------------------------------------------------------------------
# POST /api/admin/site
# ---------------------------------------------------------------------------

SITE_FIELDS = (
    "SiteName",
    "Announcement",
    "SearchDownstreamMaxPage",
    "SiteInterfaceCacheTime",
    "DoubanProxyType",
    "DoubanProxy",
    "DoubanImageProxyType",
    "DoubanImageProxy",
    "DisableYellowFilter",
    "DanmakuApiBaseUrl",
    "TVBoxEnabled",
    "TVBoxPassword",
    "PlaybackSaveInterval",
)
# The first nine are required and typed; the last four are optional but, when
# present, have to be of the right type. Mirrors the route's `typeof` ladder.
SITE_REQUIRED_TYPES = {
    "SiteName": _JS_STRING,
    "Announcement": _JS_STRING,
    "SearchDownstreamMaxPage": "number",
    "SiteInterfaceCacheTime": "number",
    "DoubanProxyType": _JS_STRING,
    "DoubanProxy": _JS_STRING,
    "DoubanImageProxyType": _JS_STRING,
    "DoubanImageProxy": _JS_STRING,
    "DisableYellowFilter": "boolean",
}
SITE_OPTIONAL_TYPES = {
    "DanmakuApiBaseUrl": _JS_STRING,
    "TVBoxEnabled": "boolean",
    "TVBoxPassword": _JS_STRING,
    "PlaybackSaveInterval": "number",
}


def _site_body_ok(body: dict) -> bool:
    for field, kind in SITE_REQUIRED_TYPES.items():
        value = body.get(field)
        if kind is _JS_STRING:
            if not _js_string(value):
                return False
        elif kind == "number":
            if not _js_number_defined(value):
                return False
        elif not _js_boolean(value):
            return False

    for field, kind in SITE_OPTIONAL_TYPES.items():
        if field not in body:
            continue  # `undefined` skips the check; an explicit null does not
        value = body[field]
        if kind is _JS_STRING:
            if not _js_string(value):
                return False
        elif kind == "boolean":
            if not _js_boolean(value):
                return False
        elif not _js_number_defined(value):
            return False

    interval = body.get("PlaybackSaveInterval")
    if "PlaybackSaveInterval" in body and isinstance(interval, (int, float)) and not isinstance(
        interval, bool
    ):
        if not math.isfinite(interval) or interval < 1 or interval > 3600:
            return False
    return True


async def site(request: Request):
    gated = _localstorage_guard()
    if gated:
        return json_response(gated, 400)

    try:
        body = await request.json()

        auth_info = get_auth_info_from_cookie(request)
        if not auth_info or not auth_info.get("username"):
            return json_response({"error": "Unauthorized"}, 401)
        username = auth_info["username"]

        if not _site_body_ok(body):
            return json_response({"error": "参数格式错误"}, 400)

        admin_config_dict = await get_config()
        if username != env_raw(USERNAME):
            user = find_user(admin_config_dict, username)
            if not user or user.get("role") != "admin" or user.get("banned"):
                return json_response({"error": "权限不足"}, 401)

        # 更新缓存中的站点设置
        admin_config_dict["SiteConfig"] = {field: body.get(field) for field in SITE_FIELDS}

        # 同步更新 ConfigFile 中的 cache_time
        try:
            config_file_data = json.loads(admin_config_dict.get("ConfigFile") or "")
            config_file_data["cache_time"] = body.get("SiteInterfaceCacheTime")
            admin_config_dict["ConfigFile"] = json.dumps(
                config_file_data, ensure_ascii=False, separators=(",", ":")
            )
        except Exception as error:  # noqa: BLE001
            print("更新 ConfigFile 中的 cache_time 失败:", error)

        await _save_admin_config(admin_config_dict)
        return json_response({"ok": True}, headers={"Cache-Control": "no-store"})
    except Exception as error:  # noqa: BLE001
        print("更新站点配置失败:", error)
        return json_response({"error": "更新站点配置失败", "details": str(error)}, 500)


# ---------------------------------------------------------------------------
# GET/POST /api/admin/subscription
# ---------------------------------------------------------------------------

BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _decode_base58(text: str) -> str:
    """`decodeBase58` — the simplified variant upstream wrote, leading `1`s and
    all (it does not prepend a zero byte per leading `1`, so neither does this).
    """
    num = 0
    for ch in text:
        index = BASE58_ALPHABET.find(ch)
        if index == -1:
            raise ValueError("Invalid Base58 character")
        num = num * 58 + index
    raw = bytearray()
    while num > 0:
        raw.insert(0, num & 0xFF)
        num >>= 8
    return raw.decode("utf-8")


async def _fetch_subscription_data(url: str):
    """`fetchSubscriptionData` — plain `fetch`, Base58 or JSON."""
    response = await fetch_with_timeout(url)
    if not 200 <= response.status_code < 300:
        raise Exception(f"HTTP {response.status_code}: {response.reason_phrase}")
    text = response.text
    try:
        return json.loads(_decode_base58(text))
    except Exception:  # noqa: BLE001 — the TS catch, which retries as plain JSON
        return json.loads(text)


def _import_sources(admin_config: dict, subscription_data, import_mode: str) -> None:
    """`importSources` — merge `api_site` into `SourceConfig`."""
    api_site = subscription_data.get("api_site") or {} if isinstance(subscription_data, dict) else {}

    if import_mode == "overwrite":
        admin_config["SourceConfig"] = []
        default_config = {"cache_time": 7200, "api_site": {}, "custom_category": []}
        config_file_obj = dict(default_config)
        try:
            raw = admin_config.get("ConfigFile")
            if raw and isinstance(raw, str):
                parsed = json.loads(raw)
                config_file_obj["cache_time"] = parsed.get("cache_time", default_config["cache_time"])
                config_file_obj["custom_category"] = parsed.get(
                    "custom_category", default_config["custom_category"]
                )
        except Exception:  # noqa: BLE001 — parse failure keeps the defaults
            pass
        config_file_obj["api_site"] = {}
        admin_config["ConfigFile"] = json.dumps(
            config_file_obj, ensure_ascii=False, separators=(",", ":")
        )

    sources = admin_config.setdefault("SourceConfig", [])
    existing_keys = {s.get("key") for s in sources}
    for key, item in (api_site or {}).items():
        if key in existing_keys:
            existing = next((s for s in sources if s.get("key") == key), None)
            if existing is not None:
                existing["name"] = item.get("name")
                existing["api"] = item.get("api")
                existing["detail"] = item.get("detail")
                existing["from"] = "config"
        else:
            sources.append(
                {
                    "key": key,
                    "name": item.get("name"),
                    "api": item.get("api"),
                    "detail": item.get("detail"),
                    "from": "config",
                    "disabled": False,
                }
            )


async def subscription_get(request: Request):
    gated = _localstorage_guard()
    if gated:
        return json_response(gated, 400)

    auth_info = get_auth_info_from_cookie(request)
    if not auth_info or not auth_info.get("username"):
        return json_response({"error": "Unauthorized"}, 401)
    username = auth_info["username"]

    try:
        config = await get_config()
        if username != env_raw(USERNAME):
            user_entry = find_user(config, username)
            if not user_entry or user_entry.get("role") != "admin" or user_entry.get("banned"):
                return json_response({"error": "权限不足"}, 401)
        return json_response({"subscriptionConfig": config.get("SubscriptionConfig") or {}})
    except Exception as error:  # noqa: BLE001
        print("获取订阅配置失败:", error)
        return json_response({"error": "获取订阅配置失败", "details": str(error)}, 500)


async def subscription_post(request: Request):
    gated = _localstorage_guard()
    if gated:
        return json_response(gated, 400)

    try:
        body = await request.json()
        action = body.get("action")

        if not action or action not in SUBSCRIPTION_ACTIONS:
            return json_response({"error": "参数格式错误"}, 400)

        # `check` runs without an identity; `update`/`import` need one.
        username = None
        if action in ("update", "import"):
            auth_info = get_auth_info_from_cookie(request)
            if not auth_info or not auth_info.get("username"):
                return json_response({"error": "Unauthorized"}, 401)
            username = auth_info["username"]

        admin_config_dict = await get_config()
        if username and _operator_role(admin_config_dict, username) is None:
            return json_response({"error": "权限不足"}, 401)

        match action:
            case "update":
                subscription_config = admin_config_dict.setdefault("SubscriptionConfig", {})
                if "subscriptionUrl" in body:
                    subscription_config["subscriptionUrl"] = body["subscriptionUrl"]
                if "autoUpdate" in body:
                    subscription_config["autoUpdate"] = bool(body["autoUpdate"])
                if "updateInterval" in body:
                    # `Number(x)`; a garbage value is NaN, which `JSON.stringify`
                    # writes as null and `strip_none` then drops.
                    subscription_config["updateInterval"] = js_number(body["updateInterval"])
                if "importMode" in body:
                    if body["importMode"] not in ("overwrite", "merge"):
                        return json_response({"error": "importMode 必须是 overwrite 或 merge"}, 400)
                    subscription_config["importMode"] = body["importMode"]
                await _save_admin_config(config_self_check(admin_config_dict))
                return json_response({"success": True})

            case "import":
                subscription_config = admin_config_dict.get("SubscriptionConfig") or {}
                url = body.get("subscriptionUrl") or subscription_config.get("subscriptionUrl")
                if not url:
                    return json_response({"error": "订阅地址未提供"}, 400)
                mode = body.get("importMode") or subscription_config.get("importMode") or "merge"
                subscription_data = await _fetch_subscription_data(url)
                _import_sources(admin_config_dict, subscription_data, mode)
                admin_config_dict["SubscriptionConfig"] = admin_config_dict.get("SubscriptionConfig") or {}
                admin_config_dict["SubscriptionConfig"]["lastUpdated"] = math.floor(time.time())
                await _save_admin_config(config_self_check(admin_config_dict))
                return json_response({"success": True, "imported": True})

            case "check":
                sub_config = admin_config_dict.get("SubscriptionConfig") or {}
                auto_update = sub_config.get("autoUpdate") or False
                subscription_url = sub_config.get("subscriptionUrl")
                last_updated = sub_config.get("lastUpdated")
                update_interval = sub_config.get("updateInterval") or 86400
                now = math.floor(time.time())
                should_import = False
                url_to_import = ""
                if auto_update and subscription_url:
                    if not last_updated or (now - last_updated) > update_interval:
                        should_import = True
                        url_to_import = subscription_url
                if not should_import:
                    return json_response(
                        {"success": True, "updated": False, "reason": "未满足自动更新条件"}
                    )
                try:
                    subscription_data = await _fetch_subscription_data(url_to_import)
                    mode = sub_config.get("importMode") or "merge"
                    _import_sources(admin_config_dict, subscription_data, mode)
                    admin_config_dict["SubscriptionConfig"] = admin_config_dict.get(
                        "SubscriptionConfig"
                    ) or {}
                    admin_config_dict["SubscriptionConfig"]["lastUpdated"] = now
                    await _save_admin_config(config_self_check(admin_config_dict))
                    return json_response({"success": True, "updated": True, "imported": True})
                except Exception as error:  # noqa: BLE001
                    print("自动更新导入失败:", error)
                    return json_response(
                        {"success": False, "updated": False, "error": str(error)}, 500
                    )

            case _:
                return json_response({"error": "未知操作"}, 400)
    except Exception as error:  # noqa: BLE001
        print("订阅操作失败:", error)
        return json_response({"error": "订阅操作失败", "details": str(error)}, 500)


# ---------------------------------------------------------------------------
# GET/POST /api/admin/tvbox
# ---------------------------------------------------------------------------


def _tvbox_config_url(request) -> str:
    return f"{get_request_origin(request)}/api/tvbox/config"


def _b64_username(username: str) -> str:
    """`Buffer.from(username,'utf8').toString('base64')`."""
    return base64.b64encode(username.encode("utf-8")).decode("ascii")


def _tvbox_url(base_url: str, username: str) -> str:
    """`${baseUrl}?un=${encodeURIComponent(un)}` — base64 carries `+/=`, which
    the query string has to have escaped or `/api/tvbox/config` would not see
    the same `un` it did."""
    return f"{base_url}?un={encode_uri_component(_b64_username(username))}"


async def tvbox_get(request: Request):
    admin_config_dict = await get_config()

    # localstorage mode: no login required, environment variables only. Not
    # reachable with the ported backends; kept so the shape stays the same.
    if env(STORAGE_TYPE, "sqlite") == "localstorage":
        raw = env_raw(TVBOX_ENABLED)
        return json_response(
            {
                "enabled": True if raw is None or raw.strip() == "" else raw.lower() == "true",
                "password": env_raw(PASSWORD) or "",
                "url": _tvbox_config_url(request),
                "localMode": True,
            }
        )

    auth_info = get_auth_info_from_cookie(request)
    if not auth_info or not auth_info.get("username"):
        return json_response({"error": "Unauthorized"}, 401)

    site_config = admin_config_dict.get("SiteConfig") or {}
    base_url = _tvbox_config_url(request)
    return json_response(
        {
            "enabled": site_config.get("TVBoxEnabled") is True,
            "password": site_config.get("TVBoxPassword") or "",
            "url": _tvbox_url(base_url, auth_info["username"]),
            "localMode": False,
        }
    )


async def tvbox_post(request: Request):
    auth_info = get_auth_info_from_cookie(request)
    if not auth_info or not auth_info.get("username"):
        return json_response({"error": "Unauthorized"}, 401)

    admin_config_dict = await get_config()
    username = auth_info["username"]
    if username != env_raw(USERNAME):
        user = find_user(admin_config_dict, username)
        if not user or user.get("role") != "admin" or user.get("banned"):
            return json_response({"error": "权限不足"}, 403)

    body = await request.json()
    enabled = body.get("enabled")
    password = body.get("password")
    mode = body.get("mode")

    if env(STORAGE_TYPE, "sqlite") == "localstorage":
        return json_response(
            {"error": "本地模式下由环境变量 TVBOX_ENABLED 控制开关，口令=PASSWORD"}, 400
        )

    site_config = admin_config_dict.setdefault("SiteConfig", {})
    if isinstance(enabled, bool):
        site_config["TVBoxEnabled"] = enabled

    final_password = site_config.get("TVBoxPassword") or ""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    if mode == "random":
        # Upstream used `Math.random`; a CSPRNG is strictly better here and the
        # password is only ever read back, never reproduced.
        final_password = "".join(secrets.choice(alphabet) for _ in range(16))
    elif mode == "custom" and isinstance(password, str):
        final_password = password
    site_config["TVBoxPassword"] = final_password

    await _save_admin_config(admin_config_dict)

    base_url = _tvbox_config_url(request)
    return json_response(
        {
            "enabled": site_config.get("TVBoxEnabled") is True,
            "password": site_config.get("TVBoxPassword") or "",
            "url": _tvbox_url(base_url, username),
        }
    )


ROUTES = [
    ("/api/admin/config", admin_config, ["GET"]),
    ("/api/admin/reset", admin_reset, ["GET"]),
    ("/api/admin/config_file", config_file, ["POST"]),
    ("/api/admin/user", user, ["POST"]),
    ("/api/admin/group", group, ["POST"]),
    ("/api/admin/source", source, ["POST"]),
    ("/api/admin/category", category, ["POST"]),
    ("/api/admin/site", site, ["POST"]),
    ("/api/admin/subscription", subscription_get, ["GET"]),
    ("/api/admin/subscription", subscription_post, ["POST"]),
    ("/api/admin/tvbox", tvbox_get, ["GET"]),
    ("/api/admin/tvbox", tvbox_post, ["POST"]),
]
