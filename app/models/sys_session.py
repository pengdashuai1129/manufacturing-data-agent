
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SysSessionMySQL(Base):

    __tablename__ = "sys_session"

    token: Mapped[str] = mapped_column(String(64), primary_key=True, comment="会话令牌")
    user_id: Mapped[str] = mapped_column(String(32), comment="账号编号")
    created_at: Mapped[datetime] = mapped_column(DateTime, comment="登录时间")
    expires_at: Mapped[datetime] = mapped_column(DateTime, comment="过期时间")
