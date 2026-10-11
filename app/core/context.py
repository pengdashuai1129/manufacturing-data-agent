import uuid
import contextvars

request_id_ctx_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id",
    default="-",
)


def generate_request_id() -> str:
    return uuid.uuid4().hex[:8]
