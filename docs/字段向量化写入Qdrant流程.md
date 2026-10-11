# 字段向量化写入 Qdrant：全流程详解

> 本文只讲一条链路：**把上一步补齐好的字段元数据（ColumnInfo）向量化，写进 Qdrant 向量库**，供后续"问数"阶段做语义召回。
> 目的：搞懂"为什么向量化""一个字段为什么拆多个点""向量和元数据怎么分开存"。

---

## 一、一句话总览

> **把"字段的各种叫法（名字、描述、别名）"分别转成向量，作为独立的"语义入口"写进 Qdrant。这样用户以后用自然语言问"合同金额"，能通过向量相似度找到 fact_order_item.amount 这个字段。**

展开就是三件事：

1. **建集合**：确保 Qdrant 里有一个存字段向量的集合（第一次建，之后跳过）。
2. **拆点**：每个字段拆成多个 point——字段名、字段描述、每个别名各是一个点。
3. **向量化 + 写入**：把每个点的文本批量转成向量，连同字段完整信息（payload）一起写入。

---

## 二、为什么需要向量化（先懂"为什么"，再看"怎么做"）

上一步我们把字段元数据存进了 MySQL meta 库。但 MySQL 是**精确匹配**：

```sql
-- 只能这么查：字段名必须一字不差
SELECT * FROM column_info WHERE name = 'amount';
```

可用户不会问"amount"，用户问的是：**"查一下金额"**、**"合同金额是多少"**、**"明细金额"**。

这些词不是字段名，而是 `amount` 的 **alias（别名）**。MySQL 的 `WHERE name = '合同金额'` 永远匹配不到——因为 name 列存的是 `amount`，不是 `合同金额`。

向量化解决的问题：把 `合同金额`、`金额`、`明细金额` 这些别名也转成向量。用户问"合同金额"时，拿"合同金额"的向量去 Qdrant 里搜，就能找到那个"向量方向最接近"的点——那个点恰好就是 `fact_order_item.amount` 字段（因为它 alias 里有"合同金额"）。

**一句话：MySQL 负责"精确召回"，Qdrant 负责"语义召回"。两者互补。**

---

## 三、分层：为什么这次比 MySQL 步骤少两层

这是上一轮已经讨论过的结论，这里再钉一次：

```
写 MySQL（上一步）：
  ColumnInfo(实体) → Mapper → ColumnInfoMySQL(ORM模型) → SQL INSERT
                          ↑ models + mappers 两层

写 Qdrant（这一步）：
  ColumnInfo(实体) → asdict() → payload(dict) → upsert point
                     ↑ 一步到位，没有中间层
```

- MySQL 是**关系型库**，要靠 SQLAlchemy ORM 做"类 ↔ 表"映射，所以需要 `models/` + `mappers/`。
- Qdrant 是**向量库**，point 的 `payload` 就是自由 JSON，把 `asdict(实体)` 直接塞进去就行，**没有映射这回事**。

所以这次只有：**Service（编排）+ Repository（建集合/写点）+ 两个已存在的 client**。实体层 `ColumnInfo` 照样在用——它是贯穿所有存储的"标准箱子"。

---

## 四、文件清单（按调用顺序）

| 顺序 | 文件 | 层 | 职责 |
|---|---|---|---|
| 1 | `scripts/build_meta_knowledge.py` | 入口 | init 客户端，注入 qdrant repo + embedding client |
| 2 | `services/meta_knowledge_service.py` | 编排 | `_save_column_info_to_qdrant()`：拆点 → 向量化 → 写 |
| 3 | `repositories/qdrant/column_qdrant_repository.py` | 仓储 | `ensure_collection()` + `upsert()`（+ `search()` 备用） |
| 4 | `clients/qdrant_client.py` | 连接 | 已存在，提供 `qdrant_client_manager.client` |
| 5 | `clients/embedding_client.py` | 连接 | 已存在，提供 `embedding_client_manager.client`（bge-m3） |

> 注意：`clients/` 和 `entities/column_info.py` 都是**上一步已经写好的**，这次不用动。新写/修改的只有前 3 个。

---

## 五、完整执行顺序（6 步，对号入座）

```
① 确保集合存在
   └─ _save_column_info_to_qdrant(): await column_qdrant_repository.ensure_collection()

② 遍历字段，每个字段拆成多个 point
   └─ for column_info in column_infos:
        追加 name / description / 每个 alias 各一个 point

③ 抽出所有待向量化文本
   └─ embedding_texts = [point["embedding_text"] for point in points]

④ 分批调用向量化（batch = 20）
   └─ await embedding_client.aembed_documents(batch_texts)

⑤ 抽出 id 和 payload
   └─ ids = [...] ；payloads = [...]

⑥ 分批写入 Qdrant（batch_size = 10）
   └─ await column_qdrant_repository.upsert(ids, embeddings, payloads)
```

---

## 六、逐层代码走读（关键代码 + 为什么）

### 6.1 仓储层：`column_qdrant_repository.py`

```python
async def ensure_collection(self):
    """确保集合存在：不存在就创建，存在就跳过（幂等）"""
    if not await self.client.collection_exists(self.collection_name):
        await self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=models.VectorParams(
                size=app_config.qdrant.embedding_size,   # 1024
                distance=models.Distance.COSINE,
            ),
        )
```

**两个关键点：**

1. **`size=1024` 必须和 bge-m3 输出维度一致。** bge-m3 把任何文本都转成 1024 维向量，集合就必须声明成 1024 维。填错了，写入时 Qdrant 会报"向量维度不匹配"。这就是为什么这个值不硬编码、从 `app_config.qdrant.embedding_size` 读。

2. **`COSINE` 余弦相似度**：最常用的语义度量。两个向量方向越接近，余弦值越接近 1，代表语义越相近。相比欧氏距离，余弦只看方向、不看长度，更适合文本语义。

```python
async def upsert(self, ids, embeddings, payloads, batch_size=10):
    points = [
        models.PointStruct(id=id, vector=embedding, payload=payload)
        for id, embedding, payload in zip(ids, embeddings, payloads)
    ]
    for i in range(0, len(points), batch_size):
        await self.client.upsert(
            collection_name=self.collection_name, points=points[i : i + batch_size]
        )
```

**关键点：`zip` 把三个列表按位置打包。** `ids[0] + embeddings[0] + payloads[0]` 合成第 1 个点，以此类推。所以**三个列表的顺序必须严格对齐**——这是 Service 层保证的（见 6.2）。

**一个 Qdrant point 的结构：**

| 字段 | 作用 | 本链路填什么 |
|---|---|---|
| `id` | 主键（唯一） | `uuid.uuid4()` 随机生成 |
| `vector` | 用于相似度计算的向量 | bge-m3 输出的 1024 维浮点列表 |
| `payload` | 附带元数据（任意 JSON） | `asdict(column_info)` 完整字段信息 |

### 6.2 编排层：`_save_column_info_to_qdrant`

```python
async def _save_column_info_to_qdrant(self, column_infos: list[ColumnInfo]):
    await self.column_qdrant_repository.ensure_collection()   # ①

    points: list[dict] = []
    for column_info in column_infos:
        # 一个字段拆三个语义入口：名字 / 描述 / 每个别名
        points.append({"id": uuid.uuid4(), "embedding_text": column_info.name,        "payload": asdict(column_info)})
        points.append({"id": uuid.uuid4(), "embedding_text": column_info.description, "payload": asdict(column_info)})
        for alias in column_info.alias:
            points.append({"id": uuid.uuid4(), "embedding_text": alias,               "payload": asdict(column_info)})

    # ③④ 抽出文本 → 分批向量化
    embeddings: list[list[float]] = []
    embedding_texts = [point["embedding_text"] for point in points]
    for i in range(0, len(embedding_texts), 20):
        batch_embeddings = await self.embedding_client.aembed_documents(
            embedding_texts[i : i + 20]
        )
        embeddings.extend(batch_embeddings)

    # ⑤⑥ 抽出 id + payload → 交给 Repository 写
    ids = [point["id"] for point in points]
    payloads = [point["payload"] for point in points]
    await self.column_qdrant_repository.upsert(ids, embeddings, payloads)
```

**注意这里的"两步走"设计：**

- **先攒 `points`（含 embedding_text + payload）**，但**暂时不向量化**。
- 攒完后，**单独抽出 `embedding_texts` 去批量向量化**，得到 `embeddings`。
- 最后把 `ids / embeddings / payloads` 三组一起交给 Repository。

为什么要把 `embedding_text` 和 `payload` 分开存？因为**向量化是昂贵的**（要调 Ollama，一批一批来），而 `payload` 全程不需要变。把它们分开，才能"只把该向量化的文本拿去向量化"，而 payload 原样跟着走。

### 6.3 入口脚本：组装（依赖注入）

```python
# 初始化
qdrant_client_manager.init()
embedding_client_manager.init()

# 创建 Repository + Service（注入依赖）
column_qdrant_repo = ColumnQdrantRepository(qdrant_client_manager.client)
service = MetaKnowledgeService(
    meta_repo,
    dw_repo,
    column_qdrant_repo,
    embedding_client_manager.client,   # ← 传的是 client，不是 manager
)
```

**关键点：注入的是 `.client`，不是 `.manager`。** Service 需要的是"能向量化的对象"（`aembed_documents`），是 `OllamaEmbeddings` 实例，即 `embedding_client_manager.client`。Manager 只是"管生命周期"的壳，不参与业务。

---

## 七、四个核心概念（重点看）

### 7.1 一个字段 → 多个 point（为什么拆）

一个字段有多种"被人叫到的可能"：

- 字段名：`amount`
- 描述：`该行产品的金额，按所属合同头的币种计价。合同金额 = SUM(该字段)。`
- 别名：`金额`、`合同金额`、`明细金额`

如果只给 `amount` 这 6 个字母建一个向量，用户问"明细金额"时就搜不到。所以**每种叫法各建一个 point**，但它们的 `payload` 都指向同一个字段实体。这样不管用户用哪种叫法，都能召回到 `fact_order_item.amount`。

### 7.2 `embedding_text` 和 `payload` 分离

- `embedding_text`：**用来生成向量的文本**（决定"搜什么能搜到我"）。
- `payload`：**召回后要还给业务层的完整信息**（决定"搜到我之后能拿到什么"）。

两者各司其职：`embedding_text` 进模型转成向量，`payload` 原样存进 Qdrant。召回时 Qdrant 把 `payload` 吐回来，`ColumnInfo(**payload)` 还原成实体。

### 7.3 `id` 用 `uuid.uuid4()`，不是字段 id

上一步 MySQL 里字段 id 是 `表名.字段名`（如 `fact_order_item.amount`），那是唯一的。

但**这里一个字段拆成了多个 point**，如果都用 `fact_order_item.amount` 当 id，同一字段的 5 个 point 就会互相覆盖，最后只剩一个。所以每个 point 必须用**全局唯一的 uuid**，让"字段 id"和"point id"解耦。

### 7.4 为什么分批（embedding 20、upsert 10）

- **向量化分批（20）**：`aembed_documents` 一次请求处理一批文本。一次塞太多，请求体过大、Ollama 压力大、容易超时。20 是经验值。
- **写入分批（10）**：`upsert` 一次写 10 个点。避免一次提交过多 point，也方便出错时定位到是哪一批。

---

## 八、用真实数据走一遍

以 `fact_order_item.amount` 这个字段为例：

```
【上一步得到的 ColumnInfo 实体】
  id         = "fact_order_item.amount"
  name       = "amount"
  type       = "decimal(12,2)"   （dw 库查出来的真实类型）
  role       = "measure"
  examples   = [123.45, 89.0, ...]
  description= "该行产品的金额，按所属合同头的币种计价。合同金额 = SUM(该字段)。"
  alias      = [金额, 合同金额, 明细金额]
  table_id   = "fact_order_item"

【拆成 5 个 point】  1(name) + 1(description) + 3(alias)
  点1: embedding_text="amount"                                    payload={上面对象的 asdict}
  点2: embedding_text="该行产品的金额，按所属合同头的币种计价。合同金额 = SUM(该字段)。"  payload={同上}
  点3: embedding_text="金额"                                      payload={同上}
  点4: embedding_text="合同金额"                                  payload={同上}
  点5: embedding_text="明细金额"                                  payload={同上}

【向量化】
  "合同金额" → bge-m3 → [0.023, -0.011, ... 1024 个浮点]

【写入 Qdrant】
  collection: column_info_collection
  point: { id: uuid, vector: 1024维, payload: 字段完整信息 }

【未来召回】
  用户问"合同金额" → "合同金额"向量化 → query_points 搜索
  → 命中"点4"（alias=合同金额）→ 返回 payload → ColumnInfo(**payload) 还原
  → 拿到字段名 amount、类型 decimal(12,2)、描述、所属表 fact_order_item
```

整个 86 个字段的流程：每个字段都这样拆，最终 Qdrant 的 `column_info_collection` 里有 `86 × (至少 2 + 别名个数)` 个点。

---

## 九、容易混淆 / 踩坑的点（汇总）

1. **维度不匹配**：`ensure_collection` 里的 `size` 必须等于 Embedding 模型的输出维度。bge-m3 是 1024，填 768 或 1536 都会在写入时报错。

2. **id 冲突**：不能用字段 id 当 point id（一个字段多个点会互相覆盖），必须用 uuid。

3. **顺序对齐**：`ids / embeddings / payloads` 三列表必须按 `zip` 顺序严格对应。`aembed_documents` 返回的向量顺序和输入文本顺序一致，靠 `extend` 累积保证不乱。

4. **集合幂等**：`ensure_collection` 先 `collection_exists` 判断，重复运行构建脚本不会报"集合已存在"，也不会重复建。

5. **payload 存的是 `asdict` 结果**：所以实体字段名必须稳定。以后 `ColumnInfo` 加字段，payload 自动多一列，召回时 `ColumnInfo(**payload)` 也能对上（前提是字段名一致）。

6. **`embedding_client` 没有 `close()`**：Ollama 是无状态 HTTP 客户端，不像 MySQL/Qdrant 有连接池要手动关。

---

## 十、下一步（未完成的部分）

当前链路到"字段向量写入 Qdrant"为止。接下来还要补：

1. **写 ES**：把 `sync=true` 的字段真实取值写进 ES（`_save_value_info_to_es`、`value_es_repository`）。
2. **指标链路**：`metrics` 的入库 + 向量化（`_save_metrics_to_meta_db`、`_save_metric_info_to_qdrant`、`metric_qdrant_repository`、`metric_info`/`column_metric` 实体/模型/映射）。

这两块的 Repository 都还没写。
