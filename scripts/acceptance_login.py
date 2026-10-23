"""Interactive account login for local/remote acceptance scripts."""

import getpass
import os


async def acceptance_login(client, base):
    info = await client.get("/api/v1/auth/me")
    info.raise_for_status()
    if not info.json()["initialized"]:
        raise SystemExit("请先停服执行 deploy.sh init-admin 创建管理员")
    username = os.getenv("OFFICE_SMOKE_USERNAME") or input("验收账号：").strip()
    password = os.getenv("OFFICE_SMOKE_PASSWORD") or getpass.getpass("账号密码：")
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"Origin": base, "X-CSRF-Token": info.json()["csrf_token"]},
    )
    response.raise_for_status()
    return response.json()["csrf_token"]
