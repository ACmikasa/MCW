"""Authentication, user administration, and role administration endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from services.auth_service import AuthError, AuthService

SESSION_COOKIE = "eeg_session"


def build_auth_router(auth: AuthService) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    def current_user(request: Request) -> dict:
        user = auth.user_from_token(request.cookies.get(SESSION_COOKIE))
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录")
        return user

    def require(permission: str):
        def dependency(user: dict = Depends(current_user)) -> dict:
            if permission not in user["permissions"]:
                raise HTTPException(status_code=403, detail="没有执行此操作的权限")
            return user
        return dependency

    async def json_body(request: Request) -> dict:
        try:
            value = await request.json()
            return value if isinstance(value, dict) else {}
        except Exception as error:
            raise HTTPException(status_code=400, detail="请求内容必须是 JSON") from error

    def translate(action):
        try:
            return action()
        except AuthError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.post("/login")
    async def login(request: Request, response: Response):
        body = await json_body(request)
        result = auth.authenticate(str(body.get("username", "")), str(body.get("password", "")))
        if result is None:
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        token, user = result
        response.set_cookie(
            SESSION_COOKIE, token, max_age=auth.session_hours * 3600,
            httponly=True, samesite="strict", secure=request.url.scheme == "https", path="/",
        )
        return {"user": user}

    @router.post("/logout")
    async def logout(request: Request, response: Response):
        auth.logout(request.cookies.get(SESSION_COOKIE))
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"success": True}

    @router.get("/me")
    async def me(user: dict = Depends(current_user)):
        return user

    @router.put("/me/password")
    async def change_password(request: Request, response: Response, user: dict = Depends(current_user)):
        body = await json_body(request)
        if not auth.verify_user_password(user["id"], str(body.get("current_password", ""))):
            raise HTTPException(status_code=400, detail="当前密码错误")
        translate(lambda: auth.set_password(user["id"], str(body.get("new_password", ""))))
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"success": True, "message": "密码已修改，请重新登录"}

    @router.get("/users")
    async def users(_: dict = Depends(require("users:manage"))):
        return {"items": auth.list_users()}

    @router.post("/users", status_code=201)
    async def create_user(request: Request, _: dict = Depends(require("users:manage"))):
        body = await json_body(request)
        try:
            role_id = int(body.get("role_id", 0))
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=400, detail="请选择有效角色") from error
        return translate(lambda: auth.create_user(
            str(body.get("username", "")), str(body.get("display_name", "")),
            str(body.get("password", "")), role_id, bool(body.get("is_active", True)),
        ))

    @router.put("/users/{user_id}")
    async def update_user(user_id: int, request: Request, actor: dict = Depends(require("users:manage"))):
        body = await json_body(request)
        active = bool(body.get("is_active", True))
        try:
            role_id = int(body.get("role_id", 0))
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=400, detail="请选择有效角色") from error
        if user_id == actor["id"] and not active:
            raise HTTPException(status_code=400, detail="不能停用当前登录用户")
        return translate(lambda: auth.update_user(
            user_id, str(body.get("display_name", "")), role_id, active,
        ))

    @router.put("/users/{user_id}/password")
    async def reset_password(user_id: int, request: Request, _: dict = Depends(require("users:manage"))):
        body = await json_body(request)
        translate(lambda: auth.set_password(user_id, str(body.get("password", ""))))
        return {"success": True}

    @router.delete("/users/{user_id}")
    async def delete_user(user_id: int, actor: dict = Depends(require("users:manage"))):
        translate(lambda: auth.delete_user(user_id, actor["id"]))
        return {"success": True}

    @router.get("/roles")
    async def roles(_: dict = Depends(current_user)):
        return {"items": auth.list_roles()}

    @router.get("/permissions")
    async def permissions(_: dict = Depends(require("roles:manage"))):
        return {"items": auth.list_permissions()}

    @router.post("/roles", status_code=201)
    async def create_role(request: Request, _: dict = Depends(require("roles:manage"))):
        body = await json_body(request)
        return translate(lambda: auth.create_role(
            str(body.get("name", "")), str(body.get("description", "")), list(body.get("permissions", [])),
        ))

    @router.put("/roles/{role_id}")
    async def update_role(role_id: int, request: Request, _: dict = Depends(require("roles:manage"))):
        body = await json_body(request)
        return translate(lambda: auth.update_role(
            role_id, str(body.get("description", "")), list(body.get("permissions", [])),
        ))

    @router.delete("/roles/{role_id}")
    async def delete_role(role_id: int, _: dict = Depends(require("roles:manage"))):
        translate(lambda: auth.delete_role(role_id))
        return {"success": True}

    router.current_user = current_user  # type: ignore[attr-defined]
    router.require = require  # type: ignore[attr-defined]
    return router
