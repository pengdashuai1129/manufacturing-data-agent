import asyncio

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.ranking import rrf_order
from app.agent.state import DataAgentState
from app.core.logging import logger
from app.prompt.prompt_loader import load_prompt


async def recall_metric(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer("召回指标信息")

    query = state["query"]
    keywords = state["keywords"]

    embedding_client = runtime.context["embedding_client"]
    metric_qdrant_repository = runtime.context["metric_qdrant_repository"]

    prompt = PromptTemplate(
        template=load_prompt("extend_keywords_for_metric_recall"),
        input_variables=["query"],
    )
    chain = prompt | llm | JsonOutputParser()
    expanded = await chain.ainvoke({"query": query})

    keywords = sorted(set(keywords + expanded))

    async def _search(keyword: str):
        embedding = await embedding_client.aembed_query(keyword)
        return await metric_qdrant_repository.search(embedding)

    hits_per_keyword = await asyncio.gather(*(_search(keyword) for keyword in keywords))

    metric_info_map = {}
    for hits in hits_per_keyword:
        for metric_info in hits:
            metric_info_map.setdefault(metric_info.id, metric_info)

    order = rrf_order([[info.id for info in hits] for hits in hits_per_keyword])

    retrieved_metric_infos = [
        metric_info_map[metric_id] for metric_id in order if metric_id in metric_info_map
    ]
    logger.info("检索到指标：{}", order)
    return {"retrieved_metric_infos": retrieved_metric_infos}
