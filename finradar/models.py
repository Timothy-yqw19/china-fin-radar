"""数据模型."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any


def _norm_text(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"[\s　]+", " ", s)
    return s.strip()


@dataclass
class NewsItem:
    """一条新闻 / 政策文件."""

    source: str  # 数据源 id, 如 pbc / csrc / nfra / gov / em_flash
    source_name: str  # 中文名
    title: str
    url: str = ""
    published_at: str = ""  # ISO8601 或 YYYY-MM-DD HH:MM:SS
    summary: str = ""
    content: str = ""
    channel: str = ""  # 栏目, 如 "政策法规" "证监会要闻"
    doc_no: str = ""  # 文号, 如 国办发〔2025〕8号
    # ---- 分析结果 ----
    tags: list[str] = field(default_factory=list)
    hotwords: list[str] = field(default_factory=list)
    policy_score: float = 0.0  # 政策相关度 0-100
    impact: str = ""  # 影响链解读
    # 去重键: 默认按 url(没有 url 就按 源+标题)去重。
    # 热榜这类"每天都要留一份快照"的源会显式指定它(带上日期), 这样
    # 同一个话题连续上榜几天就能统计出"连续上榜天数"。
    dedupe_key: str = ""
    fetched_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def __post_init__(self) -> None:
        self.title = _norm_text(self.title)
        self.summary = _norm_text(self.summary)
        self.channel = _norm_text(self.channel)

    @property
    def uid(self) -> str:
        """去重主键: 优先 url, 否则 源+标题."""
        base = self.dedupe_key.strip() or self.url.strip() or f"{self.source}:{self.title}"
        return hashlib.md5(base.encode("utf-8")).hexdigest()

    @property
    def date(self) -> str:
        return (self.published_at or "")[:10]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["uid"] = self.uid
        return d


@dataclass
class Term:
    """金融热词 / 专业名词."""

    id: str
    term: str
    category: str
    definition: str = ""
    aliases: list[str] = field(default_factory=list)
    era: str = ""
    plan: str = ""
    heat: int = 3
    institutions: list[str] = field(default_factory=list)
    source: str = ""
    why_hot: str = ""
    exam_points: list[str] = field(default_factory=list)
    interview_answer: str = ""
    followups: list[dict] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    facts_snapshot: dict = field(default_factory=dict)

    @property
    def all_names(self) -> list[str]:
        return [self.term, *self.aliases]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Question:
    """一道题."""

    id: str
    board: str  # 板块: 宏观与货币政策 / 固定收益 / ...
    qtype: str  # single | multi | short | case
    question: str
    answer: str = ""
    options: list[str] = field(default_factory=list)
    correct: list[str] = field(default_factory=list)  # 选择题正确项, 如 ["A","C"]
    exam_point: str = ""  # 考点
    spoken: str = ""  # 面试口语化表达
    followups: list[dict] = field(default_factory=list)
    difficulty: int = 2  # 1-3
    institutions: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Insight:
    """专题洞察: 一个议题的脉络 + 现状 + 各主体可能的动作.

    和 Term(热词) 的分工:
      Term    回答"这个词是什么、考点是什么" —— 静态知识
      Insight 回答"这条路怎么走到今天的、接下来各方可能做什么" —— 动态判断

    展望部分一律写成"触发条件 → 可能动作 → 时间窗", 而不是无条件的预测,
    因为政策展望的价值在于说清"看到什么信号说明它要来了"。
    """

    id: str
    topic: str
    category: str = ""
    thesis: str = ""  # 一句话核心判断
    actors: list[str] = field(default_factory=list)  # 涉及的主体: 监管/公募/券商/银行/外资…
    keywords: list[str] = field(default_factory=list)  # 用来关联库里的新闻与政策
    timeline: list[dict] = field(default_factory=list)  # [{when, event, source}]
    snapshot: list[dict] = field(default_factory=list)  # [{label, value, as_of}]
    outlook: list[dict] = field(default_factory=list)  # [{actor, action, trigger, horizon}]
    watchlist: list[str] = field(default_factory=list)  # 观察指标/信号
    # 成篇的专题报道: {lead: 导语, sections: [{title, body}]} —— 正文是散文,
    # 渲染时会在导语后自动织入一段"数据支撑"(来自库内真实语料)
    feature: dict = field(default_factory=dict)
    interview_take: str = ""  # 60-90 秒口语化
    related_terms: list[str] = field(default_factory=list)  # 关联的热词

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
