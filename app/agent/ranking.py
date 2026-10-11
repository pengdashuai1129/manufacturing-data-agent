
"""召回结果的融合与重排。

这个模块里全是纯函数，不依赖任何框架、不碰数据库、不调模型 —— 目的是
让「排序」这件事能被单独测明白，而不是混在节点里靠日志看。

两件事分开：

1. ``rrf_fuse`` —— 多路召回的**排名融合**。

   每个关键词各查一次向量库（或全文库），得到若干条**各自已经有序**的候选。
   同一份元数据会被多个关键词命中。旧写法是「先到先得」去重
   （``if info.id not in map``），于是最终顺序取决于哪个关键词先被遍历到 ——
   而那其实是 set 的迭代顺序，也就是 PYTHONHASHSEED 的产物，换个进程就变。
   既不可复现，也没有语义。

   RRF（Reciprocal Rank Fusion）用 ``1/(k + rank)`` 把每一路的排名累加：
   命中路数越多、在各自那一路里排得越靠前，总分越高。只用到名次、不用到
   相似度分数，所以 Qdrant 的余弦分和 ES 的 BM25 分可以直接混在一起比 ——
   这正是它比「分数归一化后加权」更省事的地方。

2. ``lexical_score`` —— 一个确定性的、不依赖模型的**词面相关性打分**。

   它是重排节点的兜底打分器：cross-encoder 没配好（比如没起 TEI 容器）时，
   重排链路照样能跑通且结果可复现。**它不是 cross-encoder 的替代品** ——
   词面重合判断不了「产量」和「车间加工量」这种同领域但不同口径的差别，
   那正是需要 cross-encoder 的地方。
"""

import re
from dataclasses import dataclass

RRF_K = 60

_PUNCT_RE = re.compile(r"[\s\W_]+", re.UNICODE)


@dataclass(frozen=True)
class FusedHit:
    """一条候选在融合之后的结果。"""

    id: str
    score: float
    hits: int
    best_rank: int


def rrf_fuse(ranked_id_lists, k: int = RRF_K) -> list[FusedHit]:
    """把若干条各自有序的 id 列表融合成一个排序。

    :param ranked_id_lists: 每一路召回的结果，**按相关性从高到低排列**，
        即 ``ranked_id_lists[i][0]`` 是第 i 路的第一名。
    :param k: RRF 的平滑常数。取 60 是原论文的经验值；调大它会让
        「多路都命中」比「单路排第一」更重要。

    返回按 (分数降序, 最好名次升序, id 升序) 排列的列表 —— 后两个键是为了
    在分数打平时给出**确定性**结果，否则同一份输入可能给出不同顺序。
    """
    scores: dict[str, float] = {}
    hits: dict[str, int] = {}
    best_rank: dict[str, int] = {}

    for ranked in ranked_id_lists or []:
        seen: set[str] = set()
        rank = 0
        for item_id in ranked:
            if item_id in seen:
                # 同一路里重复出现只算第一次，否则重复项会被重复加权
                continue
            seen.add(item_id)
            rank += 1
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
            hits[item_id] = hits.get(item_id, 0) + 1
            if item_id not in best_rank or rank < best_rank[item_id]:
                best_rank[item_id] = rank

    ordered = sorted(
        scores,
        key=lambda item_id: (-scores[item_id], best_rank[item_id], item_id),
    )
    return [
        FusedHit(
            id=item_id,
            score=scores[item_id],
            hits=hits[item_id],
            best_rank=best_rank[item_id],
        )
        for item_id in ordered
    ]


def rrf_order(ranked_id_lists, k: int = RRF_K) -> list[str]:
    """``rrf_fuse`` 的薄封装，只要排序结果不要分数。"""
    return [hit.id for hit in rrf_fuse(ranked_id_lists, k=k)]


def _bigrams(text: str) -> set[str]:
    """中英混排都适用的字符二元组。中文没有空格，词面重叠只能看 n-gram。"""
    cleaned = _PUNCT_RE.sub("", (text or "").lower())
    if not cleaned:
        return set()
    if len(cleaned) < 2:
        return {cleaned}
    return {cleaned[i : i + 2] for i in range(len(cleaned) - 1)}


def lexical_score(query: str, document: str) -> float:
    """query 的字符二元组有多大比例被候选文本覆盖，落在 [0, 1]。

    用「覆盖率」而不是 Dice / Jaccard，是因为候选文本的长短差异很大 ——
    一个字段的描述可能两百字，别名可能只有三个字。用并集做分母的话，
    描述写得越详细反而越吃亏，这与「描述详细说明它更相关」的直觉相反。
    """
    query_grams = _bigrams(query)
    if not query_grams:
        return 0.0
    document_grams = _bigrams(document)
    if not document_grams:
        return 0.0
    return len(query_grams & document_grams) / len(query_grams)
