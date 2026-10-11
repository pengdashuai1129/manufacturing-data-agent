
from dataclasses import dataclass

ROLE_ADMIN = "admin"
ROLE_BOSS = "boss"
ROLE_SALES_MANAGER = "sales_manager"
ROLE_SALESMAN = "salesman"

ROLE_LABELS = {
    ROLE_ADMIN: "管理员",
    ROLE_BOSS: "老板",
    ROLE_SALES_MANAGER: "业务主管",
    ROLE_SALESMAN: "业务员",
}

ROLES_CAN_WRITE = {ROLE_ADMIN}

ROLES_RESTRICTED = {ROLE_SALES_MANAGER, ROLE_SALESMAN}

ROLES_BY_DEPARTMENT = {ROLE_SALES_MANAGER}


def role_label(role: str) -> str:

    return ROLE_LABELS.get(role, role)


@dataclass
class User:

    user_id: str
    username: str
    password_hash: str
    display_name: str | None
    role: str
    employee_id: str | None
    status: str

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def can_write(self) -> bool:
        return self.role in ROLES_CAN_WRITE

    @property
    def is_restricted(self) -> bool:
        return self.role in ROLES_RESTRICTED

    @property
    def scope_by_department(self) -> bool:
        return self.role in ROLES_BY_DEPARTMENT
