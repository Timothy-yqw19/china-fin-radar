"""热词库加载与检索."""

from __future__ import annotations

import functools
from pathlib import Path

import yaml

from ..models import Term
from ..utils import DATA_DIR

GLOSSARY_DIR = DATA_DIR / "glossary"


@functools.lru_cache(maxsize=1)
def load_glossary(directory: str | None = None) -> tuple[Term, ...]:
    d = Path(directory) if directory else GLOSSARY_DIR
    terms: list[Term] = []
    for f in sorted(d.glob("*.yaml")):
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        cat = doc.get("category", f.stem)
        for raw in doc.get("terms") or []:
            raw.setdefault("category", cat)
            allowed = Term.__dataclass_fields__.keys()  # type: ignore[attr-defined]
            terms.append(Term(**{k: v for k, v in raw.items() if k in allowed}))
    return tuple(terms)


def categories() -> list[str]:
    seen: list[str] = []
    for t in load_glossary():
        if t.category not in seen:
            seen.append(t.category)
    return seen


def search(
    q: str = "",
    category: str | None = None,
    institution: str | None = None,
    min_heat: int = 0,
) -> list[Term]:
    res = []
    for t in load_glossary():
        if category and category not in t.category:
            continue
        if institution and institution not in t.institutions:
            continue
        if t.heat < min_heat:
            continue
        if q:
            blob = " ".join(
                [t.term, *t.aliases, t.definition, t.why_hot, " ".join(t.exam_points), t.category]
            )
            if q.lower() not in blob.lower():
                continue
        res.append(t)
    return sorted(res, key=lambda x: (-x.heat, x.category))


def get(term_or_id: str) -> Term | None:
    for t in load_glossary():
        if term_or_id == t.id or term_or_id in t.all_names:
            return t
    for t in load_glossary():
        if term_or_id in t.term:
            return t
    return None


def render_term(t: Term, wide: int = 88) -> str:
    """把一个词条渲染成终端友好的文本."""
    import textwrap

    def para(label: str, text: str) -> list[str]:
        if not text:
            return []
        body = textwrap.fill(" ".join(str(text).split()), wide, subsequent_indent="  ")
        return [f"【{label}】", f"  {body}", ""]

    out = [
        "=" * wide,
        f"{t.term}   " + "★" * t.heat,
        f"分类: {t.category} | 出现: {t.era} | 阶段: {t.plan} | 适用: {'/'.join(t.institutions)}",
        "=" * wide,
        "",
    ]
    if t.aliases:
        out += [f"别名/关联: {'、'.join(t.aliases)}", ""]
    out += para("定义", t.definition)
    out += para("出处", t.source)
    out += para("为什么热", t.why_hot)
    if t.exam_points:
        out += ["【考点清单】"]
        for i, p in enumerate(t.exam_points, 1):
            body = textwrap.fill(" ".join(str(p).split()), wide - 5, subsequent_indent="     ")
            out.append(f"  {i}. {body}")
        out.append("")
    out += para("面试口语化答法（60-90秒）", t.interview_answer)
    if t.facts_snapshot:
        out += ["【数据快照】"]
        for k, v in t.facts_snapshot.items():
            out.append(f"  {k}: {v}")
        out.append("")
    if t.followups:
        out += ["【可能的追问】"]
        for fu in t.followups:
            out.append(f"  Q: {fu.get('q','')}")
            body = textwrap.fill(
                " ".join(str(fu.get("a", "")).split()), wide - 6, subsequent_indent="     "
            )
            out += [f"  A: {body}", ""]
    if t.related:
        out += [f"【关联词】 {'、'.join(t.related)}", ""]
    return "\n".join(out)
