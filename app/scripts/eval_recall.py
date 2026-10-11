
"""召回排序的离线评测：RRF 融合到底有没有让「该召回的字段」排得更靠前。

**为什么要有这个脚本**：改排序逻辑最容易自欺欺人 —— 跑一遍看日志「好像挺合理」
就收工。这个脚本把「排得好不好」变成一个数：一组人工标注了答案字段的业务问题，
每题看答案字段排在融合结果第几位，算 MRR 与 Top-K 命中率。

**为什么能离线跑**：Qdrant / MySQL / ES / Docker 都不用起。86 个字段的定义本来
就在 ``conf/meta_config.yaml`` 里，用 Ollama 的 bge-m3 现算向量、在进程内做一次
暴力余弦排序，得到的就是 Qdrant 那一句 ``search()`` 会返回的东西。规模只有 86 条，
暴力算是微秒级，检索质量与线上一致。

**这个脚本没有测到什么**（别把它当成整条链路的验收）：

- 只测**排序**，不测「取值的 ES 召回」与「指标召回」，那两路要靠 ES / Qdrant 里
  的数据，离线拿不到
- 只测**词面重排**这条兜底路径。cross-encoder 要 TEI 容器，本脚本不依赖它
- 不测 SQL 生成质量 —— 召回排序好不代表模型一定写对 SQL

用法::

    uv run python -m app.scripts.eval_recall            # 跑全部
    uv run python -m app.scripts.eval_recall --rebuild  # 忽略向量缓存重算
"""

import argparse
import asyncio
import io
import json
import math
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import jieba.analyse
from omegaconf import OmegaConf

from app.agent.ranking import lexical_score, rrf_fuse, rrf_order


def rrf_fuse_scores(ranked_per_keyword) -> dict[str, float]:
    return {hit.id: hit.score for hit in rrf_fuse(ranked_per_keyword)}

META_CONFIG = Path(__file__).parents[2] / "conf" / "meta_config.yaml"
CACHE_FILE = Path(__file__).parents[2] / ".eval_recall_cache.json"

OLLAMA_URL = "http://localhost:11434/api/embed"
EMBED_MODEL = "bge-m3"

# 与 app/repositories/qdrant/column_qdrant_repository.py 保持一致，
# 否则评测的前提就和线上不是一回事
SCORE_THRESHOLD = 0.45
LIMIT = 50

ALLOW_POS = ("n", "nr", "ns", "nt", "nz", "v", "vn", "a", "an", "eng", "i", "l")


# (问题, 答案字段的表.字段名)。答案是「要答对这个问题，必须召回到的字段」，
# 只标一个，且标的是**最不可替代**的那个。
GOLD: list[tuple[str, str]] = [
    ("今年各业务员的中标率是多少？", "fact_quotation.quote_status"),
    ("报价为什么没接到单？", "fact_quotation.lost_reason"),
    ("今年的合同金额是多少？", "fact_order_item.amount"),
    ("我们今年签了多少份合同？", "fact_order.order_id"),
    ("合同平均延期多少天？", "fact_order.delay_days"),
    ("各车间的加工量是多少？", "fact_production_process.process_output"),
    ("哪个车间的次品最多？", "fact_production_process.defect_quantity"),
    ("公司今年的实际产量是多少？", "fact_production.output_quantity"),
    ("哪些客户下单最多？", "dim_customer.customer_name"),
    ("这个产品的只重是多少？", "dim_product.unit_weight"),
    ("样品的准时情况怎么样？", "fact_sample.is_on_time"),
    ("这个业务员属于哪个部门？", "dim_employee.department"),
]


def load_columns() -> list[dict]:

    config = OmegaConf.load(META_CONFIG)
    columns = []
    for table in config.tables:
        for column in table.columns:
            aliases = list(column.get("alias") or [])
            columns.append(
                {
                    "id": f"{table.name}.{column.name}",
                    "table": table.name,
                    "name": column.name,
                    "text": " ".join(
                        part
                        for part in (
                            f"{table.name}.{column.name}",
                            column.get("description") or "",
                            " ".join(aliases),
                        )
                        if part
                    ),
                }
            )
    return columns


async def embed(texts: list[str]) -> list[list[float]]:

    import urllib.request

    def _call(batch: list[str]):
        payload = json.dumps({"model": EMBED_MODEL, "input": batch}).encode("utf-8")
        request = urllib.request.Request(
            OLLAMA_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=300) as response:
            return json.loads(response.read().decode("utf-8"))["embeddings"]

    vectors: list[list[float]] = []
    # bge-m3 一次多喂几条更省时，但批太大 OOM，32 条一批比较稳
    for start in range(0, len(texts), 32):
        vectors.extend(await asyncio.to_thread(_call, texts[start : start + 32]))
    return vectors


def cosine(a: list[float], b: list[float]) -> float:

    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def rank_of(gold_id: str, order: list[str]) -> int | None:

    """1-based 名次；没进候选就返回 None。"""

    try:
        return order.index(gold_id) + 1
    except ValueError:
        return None


def main() -> None:

    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild", action="store_true", help="忽略向量缓存重算")
    args = parser.parse_args()

    columns = load_columns()
    print(f"载入字段 {len(columns)} 个")

    cache: dict[str, list[float]] = {}
    if CACHE_FILE.exists() and not args.rebuild:
        cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        print(f"命中向量缓存 {len(cache)} 条（--rebuild 可强制重算）")

    missing = [c["text"] for c in columns if c["text"] not in cache]
    if missing:
        print(f"需要现算的字段向量：{len(missing)} 条（bge-m3 本地推理）")
        vectors = asyncio.run(embed(missing))
        for text, vector in zip(missing, vectors):
            cache[text] = vector
        CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")

    column_vectors = [cache[c["text"]] for c in columns]

    # 问题向量也缓存，key 加前缀避免和字段撞车
    questions = [q for q, _ in GOLD]
    missing_q = [f"__q__{q}" for q in questions if f"__q__{q}" not in cache]
    if missing_q:
        vectors = asyncio.run(embed([key[len("__q__"):] for key in missing_q]))
        for key, vector in zip(missing_q, vectors):
            cache[key] = vector
        CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")

    print()

    rows = []
    for question, gold_id in GOLD:

        keywords = sorted(set(jieba.analyse.extract_tags(question, allowPOS=ALLOW_POS) + [question]))
        query_vector = cache[f"__q__{question}"]

        # 每个关键词各排一次序：等价于 recall_column 里「逐词 embed + Qdrant search」，
        # 并且拿到的是**有序**结果 —— 这正是旧代码丢掉的那个信息
        ranked_per_keyword: list[list[str]] = []
        for keyword in keywords:
            key = f"__kw__{keyword}"
            if key not in cache:
                vector = asyncio.run(embed([keyword]))[0]
                cache[key] = vector
                CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")
            keyword_vector = cache[key]

            scored = [
                (cosine(keyword_vector, column_vectors[i]), columns[i]["id"])
                for i in range(len(columns))
            ]
            scored = [(s, i) for s, i in scored if s >= SCORE_THRESHOLD]
            scored.sort(key=lambda pair: (-pair[0], pair[1]))
            ranked_per_keyword.append([i for _, i in scored[:LIMIT]])

        # 旧写法：先到先得去重
        legacy_order: list[str] = []
        seen: set[str] = set()
        for ranked in ranked_per_keyword:
            for column_id in ranked:
                if column_id not in seen:
                    seen.add(column_id)
                    legacy_order.append(column_id)

        # 新写法：RRF 融合
        fused_order = rrf_order(ranked_per_keyword)

        # 新写法 + 词面重排（cross-encoder 不在线时的兜底路径）
        by_id = {c["id"]: c for c in columns}
        reranked_order = sorted(
            fused_order,
            key=lambda cid: -lexical_score(question, by_id[cid]["text"]),
        )

        # RRF（归一化）与词面分各占一半。纯词面重排会把 RRF 好不容易算出来的
        # 名次信号整个丢掉，混合则是「保留融合结果，只用词面做微调」。
        rrf_scores = rrf_fuse_scores(ranked_per_keyword)
        top = max(rrf_scores.values()) if rrf_scores else 1.0
        blended_order = sorted(
            fused_order,
            key=lambda cid: -(
                0.5 * rrf_scores.get(cid, 0.0) / top
                + 0.5 * lexical_score(question, by_id[cid]["text"])
            ),
        )

        rows.append(
            {
                "question": question,
                "gold": gold_id,
                "keywords": len(keywords),
                "candidates": len(fused_order),
                "legacy": rank_of(gold_id, legacy_order),
                "rrf": rank_of(gold_id, fused_order),
                "rerank": rank_of(gold_id, reranked_order),
                "blend": rank_of(gold_id, blended_order),
            }
        )

    def _fmt(rank: int | None) -> str:
        return "—" if rank is None else str(rank)

    print(
        f"{'问题':<26}{'候选':>5}{'旧(先到先得)':>13}{'RRF':>7}"
        f"{'RRF+词面':>11}{'RRF+混合':>11}"
    )
    print("-" * 78)
    for row in rows:
        print(
            f"{row['question']:<26}{row['candidates']:>5}"
            f"{_fmt(row['legacy']):>13}{_fmt(row['rrf']):>7}"
            f"{_fmt(row['rerank']):>11}{_fmt(row['blend']):>11}"
        )

    print("-" * 78)

    def summarize(key: str) -> tuple[float, int, int, int]:

        ranks = [row[key] for row in rows if row[key] is not None]
        mrr = sum(1 / rank for rank in ranks) / len(rows)
        top1 = sum(1 for rank in ranks if rank == 1)
        top5 = sum(1 for rank in ranks if rank <= 5)
        missed = len(rows) - len(ranks)
        return mrr, top1, top5, missed

    print(f"{'':<26}{'MRR↑':>13}{'Top-1':>7}{'Top-5':>11}{'漏召回':>8}")
    for key, label in (
        ("legacy", "旧(先到先得)"),
        ("rrf", "RRF"),
        ("rerank", "RRF+词面重排"),
        ("blend", "RRF+混合重排"),
    ):
        mrr, top1, top5, missed = summarize(key)
        print(f"{label:<26}{mrr:>13.3f}{top1:>7}{top5:>11}{missed:>8}")

    print()
    print("注：只测字段召回的排序，不含取值/指标两路；只测词面重排，未测 cross-encoder。")


if __name__ == "__main__":
    main()
