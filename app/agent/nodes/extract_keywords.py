from langgraph.runtime import Runtime
import jieba.analyse
from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.logging import logger

ALLOW_POS = ("n", "nr", "ns", "nt", "nz", "v", "vn", "a", "an", "eng", "i", "l")

async def extract_keywords(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer("抽取关键词")

    query = state["query"]

    keywords = jieba.analyse.extract_tags(query, allowPOS=ALLOW_POS)

    keywords = list(set(keywords + [query]))

    logger.info("抽取关键词完成：{}", keywords)

    return {"keywords": keywords}


