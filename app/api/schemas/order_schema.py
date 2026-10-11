
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class OrderUpsertSchema(BaseModel):

    model_config = ConfigDict(extra="allow")

    order_id: str | None = None

    employee_id: str = ""
    customer_id: str = ""
    order_date: str | None = None
    ship_date: str | None = None
    actual_ship_date: str | None = None
    currency: str = "USD"
    order_status: str | None = None
    delay_reason: str | None = None

    items: list[dict[str, Any]] = Field(default_factory=list)

    production: dict[str, Any] | None = None


class OrderImportSchema(BaseModel):

    model_config = ConfigDict(extra="allow")

    dry_run: bool = False
