import asyncio

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.ranking import rrf_fuse
from app.agent.llm import llm
from app.agent.state import DataAgentState
from app.core.logging import logger
from app.prompt.prompt_loader import load_prompt


async def recall_column(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer("召回字段信息")

    query = state["query"]
    keywords = state["keywords"]

    embedding_client = runtime.context["embedding_client"]
    column_qdrant_repository = runtime.context["column_qdrant_repository"]

    prompt = PromptTemplate(
        template=load_prompt("extend_keywords_for_column_recall"),
        input_variables=["query"],
    )
    chain = prompt | llm | JsonOutputParser()
    expanded = await chain.ainvoke({"query": query})

    # 排序后再检索：set 的迭代顺序受 PYTHONHASHSEED 影响，换个进程就变，
    # 会让同一句问题的召回结果不可复现。排序换来确定性，也顺带让日志可读。
    keywords = sorted(set(keywords + expanded))

    async def _search(keyword: str):
        embedding = await embedding_client.aembed_query(keyword)
        return await column_qdrant_repository.search(embedding)

    hits_per_keyword = await asyncio.gather(*(_search(keyword) for keyword in keywords))

    column_info_map = {}
    for hits in hits_per_keyword:
        for column_info in hits:
            column_info_map.setdefault(column_info.id, column_info)

    # 每一路的返回本身是按相似度降序的，直接用名次做 RRF 融合，
    # 而不是「哪个关键词先被遍历到谁就排前面」
    fused = rrf_fuse([[info.id for info in hits] for hits in hits_per_keyword])

    retrieved_column_infos = [
        column_info_map[hit.id] for hit in fused if hit.id in column_info_map
    ]
    logger.info("检索到字段：{}", [hit.id for hit in fused])
    return {
        "retrieved_column_infos": retrieved_column_infos,
        "recall_scores": {hit.id: hit.score for hit in fused},
    }
