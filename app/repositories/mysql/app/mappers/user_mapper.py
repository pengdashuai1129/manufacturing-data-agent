
from datetime import datetime

from app.entities.user import User
from app.models.sys_user import SysUserMySQL


class UserMapper:

    @staticmethod
    def to_entity(sys_user_mysql: SysUserMySQL) -> User:
        return User(
            user_id=sys_user_mysql.user_id,
            username=sys_user_mysql.username,
            password_hash=sys_user_mysql.password_hash,
            display_name=sys_user_mysql.display_name,
            role=sys_user_mysql.role,
            employee_id=sys_user_mysql.employee_id,
            status=sys_user_mysql.status,
        )

    @staticmethod
    def to_model(user: User, created_at: datetime) -> SysUserMySQL:
        return SysUserMySQL(
            user_id=user.user_id,
            username=user.username,
            password_hash=user.password_hash,
            display_name=user.display_name,
            role=user.role,
            employee_id=user.employee_id,
            status=user.status,
            created_at=created_at,
        )
