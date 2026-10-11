
"""Cross-encoder 重排服务的客户端。

**为什么是 HTTP 客户端而不是 import 一个模型库**：cross-encoder 要跑
BERT 级的前向计算，Python 侧要么引 torch（Windows 上 2GB 起步，还和
Smart App Control 打架），要么引 onnxruntime 再单独管模型文件。这个项目
其余部分只用到向量检索，不该为了重排把整个深度学习栈拖进来。

TEI（text-embeddings-inference）提供标准的 ``POST /rerank``，一个容器起来
即可，Python 侧零新增依赖 —— 与项目已有的「Redis 不引、pbkdf2 用标准库」
的取舍保持一致。

服务没起 / 没开 / 超时，``score()`` 一律返回 ``None``，由调用方决定兜底，
**不抛异常** —— 重排是优化项，不该让它有能力把整条问数链路搞挂。
"""

import asyncio
import json
import urllib.error
import urllib.request

from app.conf.app_config import RerankConfig, app_config
from app.core.logging import logger


class RerankClient:

    def __init__(self, rerank_config: RerankConfig):

        self.rerank_config = rerank_config
        self._warned = False

    @property
    def enabled(self) -> bool:
        return bool(self.rerank_config.enable)

    def _post(self, path: str, payload: dict) -> dict | list:

        request = urllib.request.Request(
            url=f"{self.rerank_config.base_url.rstrip('/')}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(request, timeout=self.rerank_config.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _post_sync(self, path: str, payload: dict):

        """先按 TEI 当前字段名 ``texts`` 发；老版本用 ``documents``，422 时退回去。"""

        try:
            return self._post(path, payload)
        except urllib.error.HTTPError as e:
            if e.code == 422 and "texts" in payload:
                retry = dict(payload)
                retry["documents"] = retry.pop("texts")
                return self._post(path, retry)
            raise

    def _warn_once(self, message: str, *args) -> None:

        """服务没起的时候每个请求都记一条日志会刷屏，只记第一次。"""

        if not self._warned:
            logger.warning(message, *args)
            self._warned = True

    async def score(self, query: str, documents: list[str]) -> list[float] | None:

        """给每个候打分，返回与 ``documents`` **等长且同序**的分数列表。

        拿不到分数（未启用 / 连不上 / 报错）时返回 ``None``。
        """

        if not self.enabled or not documents:
            return None

        payload = {
            "query": query,
            "texts": documents,
            "raw_scores": True,
            "truncate": True,
        }

        try:
            result = await asyncio.to_thread(
                self._post_sync, self.rerank_config.path, payload
            )
        except Exception as e:
            self._warn_once(
                "重排服务不可用，本次回退到词面重排：{}: {}", type(e).__name__, e
            )
            return None

        try:
            scores = [0.0] * len(documents)
            for item in result:
                scores[int(item["index"])] = float(item["score"])
        except Exception as e:
            self._warn_once("重排服务返回格式不认识，本次回退到词面重排：{}", e)
            return None

        self._warned = False
        return scores


rerank_client = RerankClient(app_config.rerank)
