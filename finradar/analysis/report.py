"""政策日报 / 周报生成 (Markdown + 单文件 HTML)."""

from __future__ import annotations

import html
import re
from pathlib import Path

from ..utils import now_cn, workdir
from .hotwords import combined_hotwords

# 分组时优先按"议题"归类, 而不是一律塞进"政策动作" —— 否则日报第一节
# 会装下 90% 的条目, 分开等于没分。
BOARD_ORDER = [
    "货币政策", "资本市场", "银行保险", "对外开放", "主题热词", "政策动作", "发文主体",
]

_PUNCT = re.compile(r"[\s　·、，,。.；;：:！!？?“”\"'（）()《》〈〉\[\]【】\-—_/\\|]+")
# 公文标题里的"包装词": 同一份文件被不同站点转载时, 常常只差这些词
_PACKAGING = ("关于印发", "关于做好", "关于", "印发", "的通知", "通知", "的公告", "公告",
              "的批复", "批复", "发布", "出台")


def _norm_title(title: str) -> str:
    return _PUNCT.sub("", title or "")


def _core_title(title: str) -> str:
    s = _norm_title(title)
    for w in _PACKAGING:
        s = s.replace(w, "")
    return s


def dedupe_rows(rows: list[dict]) -> list[dict]:
    """把"同一件事被多个源抓到"的条目合并成一条.

    实测里同一个文件常常同时出现在 政府网政策库 / 财政部 / 财联社, 标题还会
    略有差异(带不带"关于印发…的通知"), 所以除了完全相同, 还做一次包含式匹配;
    合并后保留政策分最高的一条, 并记下其他来源。
    """
    kept: list[dict] = []
    index: list[str] = []
    cores: list[str] = []
    doc_index: dict[str, int] = {}
    for r in sorted(rows, key=lambda x: -float(x.get("policy_score") or 0)):
        norm = _norm_title(r.get("title") or "")
        if not norm:
            continue
        core = _core_title(r.get("title") or "")
        # 同一份文件被两个源转载时, 文号是最可靠的合并依据
        doc_no = (r.get("doc_no") or "").strip()
        dup_at = None
        if doc_no and doc_no in doc_index:
            dup_at = doc_index[doc_no]
        for i, k in enumerate(index):
            if dup_at is not None:
                break
            if norm == k:
                dup_at = i
                break
            # 去掉"关于印发…的通知"这类包装词后再比: 标题重复才算同一件事,
            # 但数字必须完全一致 —— "公告第178号"和"第177号"不是一条。
            ck = cores[i]
            short, long_ = (core, ck) if len(core) <= len(ck) else (ck, core)
            if len(short) >= 12 and short in long_:
                dup_at = i
                break
        if dup_at is None:
            r = dict(r)
            r["also_from"] = []
            index.append(norm)
            cores.append(core)
            kept.append(r)
            if doc_no:
                doc_index[doc_no] = len(kept) - 1
        else:
            host = kept[dup_at]
            src = r.get("source_name") or r.get("source") or ""
            if src and src != (host.get("source_name") or host.get("source")):
                host.setdefault("also_from", [])
                if src not in host["also_from"]:
                    host["also_from"].append(src)
    return kept


def _group(rows: list[dict]) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        tags = r.get("tags") or []
        key = next((t for t in BOARD_ORDER if t in tags), "其他")
        buckets.setdefault(key, []).append(r)
    return buckets


def _group_by_time(rows: list[dict], bucket: str) -> list[tuple[str, list[dict]]]:
    """按时段分组(年/季/月/周), 时段按时间正序 —— 长窗口的"回顾"要靠它."""
    from .periods import period_key, period_sort_key

    buckets: dict[str, list[dict]] = {}
    for r in rows:
        key = period_key((r.get("pub_date") or "")[:10], bucket)
        if not key:
            continue
        buckets.setdefault(key, []).append(r)
    return [
        (k, sorted(v, key=lambda x: (-float(x.get("policy_score") or 0), x.get("pub_date") or "")))
        for k, v in sorted(buckets.items(), key=lambda kv: period_sort_key(kv[0]))
    ]


def _coverage(rows: list[dict]) -> str:
    """数据覆盖区间 —— 长窗口特别需要, 否则"近 5 年"里其实只有 1 年数据是看不出来的."""
    dates = sorted((r.get("pub_date") or "")[:10] for r in rows if (r.get("pub_date") or ""))
    if not dates:
        return ""
    if dates[0] == dates[-1]:
        return dates[0]
    return f"{dates[0]} ~ {dates[-1]}"


def _md_to_html(md: str) -> str:
    """极简 Markdown → HTML (只覆盖洞察渲染用到的那几种语法)."""
    out: list[str] = []
    in_list = False
    for raw in (md or "").splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<h2>{_inline(line[3:])}</h2>")
            continue
        if line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            body = _inline(line[2:])
            out.append(f"<li>{body}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if not line:
            continue
        if line.startswith("### "):
            out.append(f"<h3>{_inline(line[4:])}</h3>")
        else:
            out.append(f"<p>{_inline(line)}</p>")
    if in_list:
        out.append("</ul>")
    return "".join(out)


def formal_md_to_html(md: str) -> str:
    """公文版 Markdown → HTML: 支持 ##/### 标题、无序与有序列表、表格、续行."""
    out: list[str] = []
    in_ul = in_ol = in_table = False

    def close_all() -> None:
        nonlocal in_ul, in_ol, in_table
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False
        if in_table:
            out.append("</tbody></table>")
            in_table = False

    for raw in (md or "").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue  # 分隔行
            if not in_table:
                out.append("<table><thead><tr>")
                out.append("".join(f"<th>{_inline(c)}</th>" for c in cells))
                out.append("</tr></thead><tbody>")
                in_table = True
            else:
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
            continue
        if in_table and not stripped.startswith("|"):
            close_all()
        if not stripped:
            close_all()
            continue
        if stripped.startswith("### "):
            close_all()
            out.append(f"<h3>{_inline(stripped[4:])}</h3>")
            continue
        if stripped.startswith("## "):
            close_all()
            out.append(f"<h2>{_inline(stripped[3:])}</h2>")
            continue
        if stripped.startswith("# "):
            close_all()
            out.append(f"<h1 class='doc-title'>{_inline(stripped[2:])}</h1>")
            continue
        import re as _re

        m = _re.match(r"^(\d+)\.\s+(.*)$", stripped)
        if m:
            if not in_ol:
                close_all()
                out.append("<ol>")
                in_ol = True
            out.append(f"<li>{_inline(m.group(2))}</li>")
            continue
        if stripped.startswith("- "):
            if not in_ul:
                close_all()
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{_inline(stripped[2:])}</li>")
            continue
        if line.startswith("    "):  # 缩进续行: 条目下的补充说明
            out.append(f"<p class='cont'>{_inline(stripped)}</p>")
            continue
        out.append(f"<p>{_inline(stripped)}</p>")
    close_all()
    return "".join(out)


def _inline(text: str) -> str:
    import re

    s = html.escape(text)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)
    return s


# ---------------------------------------------------------------- 公文文风

CN_DIGITS = "一二三四五六七八九十"


def _cn_num(i: int) -> str:
    """1 -> 一, 11 -> 十一（用于公文式编号）."""
    if i <= 10:
        return CN_DIGITS[i - 1]
    if i < 20:
        return "十" + CN_DIGITS[i - 11]
    return str(i)


def _cn_date(iso: str) -> str:
    s = (iso or "")[:10]
    try:
        y, m, d = s.split("-")
        return f"{y}年{int(m)}月{int(d)}日"
    except ValueError:
        return s


def _span_text(rows: list[dict]) -> str:
    dates = sorted((r.get("pub_date") or "")[:10] for r in rows if (r.get("pub_date") or ""))
    if not dates:
        return "—"
    return f"{_cn_date(dates[0])}至{_cn_date(dates[-1])}"


def _trim(text: str, n: int = 120) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def build_formal_markdown(
    rows: list[dict],
    title: str,
    top_hot: int,
    group_by: str,
    bucket: str,
    top_per_period: int,
    insights: list | None,
    view_rows: list[dict] | None,
    translate: str,
) -> str:
    """公文/申论体: 标题 → 编制说明 → 分章编号 → 数据来源与口径."""
    raw_n = len(rows)
    rows = dedupe_rows(rows)
    hot = combined_hotwords(rows)
    vr = view_rows if view_rows is not None else rows
    span = _span_text(rows)
    lines = [f"# {title}", "", span, ""]
    lines += [
        f"**编制时间**：{now_cn().strftime('%Y年%m月%d日 %H:%M')}（北京时间）",
        f"**统计区间**：{span}",
        f"**样本数量**：本轮收录 {raw_n} 条，经跨源合并后 {len(rows)} 条"
        + (f"（合并 {raw_n - len(rows)} 条同源转载）" if raw_n != len(rows) else ""),
        f"**数据覆盖**：{_coverage(rows) or '—'}",
        "**使用说明**：本通报用于政策学习与信息整理，不构成投资建议；"
        "政策口径以官方原文为准，涉及数据请按标注时点核对。",
        "",
    ]

    from .views import expand_keywords, pick_views, translate_titles

    # 一、政策热词情况
    if hot:
        lines += ["## 一、政策热词情况", ""]
        lines += [
            "本区间内，政策文本与新闻中出现频次较高的政策提法如下：",
            "",
            "| 序号 | 政策提法 | 出现频次 |",
            "| --- | --- | --- |",
        ]
        for i, (w, n) in enumerate(hot.most_common(top_hot), 1):
            lines.append(f"| {i} | {w} | {n} |")
        lines.append("")
        idx = 2
    else:
        idx = 1

    # 二、重点政策与市场动态
    lines += [f"## {_cn_num(idx)}、重点政策与市场动态", ""]
    if group_by == "time":
        groups = _group_by_time(rows, bucket)
        for gi, (key, items) in enumerate(groups, 1):
            lines += [f"### （{_cn_num(gi)}）{key}（{len(items)} 条）", ""]
            for r in items[:top_per_period]:
                lines += _formal_item(r, 1)
            lines.append("")
    else:
        for gi, (board, items) in enumerate(_group(rows).items(), 1):
            lines += [f"### （{_cn_num(gi)}）{board}（{len(items)} 条）", ""]
            for r in sorted(items, key=lambda x: -float(x.get("policy_score") or 0))[:20]:
                lines += _formal_item(r, 1)
            lines.append("")
    idx += 1

    # 三、媒体与外部视角
    hot_words = [w for w, _ in hot.most_common(24)] if hot else []
    media = pick_views(vr, "media", hot_words, top=8)
    external = pick_views(vr, "external", expand_keywords(hot_words), top=8)
    note = ""
    if not external:
        external = pick_views(vr, "external", None, top=5)
        if external:
            note = "（以下为近期海外动态，与本期热词未直接对应）"
    if media or external:
        lines += [f"## {_cn_num(idx)}、媒体与外部视角", ""]
        if media:
            lines += ["### （一）权威媒体报道", ""]
            for i, r in enumerate(media, 1):
                src = r.get("source_name") or r.get("source") or ""
                lines.append(
                    f"{i}. [{r.get('title')}]({r.get('url')})（{src}，"
                    f"{_cn_date(r.get('pub_date'))}）"
                )
            lines.append("")
        if external:
            lines += [f"### （二）海外机构与媒体解读{note}", ""]
            zh = translate_titles(external, backend=translate)
            for i, r in enumerate(external, 1):
                src = r.get("source_name") or r.get("source") or ""
                lines.append(
                    f"{i}. [{r.get('title')}]({r.get('url')})（{src}，"
                    f"{_cn_date(r.get('pub_date'))}）"
                )
                if zh.get(r.get("title") or ""):
                    lines.append(f"   译：{zh[r['title']]}")
            lines.append("")
        idx += 1

    # 四、专题分析
    if insights:
        from .insights import render_brief

        lines += [f"## {_cn_num(idx)}、专题分析", ""]
        for it in insights:
            lines.append(render_brief(it, rows))
        idx += 1

    # 五、数据来源与口径
    lines += [
        f"## {_cn_num(idx)}、数据来源与口径说明",
        "",
        "**（一）数据来源**：中国政府网政策文件库、国务院及各部委网站、"
        "中国人民银行、国家外汇管理局、中国证监会、国家金融监督管理总局、"
        "财政部、国家发展改革委、国家统计局、中国证券投资基金业协会、"
        "新华社、中国证券报、上海证券报、证券日报、证券时报等权威媒体，"
        "以及公开财经资讯渠道。",
        "",
        "**（二）评分口径**：政策相关度评分区间为 0 至 100 分，由政策动作、发文主体、"
        "货币与资本市场等六类关键词加权生成，并对噪音信息与例行事项作降权处理。",
        "",
        "**（三）去重口径**：同一文件被多个渠道转载的，按标题与文号合并，"
        "仅保留政策相关度最高的一条，并标注同源报道。",
        "",
        "**（四）免责声明**：本通报为个人学习整理的成果，不代表任何机构立场；"
        "涉及政策表述与数据，均应以官方发布原文及最新口径为准。",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _formal_item(r: dict, n: int) -> list[str]:
    """公文式的单条条目: 标题 + 来源时点 + 主要内容 + 政策传导路径."""
    date = _cn_date(r.get("pub_date"))
    src = r.get("source_name") or r.get("source") or ""
    title = r.get("title") or ""
    url = r.get("url") or ""
    score = r.get("policy_score")
    head = f"{n}. [{title}]({url})" if url else f"{n}. {title}"
    out = [head]
    meta = f"   来源：{src}；时间：{date}；政策相关度：{score}"
    if r.get("doc_no"):
        meta += f"；文号：{r['doc_no']}"
    out.append(meta)
    if r.get("summary"):
        out.append(f"   主要内容：{_trim(r.get('summary'))}")
    if r.get("impact"):
        out.append(f"   政策传导路径：{r['impact']}")
    return out


def build_markdown(
    rows: list[dict],
    title: str = "金融政策日报",
    top_hot: int = 15,
    group_by: str = "issue",
    bucket: str = "month",
    top_per_period: int = 8,
    insights: list | None = None,
    view_rows: list[dict] | None = None,
    translate: str = "none",
    style: str = "formal",
) -> str:
    if style == "formal":
        return build_formal_markdown(
            rows, title, top_hot, group_by, bucket, top_per_period,
            insights, view_rows, translate,
        )
    now = now_cn().strftime("%Y-%m-%d %H:%M")
    raw_n = len(rows)
    rows = dedupe_rows(rows)
    hot = combined_hotwords(rows)
    sub = f"> 生成时间 {now}（北京时间） · 共 {len(rows)} 条"
    if raw_n != len(rows):
        sub += f"（已合并 {raw_n - len(rows)} 条跨源重复）"
    coverage = _coverage(rows)
    if coverage:
        sub += f" · 数据覆盖 {coverage}"
    if group_by == "time":
        from .periods import BUCKET_LABEL

        sub += f" · 按时段整理（{BUCKET_LABEL.get(bucket, bucket)}）"
    lines = [f"# {title}", "", sub, ""]

    if hot:
        lines += ["## 一、热词榜", ""]
        lines += [
            "| # | 热词 | 出现次数 |",
            "|---|------|---------|",
        ]
        for i, (w, n) in enumerate(hot.most_common(top_hot), 1):
            lines.append(f"| {i} | {w} | {n} |")
        lines.append("")

    # 权威媒体 + 外部视角: 让报告不止有"官方原文", 还有媒体怎么说、海外怎么读
    from .views import pick_views, render_views_markdown, translate_titles

    # 媒体与外部视角单独取数: 它们的政策分天然很低, 不能跟正文用同一个阈值
    vr = view_rows if view_rows is not None else rows
    hot_words = [w for w, _ in hot.most_common(24)] if hot else []
    lines += render_views_markdown(
        pick_views(vr, "media", hot_words, top=8), "媒体视角（权威媒体怎么报道）"
    )
    from .views import expand_keywords

    # 海外报道是英文的: 先把中文热词映射成英文说法再匹配
    external = pick_views(vr, "external", expand_keywords(hot_words), top=8)
    note = ""
    if not external:
        # 本期热词没被海外条目命中时, 退而给出最近的海外动态, 并说明它们没对上
        external = pick_views(vr, "external", None, top=5)
        if external:
            note = "（以下为近期海外动态，未与本期热词直接对应）"
    if external:
        lines += [f"## 外部视角（海外机构与媒体怎么读）{note}", ""]
        zh = translate_titles(external, backend=translate)
        for r in external:
            date = (r.get("pub_date") or "")[:10]
            src = r.get("source_name") or r.get("source") or ""
            url = r.get("url") or ""
            t = r.get("title") or ""
            lines.append(f"- {date}　[{t}]({url})　`{src}`" if url else f"- {date}　{t}　`{src}`")
            if zh.get(t):
                lines.append(f"  - 中文：{zh[t]}")
        lines.append("")

    if insights:
        from .insights import render_brief

        lines += ["## 专题洞察与展望", ""]
        lines += [
            "> 每条专题 = 这条线的来龙去脉 + 现状 + **各主体可能的动作（带触发条件）**。",
            "> 展望是“看到什么信号说明它要来”，不是预测；详细版用 `finradar insight <专题>`。",
            "",
        ]
        for it in insights:
            lines.append(render_brief(it, rows))

    # ---- 长窗口: 先给"演变矩阵", 再按年/季/月回顾 ----
    if group_by == "time":
        from .hotwords import render_trend
        from .periods import BUCKET_LABEL

        lines += ["## 二、热词演变（词 × 时段）", "", "```"]
        lines.append(render_trend(rows, bucket=bucket, top=15, periods=14))
        lines += ["```", ""]
        lines += [f"## 三、分{BUCKET_LABEL.get(bucket, bucket)}回顾", ""]
        for key, items in _group_by_time(rows, bucket):
            period_hot = combined_hotwords(items).most_common(6)
            lines.append(f"### {key}（{len(items)} 条）")
            lines.append("")
            if period_hot:
                lines.append("热点词汇：" + "、".join(f"{w}({n})" for w, n in period_hot))
                lines.append("")
            for r in items[:top_per_period]:
                date = (r.get("pub_date") or "")[:10]
                src = r.get("source_name") or r.get("source")
                t = r.get("title") or ""
                url = r.get("url") or ""
                lines.append(f"- **[{t}]({url})**" if url else f"- **{t}**")
                meta = f"  - {date} · {src} · 政策分 {r.get('policy_score')}"
                if r.get("doc_no"):
                    meta += f" · {r['doc_no']}"
                lines.append(meta)
                if r.get("hotwords"):
                    lines.append(f"  - 命中热词：{'、'.join(r['hotwords'][:8])}")
                if r.get("impact"):
                    lines.append(f"  - 传导逻辑：{r['impact']}")
            lines.append("")
        lines += [
            "## 四、怎么用这份回顾准备面试",
            "",
            "1. 先看「热词演变」矩阵：哪个词在哪一年冒出来、哪一年开始高频、",
            "   哪一年被新提法替代——这就是面试里“聊聊这几年金融政策变化”的骨架。",
            "2. 再挑每个时段的 1-2 条头条精读，问自己三个问题：谁发的 / 改了什么 / 谁受影响。",
            "3. 把当年出现、后来消失的词单独记一笔——被替代的提法最容易被追问“那和现在有什么区别”。",
            "4. 用 `finradar quiz --board <板块>` 把对应考点刷一遍。",
            "",
        ]
        return "\n".join(lines)

    lines += ["## 二、按板块梳理", ""]
    for board, items in _group(rows).items():
        lines.append(f"### {board}（{len(items)}）")
        lines.append("")
        for r in sorted(items, key=lambda x: -float(x.get("policy_score") or 0))[:20]:
            date = (r.get("pub_date") or "")[:10]
            src = r.get("source_name") or r.get("source")
            t = r.get("title") or ""
            url = r.get("url") or ""
            head = f"- **{t}**" if not url else f"- **[{t}]({url})**"
            lines.append(head)
            meta = f"  - {date} · {src} · 政策分 {r.get('policy_score')}"
            if r.get("doc_no"):
                meta += f" · {r['doc_no']}"
            lines.append(meta)
            if r.get("also_from"):
                lines.append(f"  - 同源报道：{'、'.join(r['also_from'])}")
            if r.get("hotwords"):
                lines.append(f"  - 命中热词：{'、'.join(r['hotwords'][:8])}")
            if r.get("impact"):
                lines.append(f"  - 传导逻辑：{r['impact']}")
        lines.append("")

    lines += [
        "## 三、怎么用这份日报准备面试",
        "",
        "1. 只精读政策分 ≥ 60 的条目，其余扫标题即可。",
        "2. 每条只问自己三个问题：**谁发的 / 改了什么 / 谁受影响**。",
        "3. 把「传导逻辑」一行背下来——面试官追问的永远是「所以呢」。",
        "4. 用 `finradar quiz --board <板块>` 把当天热词对应的考点刷一遍。",
        "",
    ]
    return "\n".join(lines)


FORMAL_HTML_TPL = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
:root{{--ink:#1a1a1a;--paper:#ffffff;--rule:#c0392b;--line:#dcdcdc;--mut:#6b6b6b;--bg:#f4f2ee}}
@media (prefers-color-scheme:dark){{
 :root{{--ink:#e9e6e1;--paper:#17191c;--rule:#c96a5a;--line:#2c3036;--mut:#9a958c;--bg:#101215}}
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
 font:16px/1.95 "Noto Serif SC","Songti SC",SimSun,Georgia,serif}}
.wrap{{max-width:820px;margin:0 auto;padding:40px 26px 80px;background:var(--paper);
 box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.head{{text-align:center;border-bottom:3px double var(--rule);padding-bottom:14px;margin-bottom:6px}}
.org{{color:var(--rule);letter-spacing:.35em;font-size:12.5px}}
h1.doc-title{{font-size:26px;margin:10px 0 4px;letter-spacing:.08em}}
.doc-no{{color:var(--mut);font-size:12.5px}}
.meta{{border:1px solid var(--line);background:var(--bg);padding:12px 16px;margin:18px 0 26px;
 font-size:13.5px;line-height:1.9}}
.meta p{{margin:2px 0}}
h2{{font-size:18px;margin:30px 0 10px;padding-left:0;letter-spacing:.04em}}
h3{{font-size:15.5px;margin:20px 0 8px;color:var(--rule)}}
p{{margin:8px 0;text-align:justify}}
p.cont{{margin:2px 0 2px 1.6em;font-size:14.5px;color:#333}}
@media (prefers-color-scheme:dark){{p.cont{{color:#cfcbc2}}}}
ol,ul{{padding-left:1.6em}} li{{margin:6px 0}}
ol{{counter-reset:none}}
table{{width:100%;border-collapse:collapse;margin:12px 0;font-size:14.5px}}
th,td{{border:1px solid var(--line);padding:6px 10px;text-align:left}}
th{{background:var(--bg);font-weight:700}}
a{{color:inherit;text-decoration:none;border-bottom:1px solid var(--line)}}
a:hover{{color:var(--rule)}}
.foot{{margin-top:40px;border-top:1px solid var(--line);padding-top:12px;color:var(--mut);font-size:12.5px;text-align:center}}
@media print{{body{{background:#fff}} .wrap{{box-shadow:none;max-width:none}}}}
</style></head><body><div class="wrap">
<div class="head"><div class="org">金融政策研究</div>
<h1 class="doc-title">{title}</h1><div class="doc-no">{doc_no}</div></div>
{body}
<div class="foot">本通报由 finradar 自动编制 · 生成时间 {now}（北京时间） · 政策以官方原文为准</div>
</div></body></html>"""


def build_formal_html(
    rows: list[dict],
    title: str,
    top_hot: int,
    group_by: str,
    bucket: str,
    top_per_period: int,
    insights: list | None,
    view_rows: list[dict] | None,
    translate: str,
) -> str:
    md = build_formal_markdown(
        rows, title, top_hot, group_by, bucket, top_per_period, insights, view_rows, translate
    )
    # 先剥掉首个一级标题(模板里单独渲染)和紧随其后的区间行
    lines = md.split("\n")
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    doc_no = "内部学习参考资料"
    if lines and lines[0].strip() and not lines[0].startswith(("*", "#", "|")):
        doc_no = lines[0].strip()
        lines = lines[1:]
    body = formal_md_to_html("\n".join(lines))
    return FORMAL_HTML_TPL.format(
        title=html.escape(title), doc_no=html.escape(doc_no),
        now=now_cn().strftime("%Y-%m-%d %H:%M"), body=body,
    )


HTML_TPL = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
:root{{--bg:#fbfaf7;--fg:#1f2328;--mut:#6a7280;--line:#e6e3dc;--card:#fff;--acc:#8a5a2b}}
@media (prefers-color-scheme:dark){{:root{{--bg:#14161a;--fg:#e8e6e1;--mut:#9aa1ab;--line:#2a2e35;--card:#1b1e23;--acc:#d9a441}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}}
.wrap{{max-width:900px;margin:0 auto;padding:32px 20px 64px}}
h1{{font-size:26px;margin:0 0 6px}} .sub{{color:var(--mut);font-size:13px;margin-bottom:26px}}
h2{{font-size:18px;margin:34px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--line)}}
h3{{font-size:15px;margin:22px 0 10px;color:var(--acc)}}
.chips{{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:18px}}
.chip{{background:var(--card);border:1px solid var(--line);border-radius:99px;padding:3px 10px;font-size:12px}}
.chip b{{color:var(--acc)}}
.item{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:9px}}
.item a{{color:inherit;text-decoration:none;font-weight:600}} .item a:hover{{color:var(--acc)}}
.meta{{color:var(--mut);font-size:12px;margin-top:5px}}
.imp{{font-size:12.5px;margin-top:6px;padding-left:9px;border-left:2px solid var(--acc);color:var(--mut)}}
.score{{float:right;font-size:12px;color:var(--acc);font-weight:700}}
pre.trend{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;
 font:12px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;overflow-x:auto;white-space:pre}}
</style></head><body><div class="wrap">
<h1>{title}</h1><div class="sub">{now}</div>
{body}
</div></body></html>"""


def build_html(
    rows: list[dict],
    title: str = "金融政策日报",
    group_by: str = "issue",
    bucket: str = "month",
    top_per_period: int = 8,
    insights: list | None = None,
    view_rows: list[dict] | None = None,
    translate: str = "none",
    style: str = "formal",
) -> str:
    if style == "formal":
        return build_formal_html(
            rows, title, 15, group_by, bucket, top_per_period,
            insights, view_rows, translate,
        )
    now = now_cn().strftime("%Y-%m-%d %H:%M")
    raw_n = len(rows)
    rows = dedupe_rows(rows)
    hot = combined_hotwords(rows)
    parts = []
    if hot:
        chips = "".join(
            f'<span class="chip">{html.escape(w)} <b>{n}</b></span>'
            for w, n in hot.most_common(20)
        )
        parts.append(f"<h2>热词榜</h2><div class='chips'>{chips}</div>")

    # 媒体与外部视角（和 Markdown 版一致）
    from .views import expand_keywords, pick_views, translate_titles

    vr = view_rows if view_rows is not None else rows
    hot_words = [w for w, _ in hot.most_common(24)] if hot else []
    for kind, label in (("media", "媒体视角（权威媒体怎么报道）"),
                        ("external", "外部视角（海外机构与媒体怎么读）")):
        kw = hot_words if kind == "media" else expand_keywords(hot_words)
        items = pick_views(vr, kind, kw, top=8)
        if not items and kind == "external":
            items = pick_views(vr, kind, None, top=5)
        if not items:
            continue
        zh = translate_titles(items, backend=translate) if kind == "external" else {}
        lis = "".join(
            "<li>"
            + (
                f'<a href="{html.escape(r.get("url") or "")}" target="_blank" rel="noopener">'
                f'{html.escape(r.get("title") or "")}</a>'
                if r.get("url")
                else html.escape(r.get("title") or "")
            )
            + f'<div class="m">{(r.get("pub_date") or "")[:10]} · '
            + html.escape(r.get("source_name") or r.get("source") or "")
            + "</div>"
            + (
                f'<div class="zh">{html.escape(zh[r.get("title") or ""])}</div>'
                if zh.get(r.get("title") or "")
                else ""
            )
            + "</li>"
            for r in items
        )
        parts.append(f"<h2>{label}</h2><ul class='docs'>{lis}</ul>")

    if insights:
        from .insights import render_brief

        parts.append("<h2>专题洞察与展望</h2>")
        parts.append(
            "<div class='sub'>每条专题 = 来龙去脉 + 现状 + 各主体可能的动作（带触发条件）</div>"
        )
        for it in insights:
            parts.append(_md_to_html(render_brief(it, rows)))

    if group_by == "time":
        from .hotwords import render_trend
        from .periods import BUCKET_LABEL

        parts.append("<h2>热词演变（词 × 时段）</h2>")
        parts.append(f"<pre class='trend'>{html.escape(render_trend(rows, bucket=bucket, top=15, periods=14))}</pre>")
        parts.append(f"<h2>分{BUCKET_LABEL.get(bucket, bucket)}回顾</h2>")
        for key, items in _group_by_time(rows, bucket):
            period_hot = combined_hotwords(items).most_common(6)
            parts.append(f"<h3>{html.escape(key)}（{len(items)} 条）</h3>")
            if period_hot:
                chips = "".join(
                    f'<span class="chip">{html.escape(w)} <b>{n}</b></span>' for w, n in period_hot
                )
                parts.append(f"<div class='chips'>{chips}</div>")
            for r in items[:top_per_period]:
                t = html.escape(r.get("title") or "")
                url = html.escape(r.get("url") or "")
                title_html = (
                    f'<a href="{url}" target="_blank" rel="noopener">{t}</a>' if url else t
                )
                meta = f"{(r.get('pub_date') or '')[:10]} · {html.escape(r.get('source_name') or '')}"
                if r.get("doc_no"):
                    meta += " · " + html.escape(str(r["doc_no"]))
                if r.get("hotwords"):
                    meta += " · " + html.escape("、".join(r["hotwords"][:6]))
                imp = (
                    f"<div class='imp'>{html.escape(r['impact'])}</div>"
                    if r.get("impact")
                    else ""
                )
                parts.append(
                    f"<div class='item'><span class='score'>{r.get('policy_score')}</span>"
                    f"{title_html}<div class='meta'>{meta}</div>{imp}</div>"
                )
        sub = f"生成时间 {now}（北京时间） · 共 {len(rows)} 条"
        if raw_n != len(rows):
            sub += f"（已合并 {raw_n - len(rows)} 条跨源重复）"
        coverage = _coverage(rows)
        if coverage:
            sub += f" · 数据覆盖 {coverage}"
        sub += f" · 按时段整理（{BUCKET_LABEL.get(bucket, bucket)}）"
        return HTML_TPL.format(title=html.escape(title), now=sub, n=len(rows), body="".join(parts))

    parts.append("<h2>按板块梳理</h2>")
    for board, items in _group(rows).items():
        parts.append(f"<h3>{html.escape(board)}（{len(items)}）</h3>")
        for r in sorted(items, key=lambda x: -float(x.get("policy_score") or 0))[:25]:
            t = html.escape(r.get("title") or "")
            url = html.escape(r.get("url") or "")
            title_html = f'<a href="{url}" target="_blank" rel="noopener">{t}</a>' if url else t
            meta = f"{(r.get('pub_date') or '')[:10]} · {html.escape(r.get('source_name') or '')}"
            if r.get("doc_no"):
                meta += " · " + html.escape(str(r["doc_no"]))
            if r.get("also_from"):
                meta += " · 亦见：" + html.escape("、".join(r["also_from"]))
            if r.get("hotwords"):
                meta += " · " + html.escape("、".join(r["hotwords"][:6]))
            imp = (
                f"<div class='imp'>{html.escape(r['impact'])}</div>" if r.get("impact") else ""
            )
            parts.append(
                f"<div class='item'><span class='score'>{r.get('policy_score')}</span>"
                f"{title_html}<div class='meta'>{meta}</div>{imp}</div>"
            )
    sub = f"生成时间 {now}（北京时间） · 共 {len(rows)} 条"
    if raw_n != len(rows):
        sub += f"（已合并 {raw_n - len(rows)} 条跨源重复）"
    return HTML_TPL.format(title=html.escape(title), now=sub, n=len(rows), body="".join(parts))


def write_report(
    rows: list[dict],
    title: str = "金融政策日报",
    stem: str | None = None,
    group_by: str = "issue",
    bucket: str = "month",
    top_per_period: int = 8,
    insights: list | None = None,
    view_rows: list[dict] | None = None,
    translate: str = "none",
    style: str = "formal",
) -> dict:
    out = workdir() / "reports"
    out.mkdir(parents=True, exist_ok=True)
    stem = stem or f"report_{now_cn():%Y%m%d}"
    md_path = out / f"{stem}.md"
    html_path = out / f"{stem}.html"
    md_path.write_text(
        build_markdown(rows, title, group_by=group_by, bucket=bucket,
                       top_per_period=top_per_period, insights=insights, view_rows=view_rows,
                       translate=translate, style=style),
        encoding="utf-8",
    )
    html_path.write_text(
        build_html(rows, title, group_by=group_by, bucket=bucket,
                   top_per_period=top_per_period, insights=insights, view_rows=view_rows,
                   translate=translate, style=style),
        encoding="utf-8",
    )
    return {"markdown": str(md_path), "html": str(html_path)}


INDEX_TPL = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>金融政策通报 · 归档</title>
<style>
:root{--ink:#1a1a1a;--paper:#fff;--line:#e2ddd3;--mut:#6b6b6b;--bg:#f5f3ef;--acc:#9c3b2e}
@media (prefers-color-scheme:dark){:root{--ink:#e9e6e1;--paper:#17191c;--line:#2c3036;
 --mut:#9a958c;--bg:#101215;--acc:#d98a78}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font:16px/1.8 "Noto Serif SC","Songti SC",SimSun,Georgia,serif}
.wrap{max-width:760px;margin:0 auto;padding:44px 24px 80px}
h1{font-size:24px;margin:0 0 6px;letter-spacing:.06em}
.sub{color:var(--mut);font-size:13px;margin-bottom:28px}
.item{background:var(--paper);border:1px solid var(--line);border-radius:10px;
 padding:14px 18px;margin-bottom:10px;display:flex;flex-wrap:wrap;gap:10px;align-items:baseline}
.item .d{font-family:ui-monospace,Menlo,monospace;color:var(--acc);font-size:13.5px}
.item a{color:inherit;text-decoration:none;border-bottom:1px solid var(--line)}
.item a:hover{color:var(--acc)}
.item .n{color:var(--mut);font-size:12.5px;margin-left:auto}
.foot{margin-top:34px;color:var(--mut);font-size:12.5px;text-align:center}
</style></head><body><div class="wrap">
<h1>金融政策通报 · 归档</h1>
<div class="sub">__COUNT__ 期 · 最近更新 __NOW__（北京时间） · 按日期倒序</div>
__ITEMS__
<div class="foot">由 finradar 自动生成 · 政策以官方原文为准 · 不构成投资建议</div>
</div></body></html>"""


def write_report_index(directory: str | Path, limit: int = 60) -> Path:
    """为 docs/reports/ 生成索引页, 让 GitHub Pages 上能按日期浏览历史通报."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    htmls = sorted(d.glob("report_*.html"), reverse=True)[:limit]
    rows_html = []
    for h in htmls:
        stem = h.stem
        date = stem.replace("report_", "")
        cn = _cn_date(f"{date[:4]}-{date[4:6]}-{date[6:8]}") if len(date) == 8 else date
        md = h.with_suffix(".md")
        size = f"{h.stat().st_size // 1024} KB"
        rows_html.append(
            f"<div class='item'><span class='d'>{cn}</span>"
            f"<a href='{h.name}'>金融政策通报（{cn}）</a>"
            + (f"<a href='{md.name}' style='font-size:13px'>Markdown</a>" if md.exists() else "")
            + f"<span class='n'>{size}</span></div>"
        )
    out = d / "index.html"
    out.write_text(
        INDEX_TPL.replace("__COUNT__", str(len(htmls)))
        .replace("__NOW__", now_cn().strftime("%Y-%m-%d %H:%M"))
        .replace("__ITEMS__", "\n".join(rows_html) or "<p>还没有归档的通报。</p>"),
        encoding="utf-8",
    )
    return out
