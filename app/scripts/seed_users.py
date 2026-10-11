
import argparse
import asyncio
import sys
from datetime import datetime

from sqlalchemy import text

DEFAULT_SUPERVISOR = "E001"

DEFAULT_PASSWORD = "Demo@2026"


async def _load_salesmen(dw_session) -> list[tuple[str, str]]:

    result = await dw_session.execute(
        text(
            "SELECT employee_id, employee_name FROM dim_employee "
            "WHERE department = '业务部' AND employee_role = '业务员' "
            "ORDER BY employee_id"
        )
    )
    return [(row[0], row[1]) for row in result.fetchall()]


async def _seed(password: str, supervisor_id: str):
    from app.clients.mysql_client import app_mysql_client_manager, dw_mysql_client_manager
    from app.entities.user import (
        ROLE_ADMIN,
        ROLE_BOSS,
        ROLE_SALES_MANAGER,
        ROLE_SALESMAN,
        User,
    )
    from app.repositories.mysql.app.app_mysql_repository import AppMySQLRepository
    from app.services.auth_service import hash_password

    app_mysql_client_manager.init()
    dw_mysql_client_manager.init()

    created: list[tuple[str, str, str]] = []
    skipped: list[str] = []

    try:
        async with (
            app_mysql_client_manager.session_factory() as app_session,
            dw_mysql_client_manager.session_factory() as dw_session,
        ):
            repo = AppMySQLRepository(app_session)

            plan: list[User] = [
                User(
                    user_id="admin",
                    username="admin",
                    password_hash="",
                    display_name="系统管理员",
                    role=ROLE_ADMIN,
                    employee_id=None,
                    status="active",
                ),
                User(
                    user_id="boss",
                    username="boss",
                    password_hash="",
                    display_name="总经理",
                    role=ROLE_BOSS,
                    employee_id=None,
                    status="active",
                ),
            ]

            salesmen = await _load_salesmen(dw_session)
            if not salesmen:
                print("✗ dw.dim_employee 里没读到业务部的人，先确认数仓已入库")
                return 1
            if supervisor_id not in [emp_id for emp_id, _ in salesmen]:
                print(f"✗ --supervisor 指定的 {supervisor_id} 不在业务部名单里：{salesmen}")
                return 1

            for employee_id, employee_name in salesmen:
                is_supervisor = employee_id == supervisor_id
                plan.append(
                    User(
                        user_id=employee_id,
                        username=employee_id,
                        password_hash="",
                        display_name=employee_name,
                        role=ROLE_SALES_MANAGER if is_supervisor else ROLE_SALESMAN,
                        employee_id=employee_id,
                        status="active",
                    )
                )

            password_hash = hash_password(password)
            now = datetime.now()

            for user in plan:
                if await repo.get_user_by_username(user.username) is not None:
                    skipped.append(user.username)
                    continue
                user.password_hash = password_hash
                repo.add_user(user, created_at=now)
                created.append((user.username, user.role, user.display_name or ""))

            await app_session.commit()

    finally:
        await app_mysql_client_manager.close()
        await dw_mysql_client_manager.close()

    from app.entities.user import role_label

    print("=" * 60)
    if created:
        print(f"新建 {len(created)} 个账号（初始口令：{password}）：")
        for username, role, display_name in created:
            print(f"  {username:<8} {role_label(role):<8} {display_name}")
    if skipped:
        print(f"\n跳过 {len(skipped)} 个已存在的账号：{'、'.join(skipped)}")
    print("=" * 60)
    print("⚠️ 这是内部工具，请第一时间改掉初始口令。")
    return 0


def main():
    parser = argparse.ArgumentParser(description="初始化登录账号")
    parser.add_argument(
        "--password",
        default=DEFAULT_PASSWORD,
        help=f"初始口令（默认 {DEFAULT_PASSWORD}）",
    )
    parser.add_argument(
        "--supervisor",
        default=DEFAULT_SUPERVISOR,
        help=f"由哪位业务员兼任业务主管（默认 {DEFAULT_SUPERVISOR}）",
    )
    args = parser.parse_args()

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    raise SystemExit(asyncio.run(_seed(args.password, args.supervisor)))


if __name__ == "__main__":
    main()
