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


async def recall_value(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer("召回字段取值")

    query = state["query"]
    keywords = state["keywords"]

    value_es_repository = runtime.context["value_es_repository"]

    prompt = PromptTemplate(
        template=load_prompt("extend_keywords_for_value_recall"),
        input_variables=["query"],
    )
    chain = prompt | llm | JsonOutputParser()
    expanded = await chain.ainvoke({"query": query})

    keywords = sorted(set(keywords + expanded))

    hits_per_keyword = await asyncio.gather(
        *(value_es_repository.search(keyword) for keyword in keywords)
    )

    value_info_map = {}
    for hits in hits_per_keyword:
        for value_info in hits:
            value_info_map.setdefault(value_info.id, value_info)

    order = rrf_order([[info.id for info in hits] for hits in hits_per_keyword])

    retrieved_value_infos = [
        value_info_map[value_id] for value_id in order if value_id in value_info_map
    ]
    logger.info("检索到字段取值：{}", order)
    return {"retrieved_value_infos": retrieved_value_infos}
