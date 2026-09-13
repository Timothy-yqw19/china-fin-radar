"""把专题洞察 + 名词档案 + 候选新词渲染成一个单文件网页.

网页模板在 web/insights_template.html，样式复用词库网页(web/template.html)的
那一套 CSS 变量, 视觉上保持一致。数据以 JSON 注入, 页面用 JS 渲染, 支持搜索与筛选。
"""

from __future__ import annotations

import json
import re

from ..knowledge import glossary as G
from ..knowledge import insights as I
from ..utils import ROOT_DIR, now_cn
from .dossiers import mine_candidates, term_dossiers
from .features import build_feature
from .insights import corpus_timeline

TEMPLATE = ROOT_DIR / "web" / "insights_template.html"
KB_TEMPLATE = ROOT_DIR / "web" / "template.html"


def _clean(o):  # noqa: ANN001, ANN201
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    if isinstance(o, str):
        return re.sub(r"\s+", " ", o).strip()
    if isinstance(o, (int, float)) or o is None:
        return o
    return str(o)


def _doc(r: dict) -> dict:
    return {
        "date": (r.get("pub_date") or "")[:10],
        "title": r.get("title"),
        "url": r.get("url"),
        "score": r.get("policy_score"),
        "doc_no": r.get("doc_no") or "",
        "source": r.get("source_name") or "",
    }


def build_payload(
    rows: list[dict],
    insights=None,  # noqa: ANN001
    terms=None,  # noqa: ANN001
    candidates: bool = True,
    features: bool = True,
    min_score: float = 30.0,
) -> dict:
    """组装网页数据. insights/terms 传 None 表示全部, 传 [] 表示不要这一段."""
    insights = list(I.load_insights() if insights is None else insights)
    terms = list(G.load_glossary() if terms is None else terms)

    ins_payload = []
    for it in insights:
        by_year, latest = corpus_timeline(it, rows, per_year=3, recent=6, min_score=min_score)
        d = _clean(it.to_dict())
        d["stats"] = {
            "by_year": [
                {"year": y, "docs": [_doc(r) for r in by_year[y]]} for y in sorted(by_year)
            ],
            "latest": [_doc(r) for r in latest],
        }
        ins_payload.append(d)

    date_sorted = sorted((r.get("pub_date") or "")[:10] for r in rows if (r.get("pub_date") or ""))
    return {
        "generated": now_cn().strftime("%Y-%m-%d %H:%M"),
        "n_news": len(rows),
        "coverage": f"{date_sorted[0]} ~ {date_sorted[-1]}" if date_sorted else "",
        "insights": ins_payload,
        # 成篇报道: 只带上写了正文的
        "features": (
            [_clean(build_feature(it, rows, min_score=min_score)) for it in insights if it.feature]
            if features
            else []
        ),
        "terms": _clean(term_dossiers(terms, rows, insights)),
        "candidates": (
            _clean(
                mine_candidates(
                    rows,
                    glossary_names=[t.term for t in G.load_glossary()],
                    insight_keywords=[k for i in I.load_insights() for k in i.keywords],
                    top=40,
                    min_count=3,
                )
            )
            if candidates
            else []
        ),
    }


def shared_style() -> str:
    """复用词库网页的整套样式(配色/排版/暗色模式), 避免两份 CSS 各改各的."""
    src = KB_TEMPLATE.read_text(encoding="utf-8")
    return src[src.index("<style>") : src.index("</style>") + len("</style>")]


def render_site(payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return (
        TEMPLATE.read_text(encoding="utf-8")
        .replace("__STYLE__", shared_style())
        .replace("__INSIGHT_DATA__", data)
    )
