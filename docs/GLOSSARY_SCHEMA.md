# 词库 / 题库的数据格式

> 两个目录都用 YAML，加载器在 `finradar/knowledge/glossary.py` 与 `qbank.py`。
> 加内容不需要改代码——加载器按 dataclass 的字段名过滤，**多写的字段会被忽略，
> 少写的字段用默认值**，但 `pytest -q` 会守住下面标"必备"的字段。

---

## 一、词库 `finradar/data/glossary/*.yaml`

按六个分类分文件，每个文件一个 `category`，词条写在 `terms` 下：

```yaml
category: 宏观与货币政策        # 文件级分类，词条没写 category 时继承这里
terms:
  - id: macro-013               # 必备，全局唯一（测试会查重）
    term: 买断式逆回购            # 必备，显示名（检索和热词统计都认这个）
    category: 宏观与货币政策       # 可省略，继承文件级
    aliases: [买断式回购]          # 别名，热词匹配时和 term 等价
    era: 2024                    # 何时出现（年份或"2024年10月"）
    plan: 十四五启用，十五五常态化   # 属于哪个五年规划阶段
    heat: 5                      # 必备，1-5；5 = 2026 秋招几乎必问（测试校验范围）
    institutions: [券商, 公募, 银行]  # 适用机构，取值限于 券商 / 公募 / 银行
    source: 中国人民银行公告〔2024〕第X号   # 出处：哪份文件、哪次会议
    definition: >-                # 必备，是什么
      央行买断债券、到期再卖回的公开市场操作工具……
    why_hot: >-                   # 为什么现在热 / 面试为什么问
      它解决了质押式回购的抵押品占用问题……
    exam_points:                  # 必备，考点清单（最容易被追问的点）
      - 与 7 天逆回购的差别：质押 vs 买断、期限 3/6 个月
      - 对银行质押券池与跨季资金面的影响
    interview_answer: >-          # 必备，60-90 秒口语化答法，写成能直接说出口的话
      买断式逆回购是 2024 年 10 月启用的，和 7 天逆回购最大的区别是……
    facts_snapshot:               # 可选，关键数字 + 截止时点（会过期，务必标 as_of）
      操作规模_亿元: 8000
      as_of: 2026-06-30
    followups:                    # 可选，追问及应答；建议至少 1 条
      - q: 为什么它对债市偏友好？
        a: 期限长、不占用质押券、到期回笼可预期……
    related: [公开市场操作, 逆回购, 中期借贷便利]   # 关联词，用于串知识链
```

字段（来自 `finradar/models.py::Term`）：

| 字段 | 类型 | 必备 | 说明 |
|---|---|---|---|
| `id` | str | ✓ | 唯一标识，测试查重 |
| `term` | str | ✓ | 词条名，热词统计按它计数 |
| `category` | str | ✓ | 六大分类之一 |
| `definition` | str | ✓ | 是什么 |
| `heat` | int | ✓ | 1-5，测试校验范围 |
| `exam_points` | list | ✓ | 考点清单，至少要有一条 |
| `interview_answer` | str | ✓ | 书面答案不算，要口语化 |
| `aliases` / `era` / `plan` / `institutions` / `source` / `why_hot` | | | 建议都填 |
| `facts_snapshot` | dict | | 数字类信息一律带 `as_of` |
| `followups` | list[{q,a}] | | 追问及应答 |
| `related` | list[str] | | 关联词 |

> 写口语化答法的经验：**先给结论、再给一句机制、最后落到自己投的岗位**。
> 长度控制在 250–350 字（念出来 60–90 秒），数字带截止时点。

---

## 二、题库 `finradar/data/questions/*.yaml`

按板块分文件，每题写在 `questions` 下：

```yaml
board: 固定收益与债券市场
questions:
  - id: fi-012                 # 必备，全局唯一
    qtype: single              # 必备，single | multi | short | case
    difficulty: 2              # 1-3
    institutions: [券商, 公募, 银行]
    tags: [逆回购, 资金面]
    question: 下列说法正确的是？   # 必备，题干
    options:                   # 选择题必备：选项以 A. B. C. D. 开头
      - A. 7 天逆回购是质押式
      - B. 买断式逆回购期限为 3 个月或 6 个月
    correct: [A, B]            # 选择题必备：字母必须出现在 options 里（测试校验）
    exam_point: 必备，这道题到底在考什么（写在答案之外的一句话）
    answer: >-                 # 必备，标准答案（可以比口语版长、更结构化）
      ……
    spoken: >-                 # 必备，面试时怎么说（口语化、60-90 秒）
      ……
    followups:                 # 可选，建议至少 1 条
      - q: 追问
        a: 应答
```

字段（来自 `finradar/models.py::Question`）：

| 字段 | 类型 | 必备 | 说明 |
|---|---|---|---|
| `id` | str | ✓ | 唯一标识 |
| `board` | str | ✓ | 板块名（文件级 `board` 可继承） |
| `qtype` | str | ✓ | `single` / `multi` / `short` / `case` |
| `question` | str | ✓ | 题干 |
| `answer` | str | ✓ | 标准答案 |
| `exam_point` | str | ✓ | 考点 |
| `spoken` | str | ✓ | 口语化表达 |
| `options` / `correct` | list | 选择题 ✓ | 单选题 `correct` 必须只有一项；正确项必须在选项中 |
| `difficulty` | int | | 1-3，默认 2 |
| `institutions` / `tags` | list | | 用于 `finradar quiz --institution` 过滤 |
| `followups` | list[{q,a}] | | 追问延伸 |

---

## 三、改完之后

```bash
cd ~/Downloads/china-fin-radar
pytest -q                 # 自动校验：id 唯一、必备字段、heat 范围、
                          # 选择题正确项在选项里、单选题只有一个答案
python scripts/build_docs.py       # 重新生成 docs/秋招金融知识手册.md
python scripts/build_artifact.py   # 重新生成 output/kb.html
```

写完想马上看效果：

```bash
finradar term 买断式逆回购        # 看词条渲染出来的样子
finradar show -n 2 --board 固定收益与债券市场
finradar quiz -n 3               # 交互试一遍
```

---

## 四、其他配置文件

| 文件 | 作用 | 改完做什么 |
|---|---|---|
| `config/keywords.yaml` | 政策打分规则（各类关键词与权重、噪音与例行事项降权） | `finradar rescore` |
| `config/sources.yaml` | 配置化 HTML 列表数据源（加政务站不用写代码） | `finradar doctor` |

`keywords.yaml` 的类别会被复用到两个地方：**打分**（`score` 为各类权重，可正可负）
和**热词榜**（只有 `monetary` / `capital_market` / `banking` / `opening` / `theme`
五类算"实质议题词"，`issuer`、`strong`、`noise`、`routine` 只参与打分）。
所以往负向类别里加词等于降权，往正向类别里加词会同时影响热词榜。
