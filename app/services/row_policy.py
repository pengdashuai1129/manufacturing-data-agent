
import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from app.entities.user import ROLE_SALESMAN, User


PROTECTED_TABLES: frozenset[str] = frozenset(
    {
        "fact_order",
        "fact_quotation",
        "fact_sample",
        "fact_order_item",
        "fact_production",
        "fact_production_process",
    }
)

EMPLOYEE_COLUMNS: frozenset[str] = frozenset({"employee_id", "employee_name"})

OWNER_COLUMN = "employee_id"

_SYNTHETIC_PREFIX = "__sec_"

_DEPT_SCOPE_ALIAS = "__sec_dept"

DEPARTMENT_COLUMN = "department"

_TABLE_TOKEN_RE = re.compile(r"\b((?:fact|dim)_[a-z_]+)\b", re.IGNORECASE)

_EMPLOYEE_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,20}$")


@dataclass(frozen=True)
class Hop:

    local_column: str
    target_table: str
    target_column: str


OWNER_ROUTE: dict[str, tuple[Hop, ...]] = {
    "fact_order": (),
    "fact_quotation": (),
    "fact_sample": (),
    "fact_order_item": (Hop("order_id", "fact_order", "order_id"),),
    "fact_production": (Hop("order_id", "fact_order", "order_id"),),
    "fact_production_process": (
        Hop("production_id", "fact_production", "production_id"),
        Hop("order_id", "fact_order", "order_id"),
    ),
}


@dataclass(frozen=True)
class PolicyResult:

    sql: str | None
    reason: str | None = None
    applied: bool = False
    note: str | None = None

    @property
    def ok(self) -> bool:
        return self.reason is None


class RowPolicyDenied(Exception):
    pass


class _RewriteError(Exception):
    pass


def _reject(reason: str) -> PolicyResult:
    return PolicyResult(sql=None, reason=reason)




def _parse_single(sql: str):

    try:
        statements = sqlglot.parse(sql, read="mysql")
    except Exception as e:
        return None, f"SQL 无法解析，已拒绝执行（{type(e).__name__}）：{e}"

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        return None, f"只允许执行单条 SQL，实际解析出 {len(statements)} 条"

    return statements[0], None


def _owning_scope(node: exp.Expression):

    parent = node.parent
    while parent is not None and not isinstance(parent, (exp.Select, exp.Union)):
        parent = parent.parent
    return parent


def _raw_table_tokens(sql: str) -> set[str]:

    return {m.group(1).lower() for m in _TABLE_TOKEN_RE.finditer(sql)}


def _cte_names(root: exp.Expression) -> set[str]:

    return {c.alias.lower() for c in root.find_all(exp.CTE) if c.alias}




def _references_identity(column: exp.Column, identity_aliases: set[str]) -> bool:

    if column.name.lower() not in EMPLOYEE_COLUMNS:
        return False

    qualifier = (column.table or "").lower()
    if qualifier:
        return qualifier in identity_aliases

    return True


def is_cross_person_query(sql: str, identity_aliases: set[str] | None = None) -> bool:

    root, _ = _parse_single(sql)
    if root is None:
        return True

    selects = [root] if isinstance(root, exp.Select) else list(root.find_all(exp.Select))
    if not selects:
        return True

    aliases = identity_aliases or set()

    for select in selects:
        for projection in select.expressions:
            for column in projection.find_all(exp.Column):
                if _references_identity(column, aliases):
                    return True

        group = select.args.get("group")
        if group is not None:
            for column in group.find_all(exp.Column):
                if _references_identity(column, aliases):
                    return True

    return False




def inject_row_policy(sql: str, user: User) -> PolicyResult:

    if not user.is_restricted:
        return PolicyResult(sql=sql, applied=False)

    if not user.employee_id:
        return _reject("当前账号没有绑定员工编号，无法确定数据范围，请联系管理员")

    if not _EMPLOYEE_ID_RE.match(user.employee_id):
        return _reject("当前账号绑定的员工编号格式异常，已拒绝执行")

    owner_value = user.employee_id

    root, parse_error = _parse_single(sql)
    if root is None:
        return _reject(parse_error)

    tables = list(root.find_all(exp.Table))

    parsed_names = {t.name.lower() for t in tables}
    missed = (_raw_table_tokens(sql) & PROTECTED_TABLES) - parsed_names
    if missed:
        return _reject(
            "SQL 解析结果与原始语句不一致（未正确识别："
            + "、".join(sorted(missed))
            + "），出于安全考虑拒绝执行"
        )

    collision = _cte_names(root) & PROTECTED_TABLES
    if collision:
        return _reject("CTE 名称与受控表重名，拒绝执行：" + "、".join(sorted(collision)))

    root_scope = root if isinstance(root, exp.Select) else None

    top_tables: dict[str, exp.Table] = {}
    all_top_aliases: set[str] = set()
    employee_table_aliases: set[str] = set()

    for table in tables:
        if root_scope is None or _owning_scope(table) is not root_scope:
            if table.name.lower() in PROTECTED_TABLES:
                return _reject(
                    f"该问题涉及子查询或 UNION 中的受控表 {table.name}，"
                    "当前角色下无法安全限定数据范围，请换一种问法"
                    "（例如只查单张表，或只做简单的多表关联）"
                )
            continue

        table_name = table.name.lower()
        alias = table.alias_or_name.lower()
        if alias in all_top_aliases:
            return _reject(f"SQL 中别名 {alias} 指向多张表，无法确定数据范围，已拒绝执行")
        all_top_aliases.add(alias)

        if table_name == "dim_employee":
            employee_table_aliases.add(alias)

        if table_name in PROTECTED_TABLES:
            top_tables[alias] = table

    if not top_tables:
        return PolicyResult(sql=sql, applied=False)

    if user.role == ROLE_SALESMAN:
        identity_aliases = employee_table_aliases | set(top_tables)
        if is_cross_person_query(sql, identity_aliases):
            return _reject(
                "该问题涉及其他业务员的数据，当前角色只能查看自己名下的单据。"
                "可以改成问自己的情况，比如「我今年的成交额是多少」"
            )

    try:
        rewritten = _rewrite_scope(root_scope, top_tables, user)
    except _RewriteError as e:
        return _reject(str(e))
    except Exception as e:
        return _reject(f"改写 SQL 时发生意外错误，已拒绝执行（{type(e).__name__}）：{e}")

    try:
        text = rewritten.sql(dialect="mysql")
    except Exception as e:
        return _reject(f"改写后的 SQL 无法生成，已拒绝执行（{type(e).__name__}）：{e}")

    if owner_value not in text:
        return _reject("改写后的 SQL 未包含数据范围条件，已拒绝执行")

    if user.scope_by_department:
        note = "已按你的数据权限改写：只能看到本部门（与你在同一个部门）的业务记录"
    else:
        note = f"已按你的数据权限改写：只能看到员工编号为 {owner_value} 的记录"

    return PolicyResult(sql=text, applied=True, note=note)




def _weak_aliases(select: exp.Select) -> set[str]:

    weak: set[str] = set()

    left_side: list[str] = []
    from_clause = select.args.get("from_")
    if from_clause is not None and isinstance(from_clause.this, exp.Table):
        left_side.append(from_clause.this.alias_or_name.lower())

    for join in select.args.get("joins") or []:
        if not isinstance(join.this, exp.Table):
            continue
        right = [join.this.alias_or_name.lower()]
        side = (join.side or "").upper()

        if side == "LEFT":
            weak.update(right)
        elif side == "RIGHT":
            weak.update(left_side)
        elif side == "FULL":
            weak.update(right)
            weak.update(left_side)

        left_side.extend(right)

    return weak


def _department_scope_predicate(alias: str, employee_id: str) -> exp.Expression:

    my_department = exp.Subquery(
        this=exp.select(exp.column(DEPARTMENT_COLUMN))
        .from_(exp.Table(this=exp.to_identifier("dim_employee")))
        .where(
            exp.EQ(
                this=exp.column(OWNER_COLUMN),
                expression=exp.Literal.string(employee_id),
            )
        )
    )

    scope_table = exp.Table(
        this=exp.to_identifier("dim_employee"),
        alias=exp.TableAlias(this=exp.to_identifier(_DEPT_SCOPE_ALIAS)),
    )

    return exp.Exists(
        this=exp.select(exp.Literal.number(1))
        .from_(scope_table)
        .where(
            exp.EQ(
                this=exp.column(OWNER_COLUMN, table=_DEPT_SCOPE_ALIAS),
                expression=exp.column(OWNER_COLUMN, table=alias),
            ),
            exp.EQ(
                this=exp.column(DEPARTMENT_COLUMN, table=_DEPT_SCOPE_ALIAS),
                expression=my_department,
            ),
        )
    )


def _rewrite_scope(
    select: exp.Select,
    top_tables: dict[str, exp.Table],
    user: User,
) -> exp.Select:

    rewritten = select.copy()

    copied_top: dict[str, exp.Table] = {}
    for alias in top_tables:
        for table in rewritten.find_all(exp.Table):
            if _owning_scope(table) is rewritten and table.alias_or_name.lower() == alias:
                copied_top[alias] = table
                break
        else:
            raise _RewriteError(f"改写后定位不到表 {alias}，已拒绝执行")

    weak = _weak_aliases(rewritten)

    for alias, table in copied_top.items():
        if alias in weak and OWNER_ROUTE[table.name.lower()]:
            raise _RewriteError(
                f"{table.name} 出现在外连接（LEFT/RIGHT JOIN）的被关联侧，"
                "而它需要通过其它表才能确定归属——在不丢行前提下属主过滤做不到。"
                "请改成用 INNER JOIN，或改成只查主表的问法"
            )

    taken_aliases = {t.alias_or_name.lower() for t in rewritten.find_all(exp.Table)}

    predicates: dict[str, exp.Expression] = {}
    synthesized: dict[tuple[str, str], str] = {}
    joined: set[str] = set()

    def owner_predicate(alias: str) -> exp.Expression:

        if alias not in predicates:
            if user.scope_by_department:
                predicates[alias] = _department_scope_predicate(alias, user.employee_id)
            else:
                predicates[alias] = exp.EQ(
                    this=exp.column(OWNER_COLUMN, table=alias),
                    expression=exp.Literal.string(user.employee_id),
                )
        return predicates[alias]

    def resolve_target(source_alias: str, table_name: str) -> str:

        key = (source_alias, table_name)
        if key in synthesized:
            return synthesized[key]

        for alias, table in copied_top.items():
            if table.name.lower() == table_name and alias not in weak:
                return alias

        synthetic = _SYNTHETIC_PREFIX + table_name
        suffix = 1
        while synthetic.lower() in taken_aliases:
            suffix += 1
            synthetic = f"{_SYNTHETIC_PREFIX}{table_name}_{suffix}"
        taken_aliases.add(synthetic.lower())
        synthesized[key] = synthetic
        return synthetic

    for alias, table in copied_top.items():
        route = OWNER_ROUTE[table.name.lower()]

        if not route:
            owner_predicate(alias)
            continue

        current_alias = alias
        for hop in route:
            target_alias = resolve_target(current_alias, hop.target_table)

            if target_alias not in copied_top and target_alias not in joined:
                rewritten.append(
                    "joins",
                    exp.Join(
                        this=exp.Table(
                            this=exp.to_identifier(hop.target_table),
                            alias=exp.TableAlias(this=exp.to_identifier(target_alias)),
                        ),
                        on=exp.EQ(
                            this=exp.column(hop.local_column, table=current_alias),
                            expression=exp.column(hop.target_column, table=target_alias),
                        ),
                    ),
                )
                joined.add(target_alias)

            current_alias = target_alias

        owner_predicate(current_alias)

    where_predicates: list[exp.Expression] = []

    for alias, predicate in predicates.items():
        table = copied_top.get(alias)

        if alias in weak and table is not None:
            join = table.parent
            while join is not None and not isinstance(join, exp.Join):
                join = join.parent
            if join is None:
                raise _RewriteError(f"无法定位 {alias} 所属的 JOIN，已拒绝执行")
            existing_on = join.args.get("on")
            join.set(
                "on",
                exp.And(this=existing_on, expression=predicate)
                if existing_on is not None
                else predicate,
            )
        else:
            where_predicates.append(predicate)

    for predicate in where_predicates:
        rewritten.where(predicate, copy=False)

    return rewritten
