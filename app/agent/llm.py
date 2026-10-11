
from langchain_deepseek import ChatDeepSeek

from app.conf.app_config import app_config

llm = ChatDeepSeek(
    model=app_config.llm.model_name,
    api_key=app_config.llm.api_key,
    base_url=app_config.llm.base_url,
    temperature=0,
)
