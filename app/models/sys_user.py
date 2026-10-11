
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SysUserMySQL(Base):

    __tablename__ = "sys_user"

    user_id: Mapped[str] = mapped_column(String(32), primary_key=True, comment="账号编号")
    username: Mapped[str] = mapped_column(String(64), comment="登录名")
    password_hash: Mapped[str] = mapped_column(String(255), comment="口令散列")
    display_name: Mapped[str | None] = mapped_column(String(64), comment="显示名称")
    role: Mapped[str] = mapped_column(String(32), comment="角色")
    employee_id: Mapped[str | None] = mapped_column(
        String(20), comment="绑定的员工编号，对应 dw.dim_employee.employee_id"
    )
    status: Mapped[str] = mapped_column(String(16), comment="状态 active/disabled")
    created_at: Mapped[datetime] = mapped_column(DateTime, comment="创建时间")
