
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.base import Base


class DataChangeLogMySQL(Base):

    __tablename__ = "data_change_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="自增编号")
    table_name: Mapped[str] = mapped_column(String(64), comment="被改的表")
    pk_value: Mapped[str] = mapped_column(String(64), comment="被改行的主键值")
    action: Mapped[str] = mapped_column(String(16), comment="insert/update/delete")
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, comment="改前整行快照")
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, comment="改后整行快照")
    user_id: Mapped[str | None] = mapped_column(String(32), comment="操作人账号")
    created_at: Mapped[datetime] = mapped_column(DateTime, comment="操作时间")
