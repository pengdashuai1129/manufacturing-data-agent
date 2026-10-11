"""召回融合与重排的单元测试。

只测纯函数，不需要 MySQL / Qdrant / ES / Ollama —— 排序这种事本来就不该
为了测它而把整套服务拉起来。

    uv run python -m unittest discover -s tests -t .
"""

import os
import unittest
from types import SimpleNamespace

# app/conf/app_config.py 在导入时就会读 conf/app_config.yaml，其中 llm.api_key
# 取自环境变量。这些测试不碰模型，但模块导入会连带触发配置加载，所以先补一个
# 占位值，免得测试跑不起来。
os.environ.setdefault("DEEPSEEK_API_KEY", "not-used-by-unit-tests")

from app.agent.nodes.rerank_retrieved_info import (
    blend_fallback_scores,
    column_text,
    is_protected,
    protected_columns,
    rerank_metrics,
    rerank_tables,
)
from app.agent.ranking import lexical_score, rrf_fuse, rrf_order


def column(name, role="dimension", description="", alias=None, examples=None):
    return {
        "name": name,
        "type": "varchar",
        "role": role,
        "examples": examples or [],
        "description": description,
        "alias": alias or [],
    }


def table(name, columns):
    return {"name": name, "role": "fact", "description": "", "columns": columns}


class TestRrfFuse(unittest.TestCase):

    def test_multi_channel_hit_beats_single_channel_win(self):
        """两路都命中的 y 要排在单路第一的 x 前面 —— 这正是 RRF 的价值。"""

        fused = rrf_fuse([["x", "y"], ["y", "z"]])
        self.assertEqual([f.id for f in fused], ["y", "x", "z"])

        by_id = {f.id: f for f in fused}
        # 1/(60+2) + 1/(60+1)
        self.assertAlmostEqual(by_id["y"].score, 1 / 62 + 1 / 61)
        self.assertEqual(by_id["y"].hits, 2)
        self.assertEqual(by_id["y"].best_rank, 1)
        self.assertEqual(by_id["x"].hits, 1)

    def test_result_does_not_depend_on_channel_order(self):
        """RRF 是求和，交换两路的先后不该改变结果。"""

        a = rrf_order([["x", "y"], ["y", "z"]])
        b = rrf_order([["y", "z"], ["x", "y"]])
        self.assertEqual(a, b)

    def test_duplicate_within_one_channel_counted_once(self):
        """同一路里重复出现的 id 只算第一次，否则会被重复加权。"""

        fused = rrf_fuse([["x", "x", "y"]])
        self.assertEqual([f.id for f in fused], ["x", "y"])
        self.assertAlmostEqual(fused[0].score, 1 / 61)

    def test_ties_break_deterministically(self):
        """两路各出一名单，分数与最优名次都相同 —— 此时按 id 定序。

        这正是「不靠 dict/set 迭代顺序」的意义：换一路的先后、换个进程，
        结果都一样。
        """

        self.assertEqual(rrf_order([["a"], ["b"]]), ["a", "b"])
        self.assertEqual(rrf_order([["b"], ["a"]]), ["a", "b"])

    def test_empty_input(self):
        self.assertEqual(rrf_order([]), [])
        self.assertEqual(rrf_order([[], []]), [])

    def test_rank_beats_channel_count_when_lopsided(self):
        """名次足够靠前时，单路第一可以压过「两路都靠后」。"""

        fused = rrf_fuse([["top"], ["a", "b", "c", "d", "e", "top"]])
        by_id = {f.id: f for f in fused}
        self.assertGreater(by_id["top"].score, by_id["a"].score)

    def test_larger_k_flattens_the_score_gap(self):
        small = {f.id: f.score for f in rrf_fuse([["a", "b"]], k=1)}
        large = {f.id: f.score for f in rrf_fuse([["a", "b"]], k=1000)}
        self.assertGreater(small["a"] - small["b"], large["a"] - large["b"])


class TestLexicalScore(unittest.TestCase):

    def test_identical_text_scores_one(self):
        self.assertAlmostEqual(lexical_score("中标率", "中标率"), 1.0)

    def test_disjoint_text_scores_zero(self):
        self.assertAlmostEqual(lexical_score("中标率", "车间加工量"), 0.0)

    def test_partial_overlap_between_zero_and_one(self):
        score = lexical_score("今年各业务员的中标率", "中标率")
        self.assertGreater(score, 0.0)
        self.assertLess(score, 1.0)

    def test_relevant_candidate_outscores_irrelevant_one(self):
        query = "各车间的加工量"
        relevant = column_text("fact_production_process", column("process_output", description="车间加工量"))
        irrelevant = column_text("dim_customer", column("customer_name", description="客户名称"))
        self.assertGreater(
            lexical_score(query, relevant), lexical_score(query, irrelevant)
        )

    def test_empty_query_or_document(self):
        self.assertEqual(lexical_score("", "中标率"), 0.0)
        self.assertEqual(lexical_score("中标率", ""), 0.0)

    def test_punctuation_and_case_are_ignored(self):
        self.assertAlmostEqual(lexical_score("USD 币种", "usd币种"), 1.0)


class TestColumnText(unittest.TestCase):

    def test_includes_table_name_description_alias_and_examples(self):
        text = column_text(
            "fact_quotation",
            column("quote_status", description="报价状态", alias=["报价情况"], examples=["中标", "落标"]),
        )
        self.assertIn("fact_quotation.quote_status", text)
        self.assertIn("报价状态", text)
        self.assertIn("报价情况", text)
        self.assertIn("中标", text)

    def test_missing_optional_parts_do_not_leave_stray_spaces(self):
        self.assertEqual(column_text("t", column("c")), "t.c")


class TestProtection(unittest.TestCase):

    def test_primary_and_foreign_keys_are_protected(self):
        protected = set()
        self.assertTrue(is_protected("t", column("id", role="primary_key"), protected))
        self.assertTrue(is_protected("t", column("cid", role="foreign_key"), protected))
        self.assertFalse(is_protected("t", column("amt", role="measure"), protected))

    def test_metric_dependency_is_protected_case_insensitively(self):
        metrics = [{"name": "中标率", "description": "", "alias": [],
                    "relevant_columns": ["fact_quotation.quote_status"]}]
        protected = protected_columns(metrics)
        self.assertIn(("fact_quotation", "quote_status"), protected)
        self.assertTrue(
            is_protected("Fact_Quotation", column("quote_status"), protected)
        )
        self.assertFalse(is_protected("fact_quotation", column("currency"), protected))

    def test_reference_without_dot_is_ignored(self):
        metrics = [{"name": "m", "description": "", "alias": [], "relevant_columns": ["裸字段名"]}]
        self.assertEqual(protected_columns(metrics), set())


class TestRerankTables(unittest.TestCase):

    def test_orders_columns_by_score(self):
        tables = [table("t", [column("a"), column("b"), column("c")])]
        score_of = {("t", "a"): 0.1, ("t", "b"): 0.9, ("t", "c"): 0.5}
        rerank_tables(tables, score_of, set())
        self.assertEqual([c["name"] for c in tables[0]["columns"]], ["b", "c", "a"])

    def test_protected_column_leads_even_with_zero_score(self):
        tables = [table("t", [column("amt", role="measure"), column("oid", role="primary_key")])]
        score_of = {("t", "amt"): 0.99, ("t", "oid"): 0.0}
        rerank_tables(tables, score_of, set())
        self.assertEqual([c["name"] for c in tables[0]["columns"]], ["oid", "amt"])

    def test_equal_scores_keep_original_order(self):
        """稳定排序：重排不该在没信息的时候把 RRF 融合的顺序打乱。"""

        tables = [table("t", [column("a"), column("b"), column("c")])]
        rerank_tables(tables, {}, set())
        self.assertEqual([c["name"] for c in tables[0]["columns"]], ["a", "b", "c"])

    def test_no_trimming_when_cap_is_zero(self):
        tables = [table("t", [column(f"c{i}") for i in range(10)])]
        _, dropped = rerank_tables(tables, {}, set(), max_per_table=0)
        self.assertEqual(dropped, 0)
        self.assertEqual(len(tables[0]["columns"]), 10)

    def test_trimming_keeps_top_scored_columns(self):
        tables = [table("t", [
            column("pk", role="primary_key"),
            column("a"), column("b"), column("c"), column("d"),
        ])]
        score_of = {("t", "a"): 0.9, ("t", "b"): 0.8, ("t", "c"): 0.7, ("t", "d"): 0.1}
        _, dropped = rerank_tables(tables, score_of, set(), max_per_table=2)
        self.assertEqual([c["name"] for c in tables[0]["columns"]], ["pk", "a"])
        self.assertEqual(dropped, 3)

    def test_trimming_never_drops_protected_columns(self):
        """保护列比上限还多时，上限让位 —— 宁可多留列，也不能丢 JOIN 骨架。"""

        tables = [table("t", [
            column("pk", role="primary_key"),
            column("fk1", role="foreign_key"),
            column("fk2", role="foreign_key"),
            column("a"), column("b"), column("c"), column("d"), column("e"),
        ])]
        rerank_tables(tables, {}, set(), max_per_table=2)
        names = [c["name"] for c in tables[0]["columns"]]
        self.assertEqual(names, ["pk", "fk1", "fk2"])
        self.assertEqual(len(names), 3)

    def test_metric_dependency_survives_trimming(self):
        metrics = [{"name": "中标率", "description": "", "alias": [],
                    "relevant_columns": ["t.quote_status"]}]
        protected = protected_columns(metrics)
        tables = [table("t", [column("a"), column("quote_status"), column("b"), column("c")])]
        score_of = {("t", "a"): 0.9, ("t", "b"): 0.8, ("t", "quote_status"): 0.0, ("t", "c"): 0.7}
        rerank_tables(tables, score_of, protected, max_per_table=2)
        self.assertIn("quote_status", [c["name"] for c in tables[0]["columns"]])

    def test_tables_sorted_by_best_column_score(self):
        tables = [
            table("low", [column("a")]),
            table("high", [column("b")]),
        ]
        score_of = {("low", "a"): 0.1, ("high", "b"): 0.9}
        rerank_tables(tables, score_of, set())
        self.assertEqual([t["name"] for t in tables], ["high", "low"])

    def test_table_order_stable_when_all_scores_zero(self):
        tables = [table("x", [column("a")]), table("y", [column("b")])]
        rerank_tables(tables, {}, set())
        self.assertEqual([t["name"] for t in tables], ["x", "y"])


class TestRerankMetrics(unittest.TestCase):

    def test_orders_by_score(self):
        metrics = [
            {"name": "A", "description": "", "alias": [], "relevant_columns": []},
            {"name": "B", "description": "", "alias": [], "relevant_columns": []},
        ]
        ordered = rerank_metrics(metrics, [0.2, 0.8])
        self.assertEqual([m["name"] for m in ordered], ["B", "A"])

    def test_ties_keep_original_order(self):
        metrics = [
            {"name": "A", "description": "", "alias": [], "relevant_columns": []},
            {"name": "B", "description": "", "alias": [], "relevant_columns": []},
        ]
        ordered = rerank_metrics(metrics, [0.5, 0.5])
        self.assertEqual([m["name"] for m in ordered], ["A", "B"])


class TestBlendFallback(unittest.TestCase):

    def test_low_rrf_but_exact_lexical_match_still_scores_high(self):
        """融合分低但词面完全命中，混合分不该垫底 —— 这是混合的意义。"""

        scores = blend_fallback_scores(
            "中标率",
            ["t.hit", "t.miss"],
            ["t.hit 中标率", "t.miss 客户名称"],
            {"t.hit": 0.001, "t.miss": 0.05},
        )
        self.assertGreater(scores[0], scores[1])

    def test_rrf_prior_breaks_ties_when_text_is_equally_irrelevant(self):
        scores = blend_fallback_scores(
            "完全无关的问题",
            ["t.a", "t.b"],
            ["t.a 甲", "t.b 乙"],
            {"t.a": 0.05, "t.b": 0.01},
        )
        self.assertGreater(scores[0], scores[1])

    def test_missing_recall_scores_default_to_zero_prior(self):
        scores = blend_fallback_scores("x", ["t.a"], ["t.a"], {})
        self.assertEqual(scores, [0.0])

    def test_scores_stay_within_unit_interval(self):
        scores = blend_fallback_scores(
            "中标率", ["t.a"], ["t.a 中标率"], {"t.a": 0.05}
        )
        self.assertGreaterEqual(scores[0], 0.0)
        self.assertLessEqual(scores[0], 1.0)


class TestRerankNode(unittest.IsolatedAsyncioTestCase):
    """跑真正的节点函数，但把重排服务换成桩 —— 不用起 MySQL / Qdrant / ES。"""

    class _Config:
        base_url = "http://stub"

    class _StubClient:
        """``scores=None`` 模拟服务没起（走词面兜底），否则返回给定分数。"""

        def __init__(self, scores=None):
            self.scores = scores
            self.calls = 0

        @property
        def rerank_config(self):
            return TestRerankNode._Config()

        async def score(self, query, documents):
            self.calls += 1
            return self.scores

    def _state(self, **overrides):
        state = {
            "query": "各车间的加工量是多少",
            "table_infos": [
                table("fact_production_process", [
                    column("process_output", description="工序产出数量，车间加工量"),
                    column("workshop_id", role="foreign_key"),
                    column("defect_quantity", role="measure", description="不良数量"),
                ]),
                table("dim_customer", [
                    column("customer_name", description="客户名称"),
                    column("customer_id", role="primary_key"),
                ]),
            ],
            "metric_infos": [
                {"name": "合同金额", "description": "所有合同明细金额的合计",
                 "alias": ["销售额"], "relevant_columns": ["fact_order_item.amount"]},
            ],
            "recall_scores": {
                "fact_production_process.process_output": 0.032,
                "fact_production_process.workshop_id": 0.016,
                "fact_production_process.defect_quantity": 0.015,
                "dim_customer.customer_name": 0.031,
                "dim_customer.customer_id": 0.014,
            },
        }
        state.update(overrides)
        return state

    def _runtime(self, client):
        return SimpleNamespace(context={"rerank_client": client})

    async def test_falls_back_to_blend_when_service_is_down(self):
        client = self._StubClient(scores=None)
        from app.agent.nodes.rerank_retrieved_info import rerank_retrieved_info

        out = await rerank_retrieved_info(self._state(), self._runtime(client))

        self.assertIn("混合重排", out["rerank_note"])
        # 字段一轮 + 指标一轮
        self.assertEqual(client.calls, 2)

    async def test_relevant_table_moves_to_the_front(self):
        from app.agent.nodes.rerank_retrieved_info import rerank_retrieved_info

        out = await rerank_retrieved_info(
            self._state(), self._runtime(self._StubClient(scores=None))
        )
        self.assertEqual(out["table_infos"][0]["name"], "fact_production_process")

    async def test_fk_column_still_leads_the_table(self):
        from app.agent.nodes.rerank_retrieved_info import rerank_retrieved_info

        out = await rerank_retrieved_info(
            self._state(), self._runtime(self._StubClient(scores=None))
        )
        process_table = out["table_infos"][0]
        self.assertEqual(process_table["columns"][0]["name"], "workshop_id")

    async def test_uses_cross_encoder_scores_when_available(self):
        from app.agent.nodes.rerank_retrieved_info import rerank_retrieved_info

        # 第一轮是 5 个字段，第二轮是 1 个指标 —— 桩按顺序给出不同分数
        client = self._StubClient(scores=[0.0] * 5)
        out = await rerank_retrieved_info(self._state(), self._runtime(client))

        self.assertIn("cross-encoder", out["rerank_note"])
        self.assertEqual(client.calls, 2)

    async def test_empty_candidates_short_circuit(self):
        from app.agent.nodes.rerank_retrieved_info import rerank_retrieved_info

        client = self._StubClient(scores=None)
        out = await rerank_retrieved_info(
            self._state(table_infos=[], metric_infos=[]), self._runtime(client)
        )

        self.assertEqual(client.calls, 0)
        self.assertIn("跳过重排", out["rerank_note"])

    async def test_output_state_keys_are_all_declared(self):
        """返回的键必须都在 DataAgentState 里，否则 LangGraph 会拒绝这次更新。"""

        from app.agent.nodes.rerank_retrieved_info import rerank_retrieved_info
        from app.agent.state import DataAgentState

        out = await rerank_retrieved_info(
            self._state(), self._runtime(self._StubClient(scores=None))
        )
        for key in out:
            self.assertIn(key, DataAgentState.__annotations__)


if __name__ == "__main__":
    unittest.main()
