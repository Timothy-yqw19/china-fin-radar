"""把专题洞察和库里真实抓到的政策/新闻融合起来.

专题的"脉络"和"可能动作"是人工整理, 但一份好的洞察必须能对回原始文件,
所以这里做三件事:
  1) 按关键词把库里的政策文件按年聚合成"自动脉络"(带文号与链接)
  2) 找出最近的相关动态(最近的几条, 看这条线现在走到哪了)
  3) 把洞察渲染成可直接读的文本, 并支持挂到日报/回顾报告后面
"""

from __future__ import annotations

from collections import defaultdict

from ..models import Insight

KEY_ACTOR_ORDER = ["监管", "国务院/部委", "公募", "券商", "银行", "保险", "外资机构"]


def _text(row: dict) -> str:
    return f"{row.get('title') or ''} {row.get('summary') or ''} {row.get('doc_no') or ''}"


def related_rows(
    insight: Insight,
    rows: list[dict],
    min_hits: int = 1,
    min_score: float = 30.0,
) -> list[dict]:
    """挑出和这个专题相关的条目.

    两道过滤:
      * 政策分下限 —— 关键词可能撞名(比如"算力互联互通"和金融互联互通),
        低分的快讯更容易是无关内容
      * 跳过 sample/demo 链接 —— scripts/demo_seed.py 造的假新闻不该出现在真实脉络里
    """
    keys = [k for k in insight.keywords if k]
    if not keys:
        return []
    out = []
    for r in rows:
        if "example.invalid" in (r.get("url") or ""):
            continue
        if float(r.get("policy_score") or 0) < min_score:
            continue
        t = _text(r)
        matched = [k for k in keys if k in t]
        n = len(matched)
        if n >= min_hits:
            rr = dict(r)
            rr["_hits"] = n
            rr["_matched"] = matched
            out.append(rr)
    return out


def corpus_timeline(
    insight: Insight,
    rows: list[dict],
    per_year: int = 3,
    recent: int = 5,
    min_score: float = 30.0,
) -> tuple[dict[str, list[dict]], list[dict]]:
    """按年聚合相关文件 (返回 年度 -> 当年最能代表这条线的几条, 以及最近的动态).

    排序按"命中关键词数 + 政策分", 这样同一年里最有代表性的文件排在前面。
    """
    rel = related_rows(insight, rows, min_score=min_score)
    if rel:
        # 同一份文件常被多个源转载, 合并后时间线才干净
        from .report import dedupe_rows

        rel = dedupe_rows(rel)
    by_year: dict[str, list[dict]] = defaultdict(list)
    for r in rel:
        d = (r.get("pub_date") or "")[:4]
        if d:
            by_year[d].append(r)
    for y in by_year:
        by_year[y].sort(
            key=lambda x: (-x.get("_hits", 0), -float(x.get("policy_score") or 0))
        )
        by_year[y] = by_year[y][:per_year]
    latest = sorted(
        (r for r in rel if (r.get("pub_date") or "")),
        key=lambda x: (x.get("pub_date") or ""),
        reverse=True,
    )[:recent]
    return dict(by_year), latest


def _line(label: str, value: str, indent: int = 0) -> str:
    pad = " " * indent
    return f"{pad}{label}：{value}"


def render_insight(
    insight: Insight,
    rows: list[dict] | None = None,
    per_year: int = 3,
    recent: int = 5,
) -> str:
    """渲染单个专题: 结论 → 脉络(人工 + 自动) → 现状 → 可能动作 → 观察信号 → 面试口径."""
    lines: list[str] = []
    lines.append(f"【{insight.topic}】")
    meta = []
    if insight.category:
        meta.append(f"分类: {insight.category}")
    if insight.actors:
        meta.append("涉及主体: " + "/".join(insight.actors))
    if meta:
        lines.append("  " + " | ".join(meta))
    lines.append("")

    if insight.thesis:
        lines.append("■ 核心判断")
        lines.append(f"  {insight.thesis}")
        lines.append("")

    if insight.timeline:
        lines.append("■ 脉络（人工整理）")
        for ev in insight.timeline:
            when = str(ev.get("when") or "")
            text = str(ev.get("event") or "")
            src = str(ev.get("source") or "")
            lines.append(f"  {when:<10} {text}")
            if src:
                lines.append(f"  {'':<10} └ 出处: {src}")
        lines.append("")

    if rows:
        by_year, latest = corpus_timeline(insight, rows, per_year=per_year, recent=recent)
        if by_year:
            lines.append("■ 脉络（自动聚合自库内文件，按年）")
            for y in sorted(by_year):
                items = by_year[y]
                lines.append(f"  {y}（{len(items)} 条代表）")
                for r in items:
                    score = r.get("policy_score")
                    doc_no = f" · {r['doc_no']}" if r.get("doc_no") else ""
                    lines.append(f"      [{score}] {r.get('title')}{doc_no}")
                    if r.get("url"):
                        lines.append(f"            {r['url']}")
            lines.append("")
        if latest:
            lines.append("■ 最新动态")
            for r in latest:
                lines.append(
                    f"  {(r.get('pub_date') or '')[:10]}  {r.get('title')}"
                )
            lines.append("")

    if insight.snapshot:
        lines.append("■ 现状快照（数字都会过期，答题时带上截止时点）")
        for s in insight.snapshot:
            as_of = f"（{s.get('as_of')}）" if s.get("as_of") else ""
            lines.append(f"  {s.get('label')}: {s.get('value')}{as_of}")
        lines.append("")

    if insight.outlook:
        lines.append("■ 未来可能的动作（按主体拆，写成“触发条件 → 动作”）")
        ordered = sorted(
            insight.outlook,
            key=lambda o: KEY_ACTOR_ORDER.index(o.get("actor"))
            if o.get("actor") in KEY_ACTOR_ORDER
            else 99,
        )
        for o in ordered:
            actor = o.get("actor") or "其他"
            horizon = o.get("horizon") or ""
            lines.append(f"  ▸ {actor}" + (f"（{horizon}）" if horizon else ""))
            lines.append(f"      可能动作：{o.get('action')}")
            if o.get("trigger"):
                lines.append(f"      触发条件：{o.get('trigger')}")
        lines.append("")

    if insight.watchlist:
        lines.append("■ 盯这几个信号")
        for w in insight.watchlist:
            lines.append(f"  - {w}")
        lines.append("")

    if insight.interview_take:
        lines.append("■ 面试口径（60-90 秒）")
        lines.append(f"  {insight.interview_take}")
        lines.append("")

    if insight.related_terms:
        lines.append("■ 关联热词：" + "、".join(insight.related_terms))
        lines.append("   看详解： finradar term " + insight.related_terms[0])
    return "\n".join(lines).rstrip()


def match_insights(text: str, insights: tuple[Insight, ...]) -> list[Insight]:
    """按关键词命中数排序, 找出这条文本最相关的专题 (用于报告自动挂洞察)."""
    scored = []
    for it in insights:
        n = sum(1 for k in it.keywords if k and k in text)
        if n:
            scored.append((n, it))
    scored.sort(key=lambda x: -x[0])
    return [it for _, it in scored]


def render_brief(insight: Insight, rows: list[dict], per_year: int = 2) -> str:
    """报告里用的精简版: 核心判断 + 自动脉络 + 各主体可能动作."""
    lines = [f"### {insight.topic}", ""]
    if insight.thesis:
        lines += [f"**核心判断**：{insight.thesis}", ""]
    by_year, _ = corpus_timeline(insight, rows, per_year=per_year)
    if by_year:
        lines.append("**这条线是怎么走到今天的**（自动聚合自本期报告命中文件）")
        lines.append("")
        for y in sorted(by_year):
            items = by_year[y]
            head = items[0]
            extra = f" 等 {len(items)} 条" if len(items) > 1 else ""
            lines.append(f"- {y}：[{head.get('title')}]({head.get('url')}){extra}")
        lines.append("")
    if insight.snapshot:
        lines.append("**现状**：" + "；".join(
            f"{s.get('label')} {s.get('value')}" + (f"（{s.get('as_of')}）" if s.get("as_of") else "")
            for s in insight.snapshot
        ))
        lines.append("")
    if insight.outlook:
        lines.append("**未来可能的动作**")
        lines.append("")
        for o in sorted(
            insight.outlook,
            key=lambda o: KEY_ACTOR_ORDER.index(o.get("actor"))
            if o.get("actor") in KEY_ACTOR_ORDER
            else 99,
        ):
            lines.append(
                f"- **{o.get('actor')}**：{o.get('action')}"
                + (f"（触发条件：{o.get('trigger')}）" if o.get("trigger") else "")
            )
        lines.append("")
    if insight.watchlist:
        lines.append("**盯**：" + "；".join(insight.watchlist))
        lines.append("")
    return "\n".join(lines)
