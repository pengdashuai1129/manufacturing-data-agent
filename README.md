# manufacturing-data-agent

**制造业数据问答系统** —— 用中文问数据，系统自动生成 SQL 并执行，全程流式返回进度与结果。

> 一句话：让不懂 SQL 的业务人员，也能直接把数据"问"出来。
>
> 技术主线：**LangGraph 14 节点流水线 + 全链路配置驱动 + 行级数据权限 + 增量录入**。

## 目录

- [这个项目解决什么](#这个项目解决什么)
- [部署形态](#部署形态)
- [数据说明](#数据说明)
- [核心特性](#核心特性)
- [技术栈](#技术栈)
- [系统架构](#系统架构)
- [数据模型](#数据模型)
- [工作原理与14节点流水线](#工作原理与14节点流水线)
- [召回融合与重排](#召回融合与重排)
- [示例问答](#示例问答)
- [验收集与参考 SQL](#验收集与参考-sql)
- [账号角色与权限](#账号角色与权限)
- [增量录入](#增量录入)
- [已知边界](#已知边界)
- [目录结构](#目录结构)
- [快速开始](#快速开始)

## 这个项目解决什么

面向一家**制造企业**（管件管材 + 电热毛巾架/散热器）。选这个场景，是因为它把一条完整的业务链路摊开了，而不是只做一张表的查询：

```
业务部开发客户 → 报价 → 样品单 → 合同 → 生产指令 → 各车间工序 → 发货
     └── 接单漏斗 ──┘        └──── 交付表现 ────┘   └── 生产与质量 ──┘
```

对应老板真正纠结的两个问题：

| 问题 | 靠什么数据回答 |
|---|---|
| **订单能不能接到？** | `fact_quotation` —— 报价 → 中标漏斗，`lost_reason` 说明「为什么没接到」 |
| **订单来了产量跟不跟得上？** | `fact_production_process` —— 工序级的加工量与不良数 |

但**只会问数的系统在企业里上不了线**。所以除了 NL2SQL 本身，还补齐了两件工业界真正会卡住的事：

- **行级数据权限**：业务员问不出别人的业绩（不是靠页面藏按钮，是靠改写 SQL）
- **增量录入**：数据能一天天往里长、历史合同能改，而不是每次重造一整批

## 部署形态

**目标形态：装在一台机器上、给一个厂的内网用。**

```
        ┌─────────────────── 公司内网（局域网）───────────────────┐
        │                                                       │
        │   ① 一台机器：装 MySQL / ES / Qdrant / Ollama / 本项目    │
        │      管理员定期维护 —— 录新合同、改错单、备份数据         │
        │                                                       │
        │      服务监听 0.0.0.0:8000                             │
        │              ▲          ▲          ▲                  │
        │              │          │          │                  │
        │            老板      业务主管     业务员…               │
        │         （各自用自己的账号登录，看到的数据范围不同）        │
        └───────────────────────────────────────────────────────┘
```

| 环节 | 做法 |
|---|---|
| **一台机器** | MySQL / Elasticsearch / Qdrant 由 `docker compose` 起，Ollama 与本项目跑在本机进程，不依赖任何云服务（大模型 API 除外） |
| **内网访问** | 服务监听 `0.0.0.0:8000`，同事在浏览器打开 `http://<那台机器的内网IP>:8000` 即可，不用装客户端、不用发数据 |
| **分层账号** | 每人一个账号，可见范围由角色决定（见[账号角色与权限](#账号角色与权限)），**越权不是靠自觉，是靠改写 SQL 挡住** |
| **管理员维护** | 新合同、错单走「数据录入」页签或 CSV 批量导入，派生列由服务端重算，不用碰脚本、不用重跑流水线 |

> ⚠️ 这是**目标形态**，不是生产级部署：没有 HTTPS、没有反向代理、数据库账号也没做隔离。真要上线这几处都得补，已如实列在[已知边界](#已知边界)里。

## 数据说明

> **结构与流程取自真实业务，业务记录为模拟生成。**

**真实的部分**（决定了这个系统"像不像真的"）：

- 表结构、字段名与指标口径、单据编号规则、部门与岗位、客户档案、产品档案、工艺路线（机加工 → 制管 → 焊接 → 抛光｜喷塑 → 包装），全部对照真实的钉钉审批单与 ERP 合同评审单设计；
- 其中**十余份合同及其明细直接解析自真实合同评审单**，这部分金额是真实金额。

**模拟的部分**：

- 其余全部业务记录（另外三百多份合同及明细、全部报价单 / 样品单 / 生产指令 / 工序明细），以及所有日期、数量、金额、不良数，由脚本按**固定随机种子**生成——同样的种子永远产出同样的数字。

**不进仓库**：

- `data/`（CSV 与种子文件）与 `合同/`（公司原始单据）都在 `.gitignore` 里；
- 真实员工姓名与真实客户名只存在于本地文件中，本文档中的姓名一律以「业务员 A」这类代号出现。

> ⚠️ **下文所有数字都是这套模拟数据集的结果，不代表任何真实经营业绩**，仅用于演示系统的分析能力。

## 核心特性

| 特性 | 说明 |
|---|---|
| **自然语言 → SQL** | 输入中文问题，自动生成可直接执行的 MySQL 查询 |
| **多维召回** | 字段（Qdrant 向量）、取值（ES 全文）、指标（Qdrant 向量）三路并行召回 |
| **召回融合 + 重排** | 每个关键词各召回一次，用 **RRF 按名次融合**（而不是「先到先得」去重），再用 cross-encoder 重排候选。离线验收集上 MRR **0.434 → 0.781**，见[召回融合与重排](#召回融合与重排) |
| **指标口径内置** | 15 个业务指标带口径说明与同义词别名，模型不会把「合同数」（笔数）和「合同数量」（件数）混为一谈 |
| **SQL 自校验 + 自修正** | 先过 `EXPLAIN`，再做语义检查（扫恒真 `ON` 之类），失败自动重写 |
| **账号 + 角色分级 + 行级数据权限** | 业务员问「各业务员成交排名」会被**拒绝**，而不是把自己那一行伪装成「第 1 名」；权限条件在 LLM 收工之后注入，模型改不到 |
| **增量录入** | 网页表单 / CSV 批量导入新增与修改合同，派生列由服务端重算，与 `step2` 共用同一批不变量断言 |
| **流式输出** | SSE 实时推送 14 个节点的执行进度、SQL 和最终结果 |
| **全栈闭环** | 从元数据知识库构建到前端问数界面，数据层 / 智能体层 / 服务层 / 前端完整打通 |

## 技术栈

| 层 | 技术 |
|---|---|
| 智能体编排 | LangGraph |
| 大模型 | DeepSeek（deepseek-chat，temperature=0） |
| 向量检索 | Qdrant |
| 全文检索 | Elasticsearch（IK 分词） |
| Embedding | Ollama + bge-m3 |
| 重排（可选） | TEI（text-embeddings-inference）+ bge-reranker-v2-m3 |
| 数据库 | MySQL（元数据库 `meta` + 数仓模拟库 `dw` + 应用库 `app`） |
| 后端接口 | FastAPI + SSE（Server-Sent Events） |
| 前端 | 原生 HTML / JavaScript（单文件，无构建工具） |

## 系统架构

```
用户提问（自然语言）
   │
   ▼
┌─────────────────── 智能体层（LangGraph，14 节点） ───────────────────┐
│ extract_keywords ──► 三路并行召回 ──► merge ──► rerank ──► filter_table│
│   （抽取关键词）      recall_column (Qdrant)  （RRF 融合   filter_metric│
│                      recall_value  (ES)        + 重排）   （LLM 过滤）│
│                      recall_metric (Qdrant)                         │
│                                                                     │
│ add_extra_context ──► generate_sql ──► validate_sql ─┐               │
│   （补日期/版本）      （生成 SQL）     （EXPLAIN 校验）│              │
│                                       （校验失败）──► correct_sql ──┐│
│                                                                    ││
│                        apply_row_policy ◄──────────────────────────┘│
│                        （按角色注入行级权限，LLM 到此已收工）  ◄──────┘
│                              │                                      │
│                              ▼                                      │
│                           run_sql                                   │
└─────────────────────────────────────────────────────────────────────┘
   │
   ▼
FastAPI（SSE 流式） ──► 前端聊天界面
```

**全链路配置驱动**：14 个节点里没有一处硬编码业务词。表结构、字段、指标、同义词全部写在 `conf/meta_config.yaml`，构建时灌进 MySQL（`meta` 库）+ Qdrant（字段/指标向量）+ ES（取值全文索引）。

> **换一套业务只需要改这个 YAML 加数据，代码一行不动。**

## 数据模型

星型模型，**11 张表 / 86 字段 / 15 指标**：

| 表 | 说明 |
|---|---|
| `fact_quotation` | 报价单 —— 接单漏斗前端，`lost_reason` 回答「为什么没接到单」 |
| `fact_sample` | 样品单 —— 大部分客户先打样再下单 |
| `fact_order` / `fact_order_item` | 合同头 / 合同明细 —— 拆表后「合同数」（数头）与「合同数量」（SUM 明细）天然分开 |
| `fact_production` | 生产指令 —— 公司口径的产量 |
| `fact_production_process` | 工序明细 —— 车间口径的加工量与不良（工序级） |
| `dim_customer` / `dim_product` / `dim_employee` / `dim_workshop` / `dim_date` | 客户 / 产品 / 员工 / 车间 / 日期维度 |

产品与车间是**多对多**：一个产品要过 5 道工序（机加工 → 制管 → 焊接 → 抛光｜喷塑 → 包装），第 4 道按材质二选一（不锈钢走抛光、碳钢走喷塑）——这也是工序必须单独建表的原因。

### 几个刻意的建模取舍

- **`delay_days` / `is_on_time` 做成物理列，而不是查询时算**。若让「交付及时率」依赖 `DATEDIFF(实际发货, 承诺交期)`，模型很容易写成 `COUNT(a <= b)` 或漏掉 `NULL` 行。做成派生列后口径变平凡：`平均延期天数 = AVG(delay_days)`、`交付及时率 = 准时单数 / 已发货单数`，未发货行的 `delay_days` 是 `NULL`，`AVG` 自动跳过。
- **`trade_type`（内销/出口）在客户维表上冗余了一份**。它本可由 `country` 派生，但保留它能让生成的 SQL 少出错——`WHERE trade_type = '出口'` 一行搞定，否则模型要自己列全二十多个国家和港澳台，漏一个就错。**维表适度冗余是特性。**
- **`contract_type` / `customer_level` 是派生字段**。「按客户等级看合同金额」这类问法会构成同义反复——这是真实数仓里存在的口径陷阱，保留它是有意为之。

## 工作原理与14节点流水线

| # | 节点 | 做什么 |
|---|---|---|
| 1 | `extract_keywords` | 抽取关键词并扩展同义词（"销售额"→"合同金额"，"订单量"→"合同数量"） |
| 2-4 | `recall_column` / `recall_value` / `recall_metric` | **三路并行召回**：字段（Qdrant 向量）、取值（ES 全文）、指标（Qdrant 向量）。每个关键词各召回一次，再按名次做 RRF 融合 |
| 5 | `merge_retrieved_info` | 合并三路结果，补齐指标依赖的字段与各表主键 |
| 6 | `rerank_retrieved_info` | **重排候选**：cross-encoder 给每个候选字段打分（未启用时走确定性的 RRF+词面混合打分），主键/外键与指标依赖列永远排在前面 |
| 7-8 | `filter_table` / `filter_metric` | LLM 过滤掉无关的表结构与指标（并行） |
| 9 | `add_extra_context` | 补充当前日期、数据库版本等上下文 |
| 10 | `generate_sql` | 基于召回的表结构和指标生成 SQL |
| 11 | `validate_sql` | `EXPLAIN` 预解析 + **语义校验**（恒真 `ON` 等） |
| 12 | `correct_sql` | 校验失败时让 LLM 重写**一次**（修正结果不再回炉复校验，见下） |
| 13 | `apply_row_policy` | 按当前用户角色注入行级权限条件，或直接拒绝越权问题。**它是 `run_sql` 唯一的前驱**，正常与修正两条分支都要经过它 |
| 14 | `run_sql` | 执行并返回结果 |

## 召回融合与重排

### 为什么需要融合

`extract_keywords` 抽出若干关键词、LLM 再扩展一轮同义词，然后**每个关键词各查一次向量库**。同一份字段会被多个关键词命中，于是要合并。原来的合并是「先到先得」：

```python
for keyword in keywords:
    for info in await repo.search(embed(keyword)):
        if info.id not in column_info_map:     # 谁先被遍历到，谁就排在前面
            column_info_map[info.id] = info
```

这里有两个问题。一是顺序取决于 `keywords` 的迭代顺序，而它是个 `set` —— set 的迭代顺序受 `PYTHONHASHSEED` 影响，**同一句问题换个进程召回顺序就变了**。二是它把检索返回的相似度名次整个丢掉，只留下「有没有」。

改成 **RRF**（Reciprocal Rank Fusion）：每一路的返回本身就是按相似度降序的，把名次累加，`score = Σ 1/(60 + rank)`。它只用名次、不用分数，所以向量检索的余弦分与全文检索的 BM25 分可以直接混在一起比，不必先做分数归一化。顺带把 `keywords` 排序，召回结果从此可复现。

### 重排

融合解决「同一路里谁靠前」，重排解决「跨路之后谁最该先被模型看到」。`rerank_retrieved_info` 把每个候选字段拼成 `[表名.字段名] [描述] [别名] [样例值]` 交给 cross-encoder 打分。

**排序不等于裁剪，而裁剪永远不碰骨架列**：主键 / 外键是 JOIN 的骨架，指标依赖列是口径的落点，两者重排后恒排最前，即使开了「每表最多 N 列」也不会被裁掉。加上下游 `filter_table` 本来就有「补回指标必需字段」的兜底，这个节点最坏的结果是「没帮上忙」，不是「改错了」。

### 实测

`app/scripts/eval_recall.py` 把这件事变成一个数：12 道人工标注了答案字段的业务问题，每题看答案字段排在第几位，算 MRR 与 Top-K 命中率。86 个字段的定义本来就在 `conf/meta_config.yaml` 里，脚本用 Ollama 的 bge-m3 现算向量、进程内做一次暴力余弦排序 —— 得到的就是 Qdrant 那句 `search()` 会返回的东西，所以**不用起 MySQL / Qdrant / ES / Docker**。

```
                                 MRR↑   Top-1   Top-5
旧（先到先得）                    0.434     4       6
RRF 融合                         0.733     7      11
RRF + 纯词面重排                  0.728     7      12
RRF + 混合重排                    0.781     8      12
```

两点值得说：

- **主要收益来自融合**（0.434 → 0.733），且不花任何额外算力 —— 只是把原本被扔掉的名次用起来。
- **纯词面重排反而略降**（0.728 < 0.733），因为它把 RRF 算出来的共识整个覆盖掉了。所以兜底改成**混合**：归一化后的 RRF 分与词面分各占一半（0.781）。**换掉一个信号之前，先确认它不是唯一有用的那个。**

### 启用 cross-encoder

默认关着（`conf/app_config.yaml` 里 `rerank.enable: false`）。开启：

```bash
docker compose -f docker/docker-compose.yaml up -d reranker   # TEI + bge-reranker-v2-m3
# 再把 conf/app_config.yaml 的 rerank.enable 改成 true
```

选 HTTP 服务而不是 `import torch`：cross-encoder 要跑 BERT 级前向计算，Python 侧引 torch 在 Windows 上 2GB 起步，而项目其余部分只用向量检索，没理由为一步重排把整个深度学习栈拖进来。TEI 起来就是标准的 `POST /rerank`，**Python 侧零新增依赖**。

服务没起 / 超时 / 返回格式不认识，`RerankClient.score()` 一律返回 `None` 让调用方兜底，**不抛异常** —— 重排是优化项，不该有能力把整条问数链路搞挂。

> **这个评测的边界**：上表只覆盖**字段召回的排序**（取值 / 指标两路要靠 ES 与 Qdrant 里的数据，离线拿不到）；cross-encoder 那条路径**没有实测** —— 0.781 是「RRF + 词面混合」这个**兜底路径**的成绩，不是 cross-encoder 的。

## 示例问答

**问**：今年各业务员的中标率是多少？

**Agent 实际生成的 SQL**（原文照录，未经修改）：

```sql
SELECT
    e.employee_name AS 业务员姓名,
    COUNT(CASE WHEN q.quote_status = '中标' THEN 1 END) AS 中标单数,
    COUNT(CASE WHEN q.quote_status IN ('中标', '落标') THEN 1 END) AS 已结案报价单数,
    ROUND(
        COUNT(CASE WHEN q.quote_status = '中标' THEN 1 END) * 1.0
        / NULLIF(COUNT(CASE WHEN q.quote_status IN ('中标', '落标') THEN 1 END), 0),
        4
    ) AS 中标率
FROM fact_quotation q
JOIN dim_employee e ON q.employee_id = e.employee_id
JOIN dim_date d ON q.quote_date_id = d.date_id
WHERE d.year = 2026
  AND e.employee_role = '业务员'
GROUP BY e.employee_id, e.employee_name
ORDER BY 中标率 DESC;
```

**查询结果**（姓名已匿名化）：

| 业务员 | 中标单数 | 已结案报价单数 | 中标率 |
|---|---|---|---|
| 业务员 A | 23 | 38 | 0.6053 |
| 业务员 B | 26 | 45 | 0.5778 |
| 业务员 C | 28 | 50 | 0.5600 |
| 业务员 D | 35 | 65 | 0.5385 |
| 业务员 E | 27 | 52 | 0.5192 |
| 业务员 F | 33 | 65 | 0.5077 |

> 这条 SQL **一次生成即通过 `EXPLAIN` 与语义校验，没有触发修正分支**。表里合计（中标 172 ÷ 已结案 315 = 54.6%）与下文参考 SQL 第 ① 条同源，可以互相校验。

值得一提的是，模型自己处理掉了两个口径难点：

- **分母只算已结案**（`quote_status IN ('中标','落标')`，不含还在跟的"跟进中"）——没谈完的单不该算作失败；
- **人员按角色过滤**（`e.employee_role = '业务员'`）——`dim_employee` 里既有业务员也有销售内勤，两者都会出现在 `employee_id` 上。

这两条**没有写死在提示词里**，而是写在指标「中标率」的 description 中（见 `conf/meta_config.yaml`）。它在建库阶段被向量化，召回时随指标一起进入上下文——**口径是随元数据走的，不是随提示词走的**。另外 `NULLIF(..., 0)` 防除零是模型自发加的，不在任何指引里。

## 验收集与参考 SQL

下面三条是**手写并逐条执行过**的参考查询，用来验收 Agent 的输出——不看它"说得通不通顺"，而是**拿数字对齐**。复现方式见 `app/scripts/step2_load_fact_to_mysql.py` 的 `REFERENCE_SQL`。

### ① 中标率逐年改善（口径：中标 ÷ 已结案）

```sql
SELECT ROUND(100 * SUM(quote_status = '中标') / COUNT(*), 1) AS 中标率
FROM fact_quotation
WHERE quote_status IN ('中标', '落标')       -- 不含"跟进中"
  AND quote_date_id >= :start AND quote_date_id < :end
```

| 期间 | 中标率(%) |
|---|---|
| 2025 Q1 | 41.0 |
| 2026 Q3 | 63.3 |

逐季看是一条稳步向上的曲线（%）：Q1 41.0 → Q2 36.7 → Q3 43.1 → Q4 43.0（2025）→ Q1 51.0 → Q2 56.0 → Q3 63.3（2026）。2026 Q4 只有 9 天，样本不足，不作数。

数字背后是**刻意造出的一条业务趋势**：合同数按月基本持平（分子稳定），报价数逐年递减（分母在缩）——以前撒网多成交少，近一年开始挑客户。若报价也按同样节奏摊，分子分母同步放大，中标率会变成一条平线，趋势就没了。落标原因也印证了这一点：2025 Q1 的 92 单落标里，`报价偏高 50 单 / 客户转单 16 单 / 交期太长 12 单 / 产品不符 9 单 / 客户预算取消 5 单`——「报价偏高」一家占了 54%，远高于其他季度。

### ② 产量 ≠ 车间加工量（口径陷阱，两者不能相加）

```sql
SELECT (SELECT SUM(output_quantity)  FROM fact_production)         AS 公司产量,
       (SELECT SUM(process_output)   FROM fact_production_process) AS 车间加工量合计,
       (SELECT SUM(process_output)   FROM fact_production_process
         WHERE workshop_id = 'W05')                                AS 仅喷塑车间
```

| 公司产量 | 车间加工量合计 | 仅喷塑车间 |
|---|---|---|
| 367285 | 1903286 | 57262 |

一个产品过 5 个车间，工序行就把同一件东西重复计了 5 次，所以工序表合计约是公司产量的 5 倍。

> **公司总产量看 `fact_production`；车间加工量看工序表，且必须按车间分组——跨车间求和无意义。**

### ③ 各车间不良率（工序级口径）

```sql
SELECT w.workshop_name,
       ROUND(100 * SUM(p.defect_quantity) / SUM(p.process_output), 2) AS 不良率
FROM fact_production_process p
JOIN dim_workshop w ON p.workshop_id = w.workshop_id
GROUP BY w.workshop_name
ORDER BY 不良率 DESC
```

| 车间 | 不良率(%) |
|---|---|
| 喷塑 | 6.85 |
| 焊接 | 1.68 |
| 制管 | 1.43 |
| 抛光 | 1.19 |
| 机加工 | 0.95 |
| 包装 | 0.48 |

不良是**按工序发生**的，所以 `defect_quantity` 建在工序表上，「不良率」的口径是 `SUM(工序不良数) / SUM(工序加工量)`，不是成品终检不良率。喷塑显著偏高（是第二名的四倍多），这条能直接对着「哪道工序最该整改」讲。

## 账号角色与权限

### 角色矩阵

| 角色 | 数据范围 | 查询 | 录入/修改 | 管账号 |
|---|---|---|---|---|
| 管理员 `admin` | 全部 | ✅ | ✅ | ✅ |
| 老板 `boss` | 全部 | ✅ | ❌ | ❌ |
| 业务主管 `sales_manager` | 本部门 | ✅ | ❌ | ❌ |
| 业务员 `salesman` | **只有自己名下的单** | ✅ | ❌ | ❌ |

**行级权限解决的是「问数工具最容易泄露数据的那条路」**：能挡住「谁能打开哪个页面」的权限模型，挡不住业务员在输入框里问一句「今年各业务员的成交额排名」。所以过滤必须发生在 **SQL 上**，不是在页面上。

### 实现：解析 SQL、注入条件、失败即拒绝

`app/services/row_policy.py` 是一个**纯函数模块**（可单测、不碰数据库），用 sqlglot 解析 SQL 后在 `WHERE` 里注入归属条件。

**受控的是全部 6 张事实表**——只挡住 `fact_order` 是不够的：`fact_order_item` 没有 `employee_id`，但指标「合同金额」正是 `SUM(fact_order_item.amount)`，业务员直接查明细表就能拿到全公司营收。

| 受控表 | 归属路径 |
|---|---|
| `fact_order` / `fact_quotation` / `fact_sample` | 直接：`employee_id` |
| `fact_order_item` / `fact_production` | 1 跳：`order_id → fact_order` |
| `fact_production_process` | **2 跳**：`production_id → fact_production → fact_order` |

**判定规则是 fail-closed 的**——`row_policy.py` 里每一个「说不清」的分支都指向拒绝，而不是放行：

1. sqlglot 解析失败 → **拒绝**（绝不退回执行未解析的 SQL）
2. 受控表出现在子查询 / CTE / UNION 分支里，或别名不唯一解决不了 → **拒绝**
3. 受控表在 `LEFT JOIN` 的被关联侧 → **拒绝**（条件得进 `ON`，塞进 `WHERE` 会把 LEFT 退化成 INNER、静默丢行）
4. 通过 → 按上表注入条件，改写后的 SQL 再跑一次 `EXPLAIN` 兜底

> 拒绝的失败模式是**一条看得见的报错**，放行的失败模式是**静默的数据泄露**。安全边界上只能选前者。

### 跨人问题：按 SQL 结构拒绝

纯粹的注入在这里不够用。业务员问「各业务员金额排名」，注入 `employee_id = '我'` 之后返回的是**自己这一行、名次第 1**——语法对、数字对、**意思是错的**，比报错危险得多。

所以加一条结构性判定：**业务员**的 SQL 若在 `SELECT` 或 `GROUP BY` 里引用了员工身份列（`dim_employee.employee_name` / `employee_id`，或受控表自身的 `employee_id`），判为跨人问题 → 拒绝，理由写成人话。

**业务主管不套用这条**——主管本来就该看本部门全员的对比，按部门过滤后返回团队排名才是正确语义。

### 权限节点为什么放在 LLM 的下游

`apply_row_policy` 是 `run_sql` 的**唯一前驱**，这个位置是刻意选的。

图上有一条 `correct_sql → run_sql` 的直连边。如果把权限节点放在 `generate_sql` 和 `validate_sql` 之间，只要 SQL 被重写过一次，权限条件就会被 LLM 洗掉——而语义校验实测「四次里漏一次」，意味着**修正分支是常态流量，不是边缘情况**。最坏的地方在于它**不报错**：业务员拿到的还是数字，只是全公司的数字。

放进 LLM 下游之后，这件事在结构上不可能发生：模型已经收工，节点恰好执行一次，不需要幂等保护、不需要重试计数、不会死循环。

> 设计原则是「只要不绕过 `run_sql`，就绕不过权限」——而不是「每加一个 LLM 节点，就重新检查一遍权限还在不在」。

### 会话与日志

服务端 session 表 + HttpOnly Cookie，用标准库 `hashlib.pbkdf2_hmac` / `secrets.token_urlsafe` 实现，**零新增依赖**。

不用 JWT 的理由：内网单机工具，服务端 session 能立刻踢人下线，也不必处理刷新和吊销。session 落 **MySQL 表**而不是进程内 dict——`uvicorn --reload` 一存盘就重启，dict 会把所有人踢下线。

两张审计表（都在 `app` 库）：

| 表 | 记什么 |
|---|---|
| `query_log` | 每次问数的**改写后** SQL，不是模型原始产物——审计要能复现「他实际看到了什么」 |
| `data_change_log` | 每次录入的 before/after JSON，回答「这单是谁改的、改之前长什么样」 |

## 增量录入

新增 / 修改一份合同走 `app/services/fact_write_service.py`，入口是 `POST /api/orders`、`PUT /api/orders/{id}`、`POST /api/orders/import`（CSV 批量），页面在「数据录入」页签（仅管理员可见）。

### 增量写入 vs step2 的全量重载

这是**两个不同的操作**，各有各的语义：

| 操作 | 语义 | 用什么 |
|---|---|---|
| **重建夹具** | CSV 是完整快照，每次替换一切 | `step2` 保持 `DELETE` + `REPLACE INTO` 不动 |
| **增量录入** | PK 已知，意图明确（新增或更新某一单） | 运行时写入服务 |

`step2` 的 `DELETE` 全表是刻意的——它解决的是「种子变了、单号前缀变了，旧行残留」的问题。**旧行残留不会报错，只会让查询结果悄悄偏掉**，而 `ON DUPLICATE KEY UPDATE` **表达不了「这一行不该存在了」**，没有任何 upsert 写法能补上这个语义。

所以这里**不引入 `--full` 开关**，而是让两个操作各归其位、共用同一批不变量断言（`app/services/data_quality_service.py`）。

### 子表按父键整块替换

`fact_order_item.item_id` = `<order_id>-NN`、`fact_production.production_id` = `<order_id>-NN` 都是**从父键派生的**。改一份合同删掉一行明细，旧 `item_id` 就成了孤儿——而**两个库一条外键都没有**，没有任何东西会拦住它。

所以子表是**按父键整块替换**的：先删该 order 的全部明细 / 生产 / 工序，再整批重插，全在一个事务里，引用完整性由写入服务自己承担。（`delete_production` 必须先删工序再删生产指令。）

### 派生列由服务端重算，不让前端传

表单只收「数量 + 单价」，`amount`（金额）和 `total_weight`（只重）由服务端算——这正是数仓里「派生列」该有的样子。`app/services/fact_derive_service.py` 把派生字段分成三类，**这个分类决定了「能不能重算」**：

| 类 | 字段 | 能否重算 |
|---|---|---|
| **A 可复现** | `amount` / `total_weight` / `contract_type` / `delay_days` / `is_on_time` | ✅ 已反向验证逐行与库里一致 |
| **B 依赖 `as_of`** | `order_status` | ✅ 但必须显式传 `as_of`，**不能用 `date.today()`**（会整体漂一天，反向验证会在*正确的代码*上报错） |
| **C 随机且从未落库** | 生产周期的 `_lead`、随机的延期原因文本、工序日期偏移 | ❌ **不可再生**——只能保留或原样带回，绝不能重新生成 |

改「承诺交期」时的级联策略：顺序性派生字段重算；生产与工序日期按**相同 delta 平移**（保住「交期 − 计划完工」这个可恢复的偏移量），再重算它们自己的延期字段。

> ⚠️ 平移时**既有工序行必须读出来带着走**——子表是按父键整块替换的，不带回来就等于把该单的工序明细静默删光，而工序明细正是「车间加工量 / 不良率」那几道题的唯一数据源。

## 已知边界

这些是**有意识划在范围外**的，写出来是为了说明边界在哪：

| 边界 | 说明 |
|---|---|
| **主管的可见范围 = 全部业务员** | 数据模型里业务部只有一个部门，没有「小组」这一层。这是模型边界，不是实现偷懒。 |
| **销售内勤角色不实现** | 真实单据里创建人是内勤，但 ERP 没把这个字段落库，`dim_employee` 里那 4 名内勤没有任何事实表引用它们。要做到「自己经手创建的单」必须新增 `creator_id` + 重造数据 + 重建知识库，评估后不做。 |
| **`dim_date` 只到 2026-12-31** | 2027 年之后再问「今年」，JOIN 会静默丢行。录入侧已经用校验挡住越界日期，但日历本身不自动增长。 |
| **派生字段重算是不闭合的** | A 类能逐行重算验证，B 类要求显式传 `as_of`，C 类（随机的生产周期与延期原因）不可再生——只能保留或清空。改单的级联因此只覆盖顺序性字段 + 日期平移，**不承诺「从库里反推出一切」**。 |
| **权限是应用层的** | `dw` 用的还是同一个 MySQL 账号，绕过应用直连数据库不受限制。生产环境应该用数据库账号做隔离，作品集范围内不做。 |
| **数据是模拟的** | 表结构与业务口径来自真实业务，业务记录由脚本生成（见[数据说明](#数据说明)）。文档里出现的数字**不代表真实经营情况**。 |

## 目录结构

```
manufacturing-data-agent/
├── app/
│   ├── agent/          # LangGraph 智能体（state / graph / context / ranking / 14 个 nodes）
│   ├── api/            # FastAPI 接口层（router / schema / dependencies / lifespan）
│   ├── services/       # 业务编排层（query_service / row_policy / fact_write / fact_derive / data_quality）
│   ├── repositories/   # 数据访问层（MySQL / Qdrant / ES）
│   ├── clients/        # 外部服务客户端（6 个全局单例）
│   ├── entities/       # 业务实体（纯 dataclass，不依赖框架）
│   ├── models/         # SQLAlchemy ORM 模型
│   └── scripts/        # 造数 / 入库 / 建账号 / 知识库构建 / 召回评测 / 连通性测试等脚本
├── conf/               # 配置文件（meta_config.yaml —— 全链路元数据源头）
├── docker/             # Docker Compose（MySQL / ES / Kibana / Qdrant / reranker）+ 建表 SQL
├── docs/               # 召回 / 向量化 / 索引构建等子流程的设计说明
├── prompts/            # 提示词模板
├── static/             # 前端页面（login.html / index.html）
├── tests/              # 单元测试（`python -m unittest discover -s tests -t .`）
├── main.py             # FastAPI 应用入口
└── pyproject.toml      # 依赖管理（uv）
```

## 快速开始

### 前置依赖

1. **Docker Desktop**（提供 MySQL / Elasticsearch / Qdrant）
2. **Ollama**（提供 bge-m3 向量模型）
3. **DeepSeek API Key**（[platform.deepseek.com](https://platform.deepseek.com) 申请）
4. **Python 3.10+** 和 [uv](https://github.com/astral-sh/uv)

### 启动步骤

```bash
# 1. 克隆仓库
git clone https://github.com/pengdashuai1129/manufacturing-data-agent.git
cd manufacturing-data-agent

# 2. 配置环境变量
cp .env.example .env
#    编辑 .env，把 DEEPSEEK_API_KEY 换成你的真实 Key

# 3. 启动基础设施（MySQL / ES / Qdrant）
docker compose -f docker/docker-compose.yaml up -d

# 4. 拉取 embedding 模型
ollama pull bge-m3

# 5. 安装依赖
uv sync

# 6. 建立数仓表结构（11 张表 + dim_date）
docker exec -i mysql mysql -uroot -pp123456 < docker/mysql/dw.sql

# 7. 造数并入库（生成 CSV → 跑不变量断言 → REPLACE INTO）
uv run python -m app.scripts.step1_generate_fact_csv
uv run python -m app.scripts.step2_load_fact_to_mysql   # 跑完会打印上文三条参考 SQL 及结果

# 8. 构建元数据知识库（写入 MySQL meta / Qdrant / ES）
uv run python -m app.scripts.build_meta_knowledge -c conf/meta_config.yaml

# 9. 建应用库（账号 / 会话 / 审计日志三张表）
#    ⚠️ 这一步必须手动跑：docker-entrypoint-initdb.d 只在数据卷为空时执行，
#       而跑到这里时卷早就有数据了，不会自动建
docker exec -i mysql mysql -uroot -pp123456 < docker/mysql/app.sql

# 10. 建初始账号（业务员账号从 dim_employee 读 employee_id 绑定）
uv run python -m app.scripts.seed_users

# 11. 启动服务
uv run uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

浏览器打开 **http://localhost:8000/**，用第 10 步建出来的账号登录（默认口令 `Demo@2026`，可用 `--password` 改），然后在输入框里问数；管理员还会多出一个「数据录入」页签。

要让内网同事访问，把第 11 步的 `--host` 换成 `0.0.0.0`，并放行防火墙 8000 端口（`allow_firewall_8000.bat` 已备好，需管理员权限运行；`start_server.bat` 是一键启动脚本）。

> 📌 **本仓库不含任何业务数据，也不含生成数据所需的种子文件。**
>
> `data/` 和 `合同/` 都在 `.gitignore` 里。第 7 步的 `step1` 从一份**本地种子文件** `data/real_seed.json` 出发，链式生成全部维度与事实数据。该文件由作者本地的真实单据（合同评审单 / 生产指令 PDF）解析而来，**含真实客户与员工信息，不能进仓库**。
>
> 因此 clone 这个仓库能拿到的是：完整表结构（`docker/mysql/dw.sql`）、全部元数据与指标口径（`conf/meta_config.yaml`）、以及建库 / 召回 / 生成 SQL 的全套代码；但**跑不出本文档里的这些数字**，除非你自备一份同结构的种子文件。
>
> 种子是纯 JSON，结构如下（只有字段名，无任何数据）：
>
> | key | 每条字段 |
> |---|---|
> | `customers` | `customer_id`, `customer_name`, `country`, `continent`, `trade_type` |
> | `products` | `product_id`, `product_name`, `product_type`, `heating_type`, `material`, `spec`, `unit_weight` |
> | `employees` | `employee_id`, `employee_name`, `department`, `employee_role` |
> | `orders` | `order_id`, `customer_name`, `employee_name`, `order_date`, `ship_date`, `currency`, `contract_type` |
> | `order_items` | `order_id`, `product_id`, `quantity`, `total_weight` |
>
> 换成你自己的数据后，第 6~10 步即可跑通；其余记录（报价 / 样品 / 生产 / 工序）由脚本按固定随机种子生成，**同样的种子永远产出同样的数字**。

## License

仅供学习与作品展示使用。
