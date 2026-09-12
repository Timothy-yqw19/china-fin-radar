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

from . import __version__
from .analysis import (
    annotate, combined_hotwords, dedupe_rows, discover_new_words, glossary_trend, write_report,
)
from .crawlers import build_all, config_source_ids, registry
from .knowledge import glossary as G
from .knowledge import qbank as Q
from .knowledge import quiz as QZ
from .storage import Store
from .utils import Fetcher, days_ago, setup_logging, workdir


# ---------------------------------------------------------------- 抓取

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
    rows = store.query(
        since=days_ago(a.days), min_score=a.min_score, keyword=a.keyword, limit=a.limit
    )
    if not rows:
        print("库里没有符合条件的数据，先跑 `finradar crawl`。")
        return 1
    paths = write_report(rows, title=a.title or f"金融政策日报（近{a.days}天）")
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

def cmd_hot(a: argparse.Namespace) -> int:
    store = Store(a.db)
    rows = store.query(since=days_ago(a.days), min_score=a.min_score, limit=5000)
    if not rows:
        print("库里没数据，先跑 `finradar crawl`。")
        return 1
    # 同一件事被多个源抓到会让词频虚高, 先合并再统计
    raw_n = len(rows)
    rows = dedupe_rows(rows)
    texts = [f"{r.get('title','')} {r.get('summary','')}" for r in rows]
    cnt = combined_hotwords(rows)
    extra = f"，已合并 {raw_n - len(rows)} 条跨源重复" if raw_n != len(rows) else ""
    print(f"\n=== 热词榜（近 {a.days} 天，{len(rows)} 条新闻，合并口径{extra}）===")
    for i, (w, n) in enumerate(cnt.most_common(a.top), 1):
        t = G.get(w)
        heat = "★" * (t.heat if t else 0)
        print(f"{i:>3}. {w:<28}{n:>4} 次   {heat}")

    if a.trend:
        print(f"\n=== 趋势（按{a.bucket}）===")
        tr = glossary_trend(rows, bucket=a.bucket)
        for key in sorted(tr)[-8:]:
            top = "、".join(f"{w}({n})" for w, n in tr[key].most_common(6))
            print(f"  {key}: {top}")

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

    s = net(sub.add_parser("crawl", help="抓取新闻/政策"))
    s.add_argument(
        "--source", nargs="*", default=["official", "flash"],
        help="official / flash / config / all，或具体 id: "
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
    s.add_argument("--days", type=int, default=1)
    s.add_argument("--min-score", type=float, default=45.0)
    s.add_argument("--keyword", default=None)
    s.add_argument("--limit", type=int, default=300)
    s.add_argument("--title", default=None)
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("stats", help="库存与刷题统计")
    s.set_defaults(func=cmd_stats)

    s = sub.add_parser("rescore", help="改完打分规则后重算库里已有条目")
    s.set_defaults(func=cmd_rescore)

    s = sub.add_parser("hot", help="热词榜 / 趋势 / 新词发现")
    s.add_argument("--days", type=int, default=30)
    s.add_argument("--min-score", type=float, default=0.0)
    s.add_argument("--top", type=int, default=30)
    s.add_argument("--trend", action="store_true")
    s.add_argument("--bucket", choices=["day", "week", "month"], default="week")
    s.add_argument("--discover", action="store_true", help="发现词库外的新提法")
    s.set_defaults(func=cmd_hot)

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
    setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
