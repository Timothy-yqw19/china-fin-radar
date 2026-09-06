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
    fetched_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def __post_init__(self) -> None:
        self.title = _norm_text(self.title)
        self.summary = _norm_text(self.summary)
        self.channel = _norm_text(self.channel)

    @property
    def uid(self) -> str:
        """去重主键: 优先 url, 否则 源+标题."""
        base = self.url.strip() or f"{self.source}:{self.title}"
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
