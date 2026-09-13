"""外部视角: 权威媒体怎么报道、海外机构怎么解读.

定位: 同一条政策, 官方原文、权威媒体、海外解读三者放在一起, 才算"全面解释"。
所以报告与专题里都会带上这两类内容:

  * media    —— 新华社、三大证券报、证券时报、政府网要闻
  * external —— Google News 聚合的海外媒体(路透/彭博/南华早报…)、美联储、世界银行、Rhodium Group

外部条目噪音大(什么话题都有), 所以统一按"是否命中当前窗口的热词"来筛,
筛不出来就不显示 —— 宁可少, 不要凑。
"""

from __future__ import annotations

from ..crawlers import EXTERNAL, media_source_ids

# 中文热词 → 英文说法。海外报道是英文的, 不做这层映射就永远匹配不上。
# 只收"一定会用到"的对应词, 不求全。
CN2EN: dict[str, list[str]] = {
    "货币政策": ["monetary policy", "PBOC", "central bank"],
    "适度宽松": ["easing", "accommodative", "dovish"],
    "降准": ["reserve requirement", "RRR cut"],
    "降息": ["rate cut", "interest rate cut", "lower rates"],
    "逆回购": ["reverse repo"],
    "买断式逆回购": ["outright reverse repo"],
    "国债买卖": ["government bond trading", "bond purchases"],
    "人民币": ["yuan", "renminbi", "RMB", "CNY"],
    "汇率": ["exchange rate", "currency", "FX"],
    "资本市场": ["capital market", "stock market", "equities"],
    "股市": ["stock market", "shares", "equities"],
    "中长期资金": ["long-term funds", "institutional investors"],
    "险资": ["insurance funds", "insurers"],
    "公募基金": ["mutual fund", "fund management"],
    "债券": ["bond", "debt"],
    "科创债": ["tech innovation bond", "sci-tech bond"],
    "房地产": ["property", "real estate", "housing"],
    "地方债": ["local government debt", "LGFV", "local bonds"],
    "化债": ["debt swap", "debt restructuring", "hidden debt"],
    "银行": ["bank", "lender"],
    "保险": ["insurance"],
    "消费": ["consumption", "consumer spending"],
    "出口": ["export"],
    "关税": ["tariff"],
    "外资": ["foreign investment", "foreign capital", "inflows"],
    "QFII": ["QFII"],
    "QDII": ["QDII"],
    "互联互通": ["Stock Connect", "Bond Connect", "Swap Connect"],
    "注册制": ["registration-based IPO", "IPO reform"],
    "十五五": ["15th Five-Year Plan", "five-year plan"],
    "金融强国": ["financial powerhouse", "financial power"],
    "美联储": ["Federal Reserve", "Fed"],
    "绿色金融": ["green finance"],
    "数字人民币": ["digital yuan", "e-CNY"],
    "私募": ["private equity", "private fund"],
    "券商": ["brokerage", "securities firm"],
    "政策": ["policy", "Beijing"],
    "刺激": ["stimulus"],
    "GDP": ["GDP", "growth"],
    "通胀": ["inflation"],
}


def expand_keywords(keywords: list[str]) -> list[str]:
    """把中文热词扩展成"中文 + 对应英文", 供匹配英文报道用."""
    out = [k for k in keywords if k]
    for k in keywords:
        out.extend(CN2EN.get(k, []))
    return list(dict.fromkeys(out))


def _blob(r: dict) -> str:
    return f"{r.get('title') or ''} {r.get('summary') or ''}"


def pick_views(
    rows: list[dict],
    kind: str,
    keywords: list[str] | None = None,
    top: int = 8,
    min_hits: int = 1,
) -> list[dict]:
    """挑出某一类(media/external)的条目, 可选按关键词过滤."""
    ids = media_source_ids() if kind == "media" else EXTERNAL
    pool = [r for r in rows if (r.get("source") or "") in ids]
    if keywords:
        keys = [k for k in keywords if k]
        filtered = []
        for r in pool:
            hits = sum(1 for k in keys if k in _blob(r))
            if hits >= min_hits:
                rr = dict(r)
                rr["_hits"] = hits
                filtered.append(rr)
        pool = filtered
    pool.sort(
        key=lambda r: (int(r.get("_hits") or 0), r.get("pub_date") or ""), reverse=True
    )
    return pool[:top]


def render_views_markdown(
    items: list[dict], title: str, zh: dict[str, str] | None = None
) -> list[str]:
    """渲染成 Markdown 段落(带来源与日期). zh 为英文标题的中文译文映射."""
    if not items:
        return []
    lines = [f"## {title}", ""]
    for r in items:
        date = (r.get("pub_date") or "")[:10]
        src = r.get("source_name") or r.get("source") or ""
        url = r.get("url") or ""
        t = r.get("title") or ""
        lines.append(f"- {date}　[{t}]({url})　`{src}`" if url else f"- {date}　{t}　`{src}`")
        if zh and zh.get(t):
            lines.append(f"  - 中文：{zh[t]}")
    lines.append("")
    return lines


def translate_titles(items: list[dict], backend: str = "auto") -> dict[str, str]:
    """把英文标题批量翻成中文(被缓存, 重复生成报告不再消耗额度)."""
    if not items or backend == "none":
        return {}
    from ..translate import translate_many

    titles = [r.get("title") or "" for r in items]
    return translate_many(titles, backend=backend)
