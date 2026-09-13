"""finradar 命令行入口.

用法示例:
    finradar crawl --source official --pages 2   # 抓官方政策站点
    finradar crawl --source flash                # 抓财经快讯
    finradar report --days 3 --min-score 50      # 生成政策日报
    finradar doctor                              # 逐个数据源体检
    finradar terms -q QDII                       # 查热词
    finradar term QDII                           # 看某个词的完整解析
    finradar hot --days 30 --discover            # 热词榜 + 新词发现
    finradar quiz -n 10 --board 宏观              # 刷题
    finradar quiz --wrong                        # 只刷错题
    finradar facts                               # 拉最新宏观数据 (需 akshare)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .analysis import (
    annotate, auto_bucket, combined_hotwords, dedupe_rows, discover_new_words,
    render_trend, resolve_window, write_report,
)
from .crawlers import TRENDS, build_all, config_source_ids, registry
from .knowledge import glossary as G
from .knowledge import qbank as Q
from .knowledge import quiz as QZ
from .models import NewsItem
from .storage import Store
from .utils import Fetcher, setup_logging, workdir


# ---------------------------------------------------------------- 抓取

def _run_crawl(
    store: Store,
    fetcher: Fetcher,
    sources: list[str],
    pages: int = 1,
    min_score: float = 0.0,
    quiet: bool = False,
) -> tuple[list, int]:
    """抓取并入库, 返回 (条目, 新增条数). crawl / update 共用."""
    from .crawlers import source_base_score

    items = []
    for c in build_all(sources, pages=pages, fetcher=fetcher):
        got = c.run()
        store.log_run(c.source_id, bool(got), len(got))
        if not quiet:
            print(f"  {c.source_name:<22}{len(got):>5} 条")
        items.extend(got)
    items = annotate(items)
    for it in items:  # 来源先验分作为下限
        it.policy_score = max(it.policy_score, source_base_score(it.source))
    if min_score:
        items = [i for i in items if i.policy_score >= min_score]
    return items, store.save_news(items)


def _run_backfill(
    store: Store,
    fetcher: Fetcher,
    start: str,
    end: str,
    step: str = "year",
    max_pages: int = 3,
    queries: list[str] | None = None,
    search: str | None = None,
    page_size: int = 50,
    quiet: bool = False,
) -> tuple[list, int]:
    """按日期范围回捞政策文件库, 返回 (条目, 新增条数). backfill / update 共用."""
    from .analysis.periods import iter_periods
    from .crawlers import source_base_score
    from .crawlers.official import GovPolicyCrawler

    crawler = GovPolicyCrawler(fetcher=fetcher, pages=1)
    items: list = []
    for label, s, e in iter_periods(start, end, step):
        got = crawler.fetch_range(
            s, e, queries=queries, max_pages=max_pages,
            searchfield=search, page_size=page_size,
        )
        if not quiet:
            print(f"  {label}: {len(got)} 条")
        items.extend(got)
    items = annotate(items)
    for it in items:
        it.policy_score = max(it.policy_score, source_base_score(it.source))
    return items, store.save_news(items)


def cmd_crawl(a: argparse.Namespace) -> int:
    store = Store(a.db)
    fetcher = Fetcher(timeout=a.timeout, retries=a.retries, verify=not a.insecure)
    crawlers = build_all(a.source, pages=a.pages, fetcher=fetcher)
    if not crawlers:
        print(f"没有匹配的数据源。可用: {', '.join(registry())}")
        return 1

    all_items = []
    for c in crawlers:
        items = c.run()
        store.log_run(c.source_id, bool(items), len(items))
        all_items.extend(items)

    all_items = annotate(all_items)
    if a.min_score:
        all_items = [i for i in all_items if i.policy_score >= a.min_score]

    if a.no_save:
        for i in sorted(all_items, key=lambda x: -x.policy_score)[: a.limit]:
            print(f"[{i.policy_score:5.1f}] {i.date} {i.source_name} | {i.title}")
        return 0

    new = store.save_news(all_items)
    print(f"\n抓取 {len(all_items)} 条，入库新增 {new} 条 → {store.path}")
    st = store.stats()
    print(f"库内累计 {st['total']} 条，最新日期 {st['latest']}")
    return 0


def cmd_doctor(a: argparse.Namespace) -> int:
    """逐个数据源体检: 哪个能通、哪个要修选择器."""
    fetcher = Fetcher(timeout=a.timeout, retries=1, verify=not a.insecure)
    print(f"{'数据源':<28}{'类型':<12}{'状态':<8}{'条数':<6}示例标题")
    print("-" * 100)
    bad = 0
    for c in build_all(["all"], pages=1, fetcher=fetcher):
        items = c.run()
        ok = "✓ OK" if items else "✗ FAIL"
        bad += int(not items)
        sample = items[0].title[:36] if items else "—"
        print(f"{c.source_name:<28}{c.kind:<12}{ok:<8}{len(items):<6}{sample}")
    print("-" * 100)
    if bad:
        print(
            f"\n{bad} 个数据源没有返回数据。常见原因与处理：\n"
            "  1) 接口参数调整 → 打开对应站点，用浏览器开发者工具看 Network 里的真实请求，"
            "改 finradar/crawlers/ 下的 params\n"
            "  2) 触发 WAF/风控 → 降低频率(--pages 1)、加代理，或改用 akshare 通道 "
            "(--source akshare)\n"
            "  3) 政务站证书链不全 → 加 --insecure\n"
            "  4) 网络不可达 → 确认本机能访问该域名"
        )
    return 0


# ---------------------------------------------------------------- 报告

def cmd_update(a: argparse.Namespace) -> int:
    """一条命令更新所有平台: 首次补齐十年, 之后只抓"上次运行到现在"的新增.

    做的事(按顺序):
      1. 政策文件库回捞 —— 首次从十年前开始, 之后从上次运行日前一天开始
      2. 抓全部实时源 —— 官方站 / 快讯 / 证券时报 / 大众热榜 / AkShare
      3. 重算打分(改过规则的条目会被修正)
      4. 重建网页(专题洞察网页 + 词库刷题网页)
      5. 记录本次运行时间与新增条数到 output/state.json
    """
    from datetime import date

    from .state import describe, incremental_start, load_state, record_run, save_state
    from .utils import now_cn

    state = load_state()
    store = Store(a.db)
    fetcher = Fetcher(timeout=a.timeout, retries=a.retries, verify=not a.insecure)
    today = now_cn().date()

    start, mode = incremental_start(state, default_years=a.years)
    if a.full:
        mode = "全量"
        start = a.start or date(today.year - a.years, today.month, today.day).isoformat()
    elif a.start:
        mode = "指定起点"
        start = a.start

    print(f"上次状态：{describe(state)}")
    gap_days = (today - date.fromisoformat(start)).days
    step = "year" if gap_days > 400 else ("quarter" if gap_days > 120 else "month")
    print(f"本次模式：{mode}，回捞区间 {start} ~ {today.isoformat()}（按{step}分段）\n")

    added_total = 0
    # 1) 政策库回捞(十年/增量)
    if not a.no_backfill:
        print("① 政策文件库回捞")
        _, new = _run_backfill(
            store, fetcher, start, today.isoformat(), step=step,
            max_pages=a.max_pages, search=a.search, quiet=a.quiet,
        )
        added_total += new
        print(f"   → 新增 {new} 条\n")
    else:
        print("① 跳过政策库回捞（--no-backfill）\n")

    # 2) 全源抓取
    sources = a.source or [
        "official", "flash", "trends", "external", "media", "akshare",
    ]
    print(f"② 抓取实时源（{len(build_all(sources, pages=1, fetcher=fetcher))} 个）")
    _, new = _run_crawl(
        store, fetcher, sources, pages=a.pages, min_score=a.min_score, quiet=a.quiet
    )
    added_total += new
    print(f"   → 新增 {new} 条\n")

    # 3) 重算打分
    if not a.no_rescore:
        print("③ 重算打分")
        cmd_rescore(a)
        print()

    # 4) 重建网页
    if not a.no_site:
        print("④ 重建网页")
        from .analysis.insight_site import build_payload, render_site

        rows = store.query(limit=10**6)
        out = workdir() / "insights.html"
        out.write_text(render_site(build_payload(rows)), encoding="utf-8")
        print(f"   → {out}（{len(rows):,} 条语料）")

    # 5) 记录状态
    span = f"{start} ~ {today.isoformat()}"
    state = record_run(state, mode=mode, new_items=added_total, span=span)
    p = save_state(state)
    st = store.stats()
    print(
        f"\n⑤ 完成：本次新增 {added_total} 条，库内累计 {st['total']:,} 条，"
        f"数据覆盖 {st['earliest']} ~ {st['latest']}\n状态已记录到 {p}"
    )
    if added_total:
        print("\n下一步建议：finradar report --days 3 --min-score 55  （看这两天的政策）")
    return 0


def cmd_backfill(a: argparse.Namespace) -> int:
    """回捞历史数据.

    快讯类接口没有历史, 所以能看到"过去 1/3/5 年"的只有官方政策文件:
    中国政府网政策文件库支持按日期范围检索 (mintime/maxtime), 一年一年往回捞;
    其余政务站(外汇局/统计局/发改委/基金业协会)是列表页翻页, 用 --pages 往深里翻。
    """
    from .analysis.periods import iter_periods
    from .crawlers.official import GovPolicyCrawler

    store = Store(a.db)
    fetcher = Fetcher(timeout=a.timeout, retries=a.retries, verify=not a.insecure)
    all_items: list[NewsItem] = []

    want = set(a.source or ["gov"])
    if {"gov", "policy"} & want:
        crawler = GovPolicyCrawler(fetcher=fetcher, pages=1)
        for label, start, end in iter_periods(a.start, a.end, a.step):
            print(f"\n== {label}  {start} ~ {end}")

            def progress(q, page, got, fresh, total, cat, _label=label):  # noqa: ANN001
                totals = ", ".join(f"{k}={v.get('totalCount')}" for k, v in (cat or {}).items())
                print(f"   [{q}] 第{page}页 取到{got} 新增{fresh} 累计{total}  ({totals})")

            items = crawler.fetch_range(
                start, end,
                queries=a.queries,
                max_pages=a.max_pages,
                searchfield=a.search,
                page_size=a.page_size,
                progress=progress,
            )
            print(f"   → {label} 共 {len(items)} 条")
            all_items.extend(items)

    # 列表型源: 按页码深度回捞(它们看不到日期范围, 只能一页页往前翻)
    list_sources = sorted(s for s in want if s not in ("gov", "policy"))
    if list_sources:
        for c in build_all(list_sources, pages=a.pages, fetcher=fetcher):
            items = c.run()
            print(f"\n== {c.source_name}（翻到第 {a.pages} 页）: {len(items)} 条")
            all_items.extend(items)

    if not all_items:
        print("没有回捞到数据。检查 --from/--to 与 --source。")
        return 1

    items = annotate(all_items)
    from .crawlers import source_base_score

    for it in items:  # 回捞条目也要吃到来源先验分下限, 与 crawl 一致
        it.policy_score = max(it.policy_score, source_base_score(it.source))
    new = store.save_news(items)
    dates = sorted(i.date for i in items if i.date)
    print(
        f"\n回捞 {len(items)} 条，入库新增 {new} 条（去重后）\n"
        f"日期范围 {dates[0]} ~ {dates[-1]} → {store.path}"
    )
    st = store.stats()
    print(f"库内累计 {st['total']} 条，最早 {st['earliest']}，最新 {st['latest']}")
    return 0


def cmd_rescore(a: argparse.Namespace) -> int:
    """改完 config/keywords.yaml 后, 把库里已有条目重新打一遍分 (不用重抓)."""
    from .crawlers import source_base_score
    from .models import NewsItem

    store = Store(a.db)
    rows = store.query(limit=10**7)
    if not rows:
        print("库里没数据，先跑 `finradar crawl`。")
        return 1
    items = [
        NewsItem(
            source=r.get("source") or "",
            source_name=r.get("source_name") or "",
            title=r.get("title") or "",
            url=r.get("url") or "",
            published_at=r.get("published_at") or "",
            summary=r.get("summary") or "",
            channel=r.get("channel") or "",
            doc_no=r.get("doc_no") or "",
        )
        for r in rows
    ]
    items = annotate(items)
    for it in items:  # 还原来源先验分下限
        it.policy_score = max(it.policy_score, source_base_score(it.source))
    n = store.update_analysis(items)
    dist: dict[int, int] = {}
    for it in items:
        b = int(it.policy_score // 10) * 10
        dist[b] = dist.get(b, 0) + 1
    print(f"已重算 {n} 条。分数分布：")
    for b in sorted(dist, reverse=True):
        print(f"  {b:>3}-{b + 9:<3}{dist[b]:>6} 条")
    return 0


def cmd_report(a: argparse.Namespace) -> int:
    store = Store(a.db)
    since, label, span = resolve_window(a.window, a.days)
    group_by = a.group_by
    if group_by == "auto":
        # 长窗口按时段回顾, 短窗口按议题梳理
        group_by = "time" if (span is None or span >= 180) else "issue"
    bucket = a.bucket
    if bucket == "auto":
        bucket = auto_bucket(span)
    rows = store.query(
        since=since, min_score=a.min_score, keyword=a.keyword, limit=a.limit,
        exclude_sources=None if a.include_trends else TRENDS,
    )
    if not rows:
        print(f"库里没有符合条件的 {label} 数据，先跑 `finradar crawl`（历史窗口需要 `finradar backfill`）。")
        return 1
    title = a.title or (
        f"金融政策回顾（{label}）" if group_by == "time" else f"金融政策日报（{label}）"
    )
    insights = None
    if a.insights:
        from .analysis.insights import match_insights
        from .knowledge import insights as I

        # 用"窗口内的热词 + 高分标题"去找最相关的专题, 而不是硬编码
        blob = " ".join([w for w, _ in combined_hotwords(rows).most_common(40)])
        blob += " " + " ".join(
            (r.get("title") or "") for r in sorted(
                rows, key=lambda x: -float(x.get("policy_score") or 0)
            )[:40]
        )
        insights = match_insights(blob, I.load_insights())[: a.insight_top]
        if insights:
            print("附带专题洞察：" + "；".join(i.topic for i in insights))
        else:
            print("（窗口内没有匹配的专题，报告不附洞察）")
    paths = write_report(
        rows, title=title, group_by=group_by, bucket=bucket,
        top_per_period=a.per_period, insights=insights,
        # 媒体与外部视角单独取一遍(不套 min-score): 它们是给正文做补充说明的
        view_rows=store.query(since=since, min_score=0.0, limit=10**6),
    )
    print(f"Markdown: {paths['markdown']}\nHTML:     {paths['html']}")
    return 0


def cmd_stats(a: argparse.Namespace) -> int:
    store = Store(a.db)
    st = store.stats()
    print(f"新闻总数: {st['total']}   最新日期: {st['latest']}")
    for k, v in st["by_source"].items():
        print(f"  {k:<30}{v}")
    qs = store.quiz_stats()
    print(f"\n刷题记录: 作答 {qs['answered']} 题，正确率 {qs['accuracy']}%")
    for b in qs["by_board"]:
        acc = round((b["c"] or 0) / b["n"] * 100, 1) if b["n"] else 0
        print(f"  {b['board']:<24}{b['n']:>4} 题  正确率 {acc}%")
    return 0


# ---------------------------------------------------------------- 热词

def cmd_mine(a: argparse.Namespace) -> int:
    """从语料里批量挖"词库还没有"的提法, 可选导出成词条草稿.

    规模化的关键不是放宽过滤, 而是**语料要够大**: 政策文件库回捞到 2021 年、
    加上证券时报这类财经媒体, 一次能挖出几百个候选; 再加 --trends 把
    大众热榜(头条/抖音/百度)里今天的财经话题一并列出来。
    """
    import yaml as _yaml

    from .analysis.dossiers import mine_candidates
    from .analysis.trends import finance_topics
    from .knowledge import insights as I
    from .utils import days_ago

    store = Store(a.db)
    rows = store.query(
        limit=10**6,
        exclude_sources=None if a.include_trends else TRENDS,
    )
    terms = G.load_glossary()
    cands = mine_candidates(
        rows,
        glossary_names=[t.term for t in terms],
        insight_keywords=[k for i in I.load_insights() for k in i.keywords],
        top=a.top,
        min_count=a.min_count,
        min_score=a.min_score,
        max_df_ratio=a.max_df_ratio,
    )
    print(f"=== 候选提法（语料 {len(rows):,} 条，挖出 {len(cands)} 个）===")
    print(f"{'#':>3} {'候选':<20}{'次数':>5} {'首见':>6}  最近三年")
    for i, c in enumerate(cands, 1):
        trend = "/".join(f"{y['year']}:{y['n']}" for y in c["by_year"][-3:])
        print(f"{i:>3} {c['word']:<20}{c['n']:>5} {c['first_year']:>6}  {trend}")

    # 大众热榜里的财经话题（另一个视角：政策提法 vs 大众说法）
    hot_topics: list[dict] = []
    if a.trends:
        trows = [r for r in store.query(since=days_ago(3), limit=100000)
                 if (r.get("source") or "") in TRENDS]
        if trows:
            hot_topics = finance_topics(trows)[: a.top]
            print(f"\n=== 热榜财经话题（近 3 天 {len(trows)} 条热榜）===")
            for i, t in enumerate(hot_topics, 1):
                hv = f"热度 {t['hot']:,}" if t["hot"] else ""
                print(f"{i:>3} [{ '、'.join(t['platforms']) }] {t['topic']}  {hv}")
        else:
            print("\n（没有热榜数据，先跑 finradar crawl --source trends）")

    if a.out:
        draft = {
            "category": "待分类",
            "terms": [
                {
                    "id": f"draft-{i:03d}",
                    "term": c["word"],
                    "heat": 3,
                    "era": c["first_year"],
                    "source": f"自动挖掘，待补（近三年 {c['by_year'][-3:] if c['by_year'] else []}）",
                    "definition": "TODO 待补：这个词是什么",
                    "why_hot": "TODO 待补：为什么现在热",
                    "exam_points": ["TODO 待补：考点一"],
                    "interview_answer": "TODO 待补：60-90 秒口语化答法",
                }
                for i, c in enumerate(cands, 1)
            ],
        }
        p = Path(a.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        head = (
            "# 自动挖掘的候选词草稿 —— 挑有价值的补完字段, 再挪进 finradar/data/glossary/\n"
            "# 字段要求见 docs/GLOSSARY_SCHEMA.md；pytest -q 会校验字段完整性。\n"
            "# 挑选建议：优先「近三年才高频出现」的词（老词早就该会了）。\n"
        )
        body = _yaml.safe_dump(draft, allow_unicode=True, sort_keys=False)
        p.write_text(head + body, encoding="utf-8")
        print(f"\n草稿已写出 {p}（{len(draft['terms'])} 个待补词条）")
    return 0


def cmd_trends(a: argparse.Namespace) -> int:
    """看大众热榜（头条/抖音/百度）里今天有哪些财经话题在火."""
    from .analysis.trends import FINANCE_HINTS, finance_topics, hot_value
    from .crawlers import TRENDS

    store = Store(a.db)
    since, label, _ = resolve_window(a.window or "1d", a.days)
    rows = store.query(since=since, limit=10**6)
    rows = [r for r in rows if (r.get("source") or "") in TRENDS]
    if not rows:
        print(
            f"库里没有 {label} 的热榜数据。先抓一次：\n"
            "  finradar crawl --source trends\n"
            "（热榜是实时榜，抓完直接看当天的就行）"
        )
        return 1
    platforms = {}
    for r in rows:
        platforms[r.get("source")] = r.get("source_name") or r.get("source")

    fin = [r for r in rows if any(h in (r.get("title") or "") for h in FINANCE_HINTS)]
    topics = finance_topics(rows, platforms)
    print(f"\n=== {label} 热榜财经话题（{len(rows)} 条热榜里挑出 {len(fin)} 条）===")
    if not topics:
        print("（今天热榜上没有明显的财经话题）")
    for i, t in enumerate(topics[: a.top], 1):
        hot = f"热度 {t['hot']:,}" if t["hot"] else ""
        print(f"{i:>3}. [{ '、'.join(t['platforms']) }] {t['topic']}")
        if len(t["titles"]) > 1:
            print("     其他说法：" + " / ".join(t["titles"][1:3]))
        if hot:
            print(f"     {hot}")

    if a.all:
        print("\n=== 全部热榜条目（含娱乐体育，按平台）===")
        for src in sorted({r["source"] for r in rows}):
            sub = sorted([r for r in rows if r["source"] == src], key=lambda x: -hot_value(x))
            print(f"\n-- {platforms.get(src, src)}（{len(sub)} 条）")
            for r in sub[: a.top]:
                print(f"   {r['title']}")
    return 0


def cmd_features(a: argparse.Namespace) -> int:
    """列出已经有成篇报道的专题."""
    from .analysis.features import build_feature
    from .knowledge import insights as I

    items = [it for it in I.load_insights() if it.feature]
    if not items:
        print("还没有写正文的专题。在 finradar/data/insights/*.yaml 里补 feature.lead 与 feature.sections。")
        return 1
    print(f"共 {len(items)} 篇专题报道（看全文： finradar feature <专题名>）\n")
    for it in items:
        art = build_feature(it)
        print(f"■ {art['topic']}")
        print(f"   {art['lead'][:80]}…")
        print(f"   段落: {' / '.join(s.get('title','') for s in art['sections'])}")
        print()
    if a.out:
        return cmd_feature_bundle(a)
    return 0


def cmd_feature_bundle(a: argparse.Namespace) -> int:
    """把全部报道合并导出成一个 Markdown 文件."""
    from .analysis.features import build_feature, render_feature_markdown
    from .knowledge import insights as I

    rows = Store(a.db).query(limit=10**6)
    parts = [f"# 金融政策专题报道（{len([i for i in I.load_insights() if i.feature])} 篇）", ""]
    for it in I.load_insights():
        if not it.feature:
            continue
        parts.append(render_feature_markdown(build_feature(it, rows, min_score=a.min_score)))
        parts.append("\n---\n")
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"已导出 {out}")
    return 0


def cmd_feature(a: argparse.Namespace) -> int:
    """输出一篇成篇的专题报道（Markdown，或导出网页）."""
    from .analysis.features import build_feature, render_feature_markdown
    from .knowledge import insights as I

    it = I.get(a.name)
    if not it:
        print(f"没找到「{a.name}」。用 finradar insights 看全部专题。")
        return 1
    if not it.feature:
        print(f"「{it.topic}」还没有写正文。结构化版本用 finradar insight {a.name}")
        return 1
    store = Store(a.db)
    # 报道要看整条线, 默认不设窗口; 只有显式给了 --window/--days 才收窄
    since = resolve_window(a.window, a.days)[0] if (a.window or a.days) else None
    rows = store.query(since=since, min_score=a.min_score, limit=10**6)
    art = build_feature(it, rows, min_score=max(30.0, a.min_score))
    if a.html:
        from .analysis.insight_site import build_payload, render_site
        from .knowledge import glossary as _G

        related = set(it.related_terms)
        terms = [t for t in _G.load_glossary() if t.term in related]
        payload = build_payload(
            store.query(limit=10**6), insights=[it], terms=terms,
            candidates=False, features=True, min_score=max(30.0, a.min_score),
        )
        out = Path(a.out or (workdir() / "features" / f"{it.id}.html"))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_site(payload), encoding="utf-8")
        print(f"已生成 {out}")
        return 0
    text = render_feature_markdown(art)
    if a.out:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"已写出 {out}")
    else:
        print(text)
    return 0


def cmd_insights(a: argparse.Namespace) -> int:
    """列出所有专题洞察."""
    from .knowledge import insights as I

    items = I.search(q=a.query or "", actor=a.actor, category=a.category)
    if not items:
        print("没有匹配的专题。可用分类：" + " / ".join(I.categories()))
        return 1
    print(f"共 {len(items)} 个专题（看详情： finradar insight <专题名或关键词>）\n")
    for it in items:
        print(f"■ {it.topic}")
        print(f"   id: {it.id} | 分类: {it.category} | 主体: {'/'.join(it.actors)}")
        if it.thesis:
            print(f"   核心判断: {it.thesis}")
        print(f"   展望条数: {len(it.outlook)} | 关联热词: {'、'.join(it.related_terms[:3])}")
        print()
    return 0


def cmd_insight(a: argparse.Namespace) -> int:
    """渲染单个专题: 脉络(人工 + 自动聚合) / 现状 / 各主体可能的动作 / 观察信号."""
    from .analysis.insights import render_insight
    from .knowledge import insights as I

    it = I.get(a.name)
    if not it:
        print(f"没找到「{a.name}」。用 finradar insights 看全部专题。")
        return 1
    rows: list[dict] = []
    if not a.no_news:
        store = Store(a.db)
        # 专题要看整条线, 默认不设窗口
        since = resolve_window(a.window, a.days)[0] if (a.window or a.days) else None
        rows = store.query(since=since, min_score=a.min_score, limit=200000)
    if a.html:
        # 单条专题也出一个网页(含它的关联热词档案), 方便发给别人或手机上翻
        from .analysis.insight_site import build_payload, render_site
        from .knowledge import glossary as _G

        related = set(it.related_terms)
        terms = [t for t in _G.load_glossary() if t.term in related]
        payload = build_payload(
            Store(a.db).query(limit=10**6),
            insights=[it],
            terms=terms,
            candidates=False,
            min_score=max(30.0, a.min_score),
        )
        out = a.out or str(workdir() / "insights" / f"{it.id}.html")
        p = Path(out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render_site(payload), encoding="utf-8")
        print(f"已生成 {p}（专题 1 条 / 关联名词档案 {len(terms)} 条）")
        return 0
    print(render_insight(it, rows, per_year=a.per_year, recent=a.recent))
    return 0


def cmd_hot(a: argparse.Namespace) -> int:
    store = Store(a.db)
    since, label, span = resolve_window(a.window, a.days)
    bucket = a.bucket if a.bucket != "auto" else auto_bucket(span)
    rows = store.query(
        since=since, min_score=a.min_score, limit=200000,
        exclude_sources=None if a.include_trends else TRENDS,
    )
    if not rows:
        print(f"库里没有 {label} 的数据，先跑 `finradar crawl`（历史窗口需要 `finradar backfill`）。")
        return 1
    # 同一件事被多个源抓到会让词频虚高, 先合并再统计
    raw_n = len(rows)
    rows = dedupe_rows(rows)
    texts = [f"{r.get('title','')} {r.get('summary','')}" for r in rows]
    cnt = combined_hotwords(rows)
    extra = f"，已合并 {raw_n - len(rows)} 条跨源重复" if raw_n != len(rows) else ""
    dates = sorted((r.get("pub_date") or "")[:10] for r in rows if (r.get("pub_date") or ""))
    cover = f"，数据覆盖 {dates[0]} ~ {dates[-1]}" if dates else ""
    print(f"\n=== 热词榜（{label}，{len(rows)} 条新闻，合并口径{extra}{cover}）===")
    for i, (w, n) in enumerate(cnt.most_common(a.top), 1):
        t = G.get(w)
        heat = "★" * (t.heat if t else 0)
        print(f"{i:>3}. {w:<28}{n:>4} 次   {heat}")

    if a.trend:
        from .analysis.periods import BUCKET_LABEL

        print(f"\n=== 演变（按{BUCKET_LABEL.get(bucket, bucket)}）===")
        print(render_trend(rows, bucket=bucket, top=min(a.top, 15), periods=a.periods))

    if a.discover:
        print("\n=== 新词发现（词库里还没有、但反复出现的提法）===")
        for w, n in discover_new_words(texts, top=a.top):
            print(f"  {w:<24}{n} 次")
        print("\n确认有价值后，补进 finradar/data/glossary/*.yaml")
    return 0


def cmd_terms(a: argparse.Namespace) -> int:
    res = G.search(q=a.query or "", category=a.category, institution=a.institution, min_heat=a.heat)
    if not res:
        print("没有匹配的词条。可用分类：" + " / ".join(G.categories()))
        return 1
    print(f"共 {len(res)} 条\n")
    print(f"{'热度':<8}{'词条':<34}{'分类':<22}适用")
    print("-" * 96)
    for t in res:
        print(
            f"{'★' * t.heat:<8}{t.term[:32]:<34}{t.category[:20]:<22}"
            f"{'/'.join(t.institutions)}"
        )
    print("\n看详解： finradar term <词条>")
    return 0


def cmd_term(a: argparse.Namespace) -> int:
    t = G.get(a.name)
    if not t:
        print(f"没找到「{a.name}」。试试 finradar terms -q {a.name}")
        return 1
    print(G.render_term(t))
    return 0


# ---------------------------------------------------------------- 题库

def cmd_boards(a: argparse.Namespace) -> int:
    print("题库板块：")
    for b in Q.boards():
        n = len(Q.select(board=b))
        print(f"  {b:<24}{n} 题")
    print("\n热词库分类：")
    for c in G.categories():
        n = len(G.search(category=c))
        print(f"  {c:<24}{n} 词")
    return 0


def cmd_quiz(a: argparse.Namespace) -> int:
    QZ.run_quiz(
        n=a.number,
        board=a.board,
        institution=a.institution,
        qtype=a.type,
        wrong_only=a.wrong,
        store=Store(a.db),
        seed=a.seed,
    )
    return 0


def cmd_show(a: argparse.Namespace) -> int:
    qs = Q.select(board=a.board, institution=a.institution, qtype=a.type)
    if not qs:
        print("没有匹配的题目。")
        return 1
    for q in qs[: a.number]:
        print(Q.render_question(q))
    return 0


# ---------------------------------------------------------------- 宏观数据

def cmd_facts(a: argparse.Namespace) -> int:
    from .crawlers.ak_source import fetch_macro

    data = fetch_macro(a.keys or None, tail=a.tail)
    if not data:
        print("没拿到数据。请先 `pip install akshare`，并确认网络可达。")
        return 1
    for k, v in data.items():
        as_of = v.get("as_of") or ""
        head = f"\n=== {v['label']} ({k})" + (f" · 最新 {as_of}" if as_of else "") + " ==="
        print(head)
        print(v["df"].to_string(index=False))
    return 0


# ---------------------------------------------------------------- 导出

def cmd_export(a: argparse.Namespace) -> int:
    out = workdir() / "export"
    out.mkdir(parents=True, exist_ok=True)
    if a.what == "glossary":
        data = [t.to_dict() for t in G.load_glossary()]
        p = out / "glossary.json"
    else:
        data = [q.to_dict() for q in Q.load_questions()]
        p = out / "questions.json"
    # YAML 里形如 2026-06-30 的值会被解析成 date 对象, 需要 default=str 兜底
    p.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(f"已导出 {len(data)} 条 → {p}")
    return 0


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="finradar",
        description="中国金融政策雷达 + 券商/公募/银行秋招知识库",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("-v", "--verbose", action="store_true", help="打印调试日志")
    p.add_argument("--version", action="version", version=f"finradar {__version__}")
    p.add_argument("--db", default=None, help="SQLite 路径, 默认 output/finradar.db")
    sub = p.add_subparsers(dest="cmd", required=True)

    def net(sp):  # 公共网络参数
        sp.add_argument("--timeout", type=int, default=20)
        sp.add_argument("--retries", type=int, default=3)
        sp.add_argument("--insecure", action="store_true", help="跳过 TLS 校验(部分政务站)")
        return sp

    s = net(sub.add_parser("update", help="一条命令更新全部（首次补齐十年，之后增量）"))
    s.add_argument("--years", type=int, default=10, help="首次运行时往回补多少年")
    s.add_argument("--start", default=None, help="手动指定回捞起点 YYYY-MM-DD")
    s.add_argument("--full", action="store_true", help="强制全量（忽略上次运行时间）")
    s.add_argument("--pages", type=int, default=1, help="实时源翻页数")
    s.add_argument("--max-pages", type=int, default=3, help="政策库每个时段最多翻几页")
    s.add_argument("--search", choices=["title", "fulltext"], default="fulltext")
    s.add_argument("--min-score", type=float, default=0.0)
    s.add_argument("--source", nargs="*", default=None, help="默认全部实时源")
    s.add_argument("--no-backfill", action="store_true", help="跳过政策库历史回捞")
    s.add_argument("--no-rescore", action="store_true", help="跳过重算打分")
    s.add_argument("--no-site", action="store_true", help="跳过重建网页")
    s.add_argument("--quiet", action="store_true", help="不逐源打印")
    s.set_defaults(func=cmd_update)

    s = net(sub.add_parser("crawl", help="抓取新闻/政策"))
    s.add_argument(
        "--source", nargs="*", default=["official", "flash"],
        help="official / flash / trends / config / all，或具体 id: "
        + " ".join([*registry(), *config_source_ids()]),
    )
    s.add_argument("--pages", type=int, default=1)
    s.add_argument("--min-score", type=float, default=0.0, help="只保留政策分≥该值的条目")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--no-save", action="store_true", help="只打印不入库")
    s.set_defaults(func=cmd_crawl)

    s = net(sub.add_parser("doctor", help="数据源体检"))
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("report", help="生成政策日报 (Markdown + HTML)")
    s.add_argument("--days", type=int, default=None, help="按天数取窗口（默认 30）")
    s.add_argument(
        "--window", default=None,
        help="窗口预设: 3m / 6m / 1y / 3y / 5y / all（优先于 --days）",
    )
    s.add_argument(
        "--group-by", choices=["auto", "issue", "time"], default="auto",
        help="auto: 半年以上按时段回顾，否则按议题梳理",
    )
    s.add_argument(
        "--bucket", choices=["auto", "day", "week", "month", "quarter", "year"],
        default="auto", help="时段粒度（--group-by time 时生效）",
    )
    s.add_argument("--per-period", type=int, default=8, help="每个时段展示几条")
    s.add_argument(
        "--insights", action="store_true",
        help="附带专题洞察与展望（脉络 / 现状 / 各主体可能的动作）",
    )
    s.add_argument("--insight-top", type=int, default=3, help="最多附几个专题")
    s.add_argument("--include-trends", action="store_true", help="把大众热榜条目也算进来（默认排除）")
    s.add_argument("--min-score", type=float, default=45.0)
    s.add_argument("--keyword", default=None)
    s.add_argument("--limit", type=int, default=5000)
    s.add_argument("--title", default=None)
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("stats", help="库存与刷题统计")
    s.set_defaults(func=cmd_stats)

    s = net(sub.add_parser("backfill", help="回捞历史（政策文件库按日期范围，其余源按页码）"))
    s.add_argument("--from", dest="start", default="2021-01-01", help="起始日期 YYYY-MM-DD")
    s.add_argument("--to", dest="end", default=None, help="结束日期，默认今天（北京时间）")
    s.add_argument("--step", choices=["year", "quarter", "month"], default="year")
    s.add_argument("--max-pages", type=int, default=4, help="每个关键词每时段最多翻几页")
    s.add_argument("--page-size", type=int, default=50, help="每页条数（政策库最多 50）")
    s.add_argument("--queries", nargs="*", default=None, help="政策库检索词，默认 金融/货币政策/资本市场")
    s.add_argument(
        "--search", choices=["title", "fulltext"], default="fulltext",
        help="title 只匹配标题（精度高），fulltext 全文匹配（覆盖广）",
    )
    s.add_argument(
        "--source", nargs="*", default=["gov"],
        help="gov（政策文件库，按日期回捞）/ 列表型源 id（如 safe stats ndrc amac mof，按页码回捞）",
    )
    s.add_argument("--pages", type=int, default=8, help="列表型源翻到第几页")
    s.set_defaults(func=cmd_backfill)

    s = sub.add_parser("rescore", help="改完打分规则后重算库里已有条目")
    s.set_defaults(func=cmd_rescore)

    s = sub.add_parser("hot", help="热词榜 / 趋势 / 新词发现")
    s.add_argument("--days", type=int, default=None)
    s.add_argument(
        "--window", default=None,
        help="窗口预设: 3m / 6m / 1y / 3y / 5y / all（优先于 --days）",
    )
    s.add_argument("--min-score", type=float, default=0.0)
    s.add_argument("--top", type=int, default=30)
    s.add_argument("--periods", type=int, default=14, help="演变矩阵最多显示几个时段")
    s.add_argument("--trend", action="store_true", help="显示「词 × 时段」演变矩阵")
    s.add_argument(
        "--bucket", choices=["auto", "day", "week", "month", "quarter", "year"],
        default="auto", help="时段粒度（不指定时按窗口自动选）",
    )
    s.add_argument("--discover", action="store_true", help="发现词库外的新提法")
    s.add_argument("--include-trends", action="store_true", help="把大众热榜条目也算进来（默认排除）")
    s.set_defaults(func=cmd_hot)

    s = sub.add_parser("trends", help="大众热榜里的财经话题（头条/抖音/百度）")
    s.add_argument("--window", default="1d", help="时间窗口，默认当天（1d/3d/1w…）")
    s.add_argument("--days", type=int, default=None)
    s.add_argument("--top", type=int, default=25)
    s.add_argument("--all", action="store_true", help="连娱乐体育一起看（按平台列出）")
    s.set_defaults(func=cmd_trends)

    s = sub.add_parser("mine", help="批量挖掘词库还没有的提法（可导出词条草稿）")
    s.add_argument("--top", type=int, default=200, help="最多输出多少个候选")
    s.add_argument("--min-count", type=int, default=3, help="至少出现在几篇里")
    s.add_argument("--min-score", type=float, default=40.0, help="语料的最低政策分")
    s.add_argument("--max-df-ratio", type=float, default=0.08, help="频率上限（过滤通用词）")
    s.add_argument("--trends", action="store_true", help="同时列出热榜里的财经话题")
    s.add_argument("--include-trends", action="store_true", help="把热榜条目算进语料")
    s.add_argument("--out", default=None, help="导出词条草稿 YAML 的路径")
    s.set_defaults(func=cmd_mine)

    s = sub.add_parser("insights", help="列出专题洞察（脉络 / 现状 / 未来动作）")
    s.add_argument("-q", "--query", default="", help="按关键词筛选")
    s.add_argument("--actor", default=None, help="按主体筛选，如 公募 / 券商 / 银行 / 监管")
    s.add_argument("--category", default=None, help="按分类筛选")
    s.add_argument("--out", default=None, help="同时把全部报道合并导出成 Markdown 文件")
    s.set_defaults(func=cmd_insights)

    s = sub.add_parser("features", help="列出已写好的专题报道")
    s.add_argument("--out", default=None, help="合并导出成 Markdown 文件")
    s.add_argument("--min-score", type=float, default=30.0, help="关联文件的最低政策分")
    s.add_argument("--db", default=None)
    s.set_defaults(func=cmd_features)

    s = sub.add_parser("feature", help="读一篇成篇的专题报道")
    s.add_argument("name", help="专题名或关键词，如 QDII / 公募 / 息差")
    s.add_argument("--window", default=None, help="关联数据的时间窗口（默认全部）")
    s.add_argument("--days", type=int, default=None)
    s.add_argument("--min-score", type=float, default=0.0)
    s.add_argument("--html", action="store_true", help="导出成网页（含关联名词档案）")
    s.add_argument("--out", default=None, help="输出路径")
    s.set_defaults(func=cmd_feature)

    s = sub.add_parser("insight", help="查看某个专题的完整洞察与展望")
    s.add_argument("name", help="专题名或关键词，如 QDII / 公募 / 息差")
    s.add_argument("--window", default=None, help="关联新闻的窗口（默认全部）")
    s.add_argument("--days", type=int, default=None)
    s.add_argument("--min-score", type=float, default=0.0)
    s.add_argument("--per-year", type=int, default=3, help="自动脉络里每年展示几条")
    s.add_argument("--recent", type=int, default=5, help="展示几条最新动态")
    s.add_argument("--no-news", action="store_true", help="只看人工整理的部分，不关联库内文件")
    s.add_argument("--html", action="store_true", help="导出这一条专题的网页（含关联名词档案）")
    s.add_argument("--out", default=None, help="网页输出路径，默认 output/insights/<id>.html")
    s.set_defaults(func=cmd_insight)

    s = sub.add_parser("terms", help="检索热词库")
    s.add_argument("-q", "--query", default="")
    s.add_argument("--category", default=None)
    s.add_argument("--institution", default=None, choices=["券商", "公募", "银行"])
    s.add_argument("--heat", type=int, default=0, help="最低热度 1-5")
    s.set_defaults(func=cmd_terms)

    s = sub.add_parser("term", help="查看某个热词的完整解析")
    s.add_argument("name")
    s.set_defaults(func=cmd_term)

    s = sub.add_parser("boards", help="列出题库板块与词库分类")
    s.set_defaults(func=cmd_boards)

    s = sub.add_parser("quiz", help="刷题（交互式）")
    s.add_argument("-n", "--number", type=int, default=10)
    s.add_argument("--board", default=None)
    s.add_argument("--institution", default=None, choices=["券商", "公募", "银行"])
    s.add_argument("--type", default=None, choices=["single", "multi", "short", "case"])
    s.add_argument("--wrong", action="store_true", help="只刷错题本")
    s.add_argument("--seed", type=int, default=None)
    s.set_defaults(func=cmd_quiz)

    s = sub.add_parser("show", help="直接看题目和答案（不交互）")
    s.add_argument("-n", "--number", type=int, default=5)
    s.add_argument("--board", default=None)
    s.add_argument("--institution", default=None, choices=["券商", "公募", "银行"])
    s.add_argument("--type", default=None, choices=["single", "multi", "short", "case"])
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("facts", help="拉取最新宏观数据 (LPR/M2/CPI/社融/国债收益率…)")
    s.add_argument(
        "--keys", nargs="*", default=None,
        help="默认全部: lpr money_supply cpi ppi gdp pmi shibor shrzgm credit "
        "leverage bond_rate bond_curve",
    )
    s.add_argument("--tail", type=int, default=8)
    s.set_defaults(func=cmd_facts)

    s = sub.add_parser("export", help="导出词库/题库为 JSON")
    s.add_argument("what", choices=["glossary", "questions"])
    s.set_defaults(func=cmd_export)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "end", None) is None and args.cmd == "backfill":
        from .utils import now_cn

        args.end = now_cn().date().isoformat()
    setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
