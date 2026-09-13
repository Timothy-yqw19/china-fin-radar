"""专题报道: 把一条主线的脉络、现状与展望写成成篇的文章.

和 `insights.py` 的分工:
  * insights.py  —— 结构化清单(脉络条目、按主体拆的动作), 适合速查
  * features.py  —— 成篇报道(导语 + 段落 + 转折 + 收尾), 适合通读与讲给面试官听

报道的正文由人工写在 insight 的 `feature` 字段里(散文体), 渲染时自动织入两样
来自真实语料的东西:
  1) 「数据支撑」段 —— 这段线在库里有多少文件、最早哪年出现、近三年占比、
     最近一份文件是什么(带文号与链接), 全部用句子写, 不是列条目
  2) 「主要文件」—— 供核对的少量出处链接, 放在文末
"""

from __future__ import annotations

from collections import Counter

from ..models import Insight
from ..utils import now_cn
from .insights import corpus_timeline, related_rows

# 人事任免、表彰通报这类文件标题里也会出现关键词, 但不承载政策信息
NOISE_TITLE = ("组成人员", "任职", "免职", "任免", "人事", "表彰", "通报批评", "名单公布")


def _sentences_from_corpus(insight: Insight, rows: list[dict], min_score: float = 30.0) -> str:
    """生成"数据支撑"段: 用句子说清这条线在真实语料里的位置."""
    rel = related_rows(insight, rows, min_score=min_score)
    if not rel:
        return ""
    dates = sorted((r.get("pub_date") or "")[:10] for r in rel if (r.get("pub_date") or ""))
    by_year = Counter(d[:4] for d in dates)
    years = sorted(by_year)
    cut = now_cn().year - 3
    recent = sum(by_year[y] for y in years if int(y) >= cut)
    share = round(recent / max(1, len(dates)) * 100)

    parts: list[str] = []
    parts.append(
        f"在抓到的 {len(rows):,} 条政策与新闻里，涉及这条线的有 {len(rel)} 条，"
        f"跨度从 {dates[0]} 到 {dates[-1]}。"
    )
    if len(years) > 1:
        top = ", ".join(f"{y} 年 {by_year[y]} 条" for y in years[-3:])
        parts.append(f"按年看最近三年是 {top}，近三年合计占 {share}%。")
    # 找出这条线上最近的一份正式文件。这里刻意用"最近"而不是"最重要"——
    # 重要性是判断, 而"标题点到这条线、且有文号"是客观可核对的。
    scored = [
        r for r in rel
        if (r.get("pub_date") or "")
        and not any(w in (r.get("title") or "") for w in NOISE_TITLE)
    ]
    multi = [r for r in scored if int(r.get("_hits") or 1) >= 2]
    with_docno = [r for r in (multi or scored) if r.get("doc_no")]
    # 标题里点到这条线的文件最可信(摘要里"顺带提一句"的不算);
    # 在"标题命中"里再优先有文号的正式文件。
    # keywords 前 8 个是"核心词"(写在 YAML 最前面), 后面的偏通用, 只用于统计命中数。
    core = [k for k in insight.keywords[:8] if k]
    titled = [r for r in scored if any(k in (r.get("title") or "") for k in core)]
    titled_docno = [r for r in titled if r.get("doc_no")]
    pool = titled_docno or titled or with_docno or multi or scored
    if pool:
        best = max(pool, key=lambda r: (r.get("pub_date") or "", int(r.get("_hits") or 1)))
        doc_no = f"（{best.get('doc_no')}）" if best.get("doc_no") else ""
        src = best.get("source_name") or ""
        parts.append(
            f"这条线上最近一份正式文件是 {best.get('pub_date')[:10]} 由{src}发布的"
            f"《{best.get('title')}》{doc_no}。"
        )
    return "".join(parts)


def build_feature(
    insight: Insight,
    rows: list[dict] | None = None,
    min_score: float = 30.0,
) -> dict:
    """组装一篇报道的数据结构(供 Markdown / HTML 渲染)."""
    rows = rows or []
    feat = insight.feature or {}
    data_para = _sentences_from_corpus(insight, rows, min_score=min_score) if rows else ""
    _, latest = corpus_timeline(insight, rows, per_year=1, recent=5, min_score=min_score) if rows else ({}, [])
    # 外部视角(海外媒体与机构): 英文报道要先做中英映射
    external: list[dict] = []
    if rows:
        from .views import expand_keywords, pick_views

        external = [
            {
                "date": (r.get("pub_date") or "")[:10],
                "title": r.get("title"),
                "url": r.get("url") or "",
                "source": r.get("source_name") or r.get("source") or "",
            }
            for r in pick_views(rows, "external", expand_keywords(list(insight.keywords)), top=5)
        ]
    return {
        "id": insight.id,
        "topic": insight.topic,
        "category": insight.category,
        "actors": insight.actors,
        "thesis": insight.thesis,
        "lead": feat.get("lead", ""),
        "sections": feat.get("sections") or [],
        "data_paragraph": data_para,
        "latest": [
            {
                "date": (r.get("pub_date") or "")[:10],
                "title": r.get("title"),
                "url": r.get("url"),
                "doc_no": r.get("doc_no") or "",
                "source": r.get("source_name") or "",
            }
            for r in latest
        ],
        "has_feature": bool(feat),
        "external": external,
        "generated": now_cn().strftime("%Y-%m-%d"),
    }


def render_feature_markdown(art: dict) -> str:
    """渲染成 Markdown 报道."""
    lines = [f"# {art['topic']}", ""]
    meta = [f"{art['category']}"] if art.get("category") else []
    if art.get("actors"):
        meta.append("涉及主体：" + " / ".join(art["actors"]))
    if art.get("generated"):
        meta.append(f"生成于 {art['generated']}")
    lines += ["> " + " · ".join(meta), ""]
    if art.get("lead"):
        lines += [art["lead"], ""]
    else:
        # 没写导语就退回核心判断, 保证报道开头有观点
        lines += [art.get("thesis", ""), ""]
    if art.get("data_paragraph"):
        lines += ["## 数据支撑", "", art["data_paragraph"], ""]
    for sec in art.get("sections") or []:
        lines += [f"## {sec.get('title','')}", "", sec.get("body", ""), ""]
    if art.get("external"):
        lines += ["## 外部视角（海外机构与媒体怎么读）", ""]
        for d in art["external"]:
            if d.get("url"):
                lines.append(f"- {d['date']}　[{d['title']}]({d['url']})　`{d['source']}`")
            else:
                lines.append(f"- {d['date']}　{d['title']}　`{d['source']}`")
        lines.append("")
    if not art.get("sections"):
        lines += [
            "## 核心判断",
            "",
            art.get("thesis", ""),
            "",
            "> 这篇专题还没写正文：在 finradar/data/insights/*.yaml 里补 `feature.lead`"
            " 与 `feature.sections` 即可（结构见 docs/GLOSSARY_SCHEMA.md）。",
            "",
        ]
    if art.get("latest"):
        lines += ["## 主要文件（供核对）", ""]
        for d in art["latest"]:
            doc_no = f" · {d['doc_no']}" if d.get("doc_no") else ""
            if d.get("url"):
                lines.append(f"- {d['date']}　[{d['title']}]({d['url']}){doc_no}")
            else:
                lines.append(f"- {d['date']}　{d['title']}{doc_no}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_feature_html(art: dict) -> str:
    """渲染成适合阅读的 HTML 片段(嵌入整站时用; 独立页面见 insight_site)."""
    import html as _h

    def p(text: str) -> str:
        return "".join(f"<p>{_h.escape(x.strip())}</p>" for x in (text or "").split("\n") if x.strip())

    parts = [f"<article class='feature' id='{_h.escape(art['id'])}'>"]
    parts.append(f"<h2>{_h.escape(art['topic'])}</h2>")
    meta = [art.get("category") or ""]
    if art.get("actors"):
        meta.append("涉及主体：" + " / ".join(art["actors"]))
    parts.append(f"<div class='fmeta'>{_h.escape(' · '.join([m for m in meta if m]))}</div>")
    if art.get("lead"):
        parts.append(f"<p class='lead'>{_h.escape(art['lead'])}</p>")
    elif art.get("thesis"):
        parts.append(f"<p class='lead'>{_h.escape(art['thesis'])}</p>")
    if art.get("data_paragraph"):
        parts.append(f"<h3>数据支撑</h3>{p(art['data_paragraph'])}")
    for sec in art.get("sections") or []:
        parts.append(f"<h3>{_h.escape(sec.get('title',''))}</h3>{p(sec.get('body',''))}")
    if art.get("external"):
        items = "".join(
            "<li>"
            + (
                f"<a href='{_h.escape(d['url'])}' target='_blank' rel='noopener'>{_h.escape(d['title'])}</a>"
                if d.get("url")
                else _h.escape(d["title"] or "")
            )
            + f"<span class='m'>{_h.escape(d['date'])} · {_h.escape(d.get('source',''))}</span></li>"
            for d in art["external"]
        )
        parts.append(f"<h3>外部视角（海外机构与媒体怎么读）</h3><ul class='docs'>{items}</ul>")
    if art.get("latest"):
        items = "".join(
            "<li>"
            + (
                f"<a href='{_h.escape(d['url'])}' target='_blank' rel='noopener'>{_h.escape(d['title'])}</a>"
                if d.get("url")
                else _h.escape(d["title"] or "")
            )
            + f"<span class='m'>{_h.escape(d['date'])}{(' · ' + _h.escape(d['doc_no'])) if d.get('doc_no') else ''}</span></li>"
            for d in art["latest"]
        )
        parts.append(f"<h3>主要文件（供核对）</h3><ul class='docs'>{items}</ul>")
    parts.append("</article>")
    return "".join(parts)
