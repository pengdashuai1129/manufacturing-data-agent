import sys
from pathlib import Path

from loguru import logger

from app.conf.app_config import app_config
from app.core.context import request_id_ctx_var


LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<magenta>request_id - {extra[request_id]}</magenta> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)


def _inject_request_id(record):
    request_id = request_id_ctx_var.get()
    record["extra"]["request_id"] = request_id


logger.remove()

logger = logger.patch(_inject_request_id)


console_cfg = app_config.logging.console
if console_cfg.enable:
    logger.add(
        sink=sys.stdout,
        level=console_cfg.level,
        format=LOG_FORMAT,
    )


file_cfg = app_config.logging.file
if file_cfg.enable:
    log_dir = Path(file_cfg.path)
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.add(
        sink=log_dir / "app.log",
        level=file_cfg.level,
        format=LOG_FORMAT.replace("<green>", "").replace("</green>", "")
                          .replace("<magenta>", "").replace("</magenta>", "")
                          .replace("<cyan>", "").replace("</cyan>", "")
                          .replace("<level>", "").replace("</level>", ""),
        rotation=file_cfg.rotation,
        retention=file_cfg.retention,
        encoding="utf-8",
    )

logger.info("日志系统初始化完成")


if __name__ == "__main__":
    from app.core.context import generate_request_id

    request_id_ctx_var.set(generate_request_id())
    logger.info("请求A: 这是一条 INFO 测试")

    request_id_ctx_var.set(generate_request_id())
    logger.info("请求B: 这是一条 INFO 测试")

    logger.warning("请求B: WARNING 测试")
    logger.error("请求B: ERROR 测试")
