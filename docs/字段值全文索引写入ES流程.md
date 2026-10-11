# 字段值全文索引写入 ES：全流程详解

> 本文只讲一条链路：**把"允许同步"的字段真实取值，写进 Elasticsearch 全文索引**，供后续"问数"阶段做枚举值检索。
> 目的：搞懂"为什么需要 ES""哪些字段才值得同步""keyword 和 text 分词的差别"。

---

## 一、一句话总览

> **把 `sync=true` 的字段真实取值（如国家、客户等级、产品类型）逐个抽出来，写进 ES。这样用户问"客户分布在哪些国家""客户等级有几档"时，系统能靠全文检索找到这些枚举值。**

---

## 二、为什么需要 ES（先懂"为什么"）

前面两个存储各解决一个问题，但都漏了一块：

| 存储 | 解决什么 | 回答不了什么 |
|---|---|---|
| MySQL（meta 库） | 字段的**精确元数据**（名字/类型/描述） | "字段里有哪些真实值" |
| Qdrant | 字段的**语义召回**（别名匹配） | 同上 |
| ES（这一步） | 字段的**真实取值全文检索** | —— |

具体场景：用户问"**客户都分布在哪些国家**"。系统需要知道 `dim_customer.country` 这个字段里真实存了 `德国`、`荷兰`、`美国`、`中国香港`……这些值。

这些值既不是"字段名"（MySQL 存的是 `country` 这个列名，不是列里的值），也不是"别名"（Qdrant 存的是"国家"这个同义词，不是"德国"）。**只有 ES 存了"字段里真实有哪些值"。**

> 一句话：MySQL 回答"字段叫什么"，Qdrant 回答"字段还能怎么叫"，ES 回答"字段里具体有什么"。

---

## 三、分层：和 Qdrant 一样，没有 models/mappers

写 ES 和写 Qdrant 是同一类情形——ES 的 document 就是自由 JSON：

```
写 MySQL：ColumnInfo(实体) → Mapper → ORM模型 → SQL INSERT      （有 models+mappers）
写 Qdrant：ColumnInfo(实体) → asdict → payload → point           （无 models+mappers）
写 ES：    ValueInfo(实体)  → asdict → document → bulk           （无 models+mappers）
```

所以这次和 Qdrant 一样，只有：**Service（编排）+ Repository（建索引/写文档）+ 已有的 es_client**。新增了一个 `ValueInfo` 实体，作为"字段取值"这个概念的"标准箱子"。

---

## 四、文件清单（按调用顺序）

| 顺序 | 文件 | 层 | 动作 |
|---|---|---|---|
| 1 | `scripts/build_meta_knowledge.py` | 入口 | init es 客户端，注入 value_es_repo |
| 2 | `services/meta_knowledge_service.py` | 编排 | `_save_value_info_to_es()`：筛 sync=true → 取值 → 写 |
| 3 | `repositories/es/value_es_repository.py` | 仓储 | **新建**：`ensure_index()` + `index()` + `search()` |
| 4 | `entities/value_info.py` | 实体 | **新建**：字段取值的"标准箱子" |
| 5 | `clients/es_client.py` | 连接 | 已存在，提供 `es_client_manager.client` |

---

## 五、完整执行顺序（4 步，对号入座）

```
① 确保索引存在（第一次建，之后跳过）
   └─ value_es_repository.ensure_index()

② 从配置整理"字段 id → 是否同步"对照表
   └─ column2sync[字段id] = column.sync

③ 只对 sync=true 的字段，去 dw 库拿取值全集 → 构造 ValueInfo
   └─ get_column_values(表名, 字段名, 100000)
       每个取值 → ValueInfo(id="字段id.取值", value=取值, column_id=字段id)

④ 交给 ES 仓储分批写入
   └─ value_es_repository.index(value_infos)
```

---

## 六、逐层代码走读（关键代码 + 为什么）

### 6.1 实体层：`value_info.py`

```python
@dataclass
class ValueInfo:
    id: str        # "表名.字段名.取值"，如 "dim_customer.country.德国"
    value: str     # 真实取值，如 "德国"、"中国香港"
    column_id: str # 所属字段 id，如 "dim_customer.country"
```

**为什么 `id` 要拼上取值？** 因为同一个字段有多个取值，光用字段 id 无法区分"德国"和"荷兰"。`id = 字段id.取值` 让每个取值有全局唯一标识，同时这个 id 还复用成 ES 文档的 `_id`。

### 6.2 仓储层：`value_es_repository.py`

**建索引的 mappings（最关键的配置）：**

```python
index_mappings = {
    "dynamic": False,
    "properties": {
        "id":        {"type": "keyword"},
        "value":     {"type": "text", "analyzer": "ik_max_word", "search_analyzer": "ik_max_word"},
        "column_id": {"type": "keyword"},
    },
}
```

| 字段类型 | 作用 | 类比 |
|---|---|---|
| `keyword` | **不分词、整体精确匹配** | MySQL 的 `=` 精确查询 |
| `text` + `ik_max_word` | **中文分词 + 全文检索** | MySQL 的 `LIKE '%词%'`，但快万倍 |

**为什么 `value` 用 text+IK，`id`/`column_id` 用 keyword？**

- `value` 是要被"搜"的：用户搜"香港"，要能命中取值"中国香港"。IK 分词把"中国香港"拆成 `中国`/`香港`/`中国香港`，搜"香港"也能命中。
- `id`/`column_id` 是标识，只做精确匹配，不需要分词（分词反而会把 `dim_customer.country` 拆乱）。

**写入用 `bulk` + `_id`：**

```python
batch_operations.append({"index": {"_index": self.index_name, "_id": value_info.id}})
batch_operations.append(asdict(value_info))   # 数据本体
await self.client.bulk(operations=batch_operations)
```

`bulk` 的格式是**"操作描述 + 数据本体"交替出现**，一个 HTTP 请求写多条，性能最高。

**关键：`_id` 指定了文档 id → ES 的 `index` 是 upsert（重复写入会覆盖），天然幂等。** 这和 MySQL 的 `add_all`（纯 INSERT，重复会撞主键报错）不一样。

### 6.3 编排层：`_save_value_info_to_es`

```python
async def _save_value_info_to_es(self, meta_config, column_infos):
    await self.value_es_repository.ensure_index()          # ①

    column2sync = {}                                       # ② 整理 sync 开关
    for table in meta_config.tables:
        for column in table.columns:
            column2sync[f"{table.name}.{column.name}"] = column.sync

    value_infos = []
    for column_info in column_infos:
        if not column2sync[column_info.id]:
            continue                                       # sync=false 直接跳过
        values = await self.dw_mysql_repository.get_column_values(
            column_info.table_id, column_info.name, 100000  # ③ 拿全集
        )
        for value in values:
            value_infos.append(
                ValueInfo(id=f"{column_info.id}.{value}", value=value, column_id=column_info.id)
            )

    await self.value_es_repository.index(value_infos)      # ④
```

**两个关键点：**

1. **`sync` 开关决定同步哪些字段。** 不是所有字段都要同步取值——金额、日期这种每天都在变的字段，同步了没意义；只有维度枚举（国家、客户等级、产品类型）才值得同步。这个开关在 yaml 里显式配置。

2. **这里 `get_column_values(..., 100000)` 拿的是"全集"，不是元数据里那 10 条 `examples`。** 元数据只要"给人和模型看个大概"，所以抽 10 条就够；全文检索要"把字段里所有取值都搜得到"，所以要尽量全量。

### 6.4 入口脚本：注入 es 客户端

和 Qdrant 一模一样的套路：

```python
es_client_manager.init()                                        # init
value_es_repo = ValueESRepository(es_client_manager.client)     # 注入 client
service = MetaKnowledgeService(..., value_es_repo)              # 传给 service
await es_client_manager.close()                                 # 关闭
```

---

## 七、四个核心概念（重点看）

### 7.1 `sync` 开关：哪些字段值得同步

yaml 里每个字段有个 `sync: true/false`：

- `sync: true` 的（31 个）：维度枚举类，如 `dim_customer.country`、`dim_customer.customer_level`、`dim_product.product_type`、`fact_quotation.lost_reason`、`dim_date.year` 等。
- `sync: false` 的（其余 55 个）：主键、外键、度量、金额、日期 id 等，同步没意义或数据会爆炸。

> 判断标准：**这个字段的取值是不是"有限的、稳定的、用户会直接说出来的"。** 国家是（有限、稳定、会说），合同金额不是（无限、时刻变、不会说"123.45 元"）。

### 7.2 `keyword` vs `text`（全文检索的核心区别）

| | keyword | text |
|---|---|---|
| 分词 | 不分词，整体一个词 | 按 analyzer 分词 |
| 匹配 | 精确匹配（`= "德国"`） | 模糊匹配（搜"香港"命中"中国香港"） |
| 用途 | id、状态码、枚举 id | 人名、地址、描述、正文 |

`value` 字段要能被用户**部分匹配**，所以用 text；`id`/`column_id` 是标识，要精确，所以用 keyword。

### 7.3 `dynamic=False`：防止脏字段被自动创建

ES 默认"动态映射"：写入时遇到没定义的字段，会自动给它建一个字段。这很危险——拼错一个字段名，索引里就多了一个永远用不到的脏字段。

`dynamic=False` 关掉它：遇到没定义的字段时**忽略**（不索引、不报错，但原始数据仍存 _source，能取回却搜不到）。这样 schema 是受控的，脏字段不会污染索引。

> 注意：`dynamic=False` **不会报错**，只是忽略；要"直接报错拒绝写入"得用 `dynamic="strict"`。如果担心字段名拼错被静默忽略，用 `strict` 更严格。

### 7.4 幂等性：ES 覆盖 vs MySQL 报错

- **ES**：`index` + `_id` 是 upsert，重复构建会覆盖同一条取值记录，**可重复跑**。
- **MySQL**：`add_all` 是纯 INSERT，重复构建撞主键报 1062，**不可重复跑**（这就是上一轮要你清空 meta 库的原因）。

---

## 八、用真实数据走一遍

以 `dim_customer.country` 为例（sync: true）：

```
【dw 库真实取值】 get_column_values("dim_customer", "country", 100000)
  → ["德国", "荷兰", "美国", "中国香港", ...]

【构造成 ValueInfo】
  ValueInfo(id="dim_customer.country.德国", value="德国", column_id="dim_customer.country")
  ValueInfo(id="dim_customer.country.荷兰", value="荷兰", column_id="dim_customer.country")
  ...

【写入 ES（bulk，每批 20 条）】
  {"index": {"_index":"value_index", "_id":"dim_customer.country.德国"}}
  {"id":"dim_customer.country.德国", "value":"德国", "column_id":"dim_customer.country"}
  ...

【ES 内部分词】 value="中国香港" → IK → [中国, 香港, 中国香港]

【未来检索】
  用户问"有没有香港的客户" → 提取关键词"香港"
  → ES match 查询 value 字段 → 命中"中国香港"这条文档
  → 拿到 column_id=dim_customer.country → 知道要去哪个字段查国家
```

31 个 sync=true 字段都这样处理，最终 ES 的 `value_index` 里有 `所有同步字段的取值数之和` 条文档。

---

## 九、容易混淆 / 踩坑的点（汇总）

1. **IK 插件没装会报错**：mapping 里用了 `ik_max_word`，如果 ES 没装 `analysis-ik` 插件，`ensure_index` 会报 `analyzer [ik_max_word] not found`。本项目的 Dockerfile 已经装了，但换环境要留意。

2. **`value` 类型不是纯 str**：年份、月份这类数字取值也进来（`dim_date.year` 是 int），但 ES 的 text 字段会自动把数字转字符串索引，所以不报错。类型标注 `str` 是出于"全文检索的对象是文本"的语义考虑。

3. **重复运行：ES 幂等，MySQL 不幂等**：再跑一次脚本，ES 会覆盖（没事），但 MySQL 会撞主键报 1062。所以要么先清空 meta 库，要么只重跑 ES 那步。

4. **`index_name = "value_index"` 和配置里的 `es.index_name: data_agent` 无关**：前者是写死在本类的字段取值专用索引名；后者是模板预留的通用索引名，本链路没用到。说明配置里有些字段是给后续功能预留的，不是每个都要消费。

5. **`get_column_values(..., 100000)` 不是魔法数字**：是"尽量全量"的意思。真实业务里更严谨的做法是分页全量拉取，这里用大 limit 简化。

---

## 十、下一步（未完成的部分）

当前链路到"字段取值写入 ES"为止，表+字段这条主线已全部打通：

```
✅ 表 + 字段 → MySQL meta 库（结构化元数据）
✅ 字段 → Qdrant 向量库（语义召回）
✅ 字段取值 → ES（全文检索）
⬜ 指标 → MySQL + Qdrant（指标链路，15 个指标还没入库）
```

最后剩**指标链路**：

1. 新增实体/模型/映射：`metric_info`、`column_metric`。
2. `MetaMySQLRepository` 加 `save_metric_infos` / `save_column_metrics`。
3. 新增 `metric_qdrant_repository`。
4. Service 加 `_save_metrics_to_meta_db` + `_save_metric_info_to_qdrant`。
