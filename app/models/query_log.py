
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class QueryLogMySQL(Base):

    __tablename__ = "query_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="自增编号")
    user_id: Mapped[str | None] = mapped_column(String(32), comment="提问的账号")
    question: Mapped[str | None] = mapped_column(Text, comment="用户的原始问题")
    final_sql: Mapped[str | None] = mapped_column(Text, comment="最终执行的 SQL")
    status: Mapped[str | None] = mapped_column(String(16), comment="ok/rejected/error")
    row_count: Mapped[int | None] = mapped_column(Integer, comment="返回行数")
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, comment="耗时(毫秒)")
    request_id: Mapped[str | None] = mapped_column(String(32), comment="请求编号")
    created_at: Mapped[datetime] = mapped_column(DateTime, comment="提问时间")
