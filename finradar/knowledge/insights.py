"""专题洞察库的加载与检索.

数据在 finradar/data/insights/*.yaml, 结构与字段见 docs/GLOSSARY_SCHEMA.md。
"""

from __future__ import annotations

import functools
from pathlib import Path

import yaml

from ..models import Insight
from ..utils import DATA_DIR

INSIGHT_DIR = DATA_DIR / "insights"


@functools.lru_cache(maxsize=1)
def load_insights(directory: str | None = None) -> tuple[Insight, ...]:
    d = Path(directory) if directory else INSIGHT_DIR
    out: list[Insight] = []
    allowed = Insight.__dataclass_fields__.keys()  # type: ignore[attr-defined]
    for f in sorted(d.glob("*.yaml")):
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        cat = doc.get("category", f.stem)
        for raw in doc.get("insights") or []:
            raw.setdefault("category", cat)
            out.append(Insight(**{k: v for k, v in raw.items() if k in allowed}))
    return tuple(out)


def get(name: str) -> Insight | None:
    """按 id / 专题名 / 关键词模糊查找, 大小写不敏感."""

    def norm(s: str) -> str:
        return (s or "").lower().replace(" ", "").replace("（", "(").replace("）", ")")

    q = norm(name)
    if not q:
        return None
    items = load_insights()
    for it in items:  # 精确优先
        if q in (norm(it.id), norm(it.topic)):
            return it
    for it in items:
        blob = norm(" ".join([it.id, it.topic, it.thesis, *it.keywords, *it.related_terms]))
        if q in blob:
            return it
    return None


def search(q: str = "", actor: str | None = None, category: str | None = None) -> list[Insight]:
    res = []
    for it in load_insights():
        if category and category not in it.category:
            continue
        if actor and actor not in it.actors:
            continue
        if q:
            blob = " ".join([it.topic, it.thesis, *it.keywords, *it.related_terms])
            if q.lower() not in blob.lower():
                continue
        res.append(it)
    return res


def categories() -> list[str]:
    seen: list[str] = []
    for it in load_insights():
        if it.category not in seen:
            seen.append(it.category)
    return seen


def actors() -> list[str]:
    seen: list[str] = []
    for it in load_insights():
        for a in it.actors:
            if a not in seen:
                seen.append(a)
    return seen
