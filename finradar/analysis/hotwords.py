"""热词统计与趋势分析.

两种口径:
  1) 词库口径 —— 用 finradar/data/glossary 里的热词表去匹配, 结果可解释、可对齐考点
  2) 发现口径 —— 用 n-gram + 停用词过滤, 发现词库里还没有的新词 (供人工补录)
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Iterable

from ..knowledge.glossary import load_glossary

STOPWORDS = set(
    """
的 了 和 与 及 或 在 是 为 对 中 上 下 有 将 把 被 等 就 也 并 而 从 到 由 以 其 该 各 个
我们 他们 表示 指出 认为 强调 要求 提出 进一步 加大 加快 推动 支持 促进 完善 加强 有关
方面 情况 问题 工作 相关 目前 记者 报道 消息 今日 昨日 今天 昨天 上午 下午 发布 举行
持续 有效 积极 稳定 增长 下降 亿元 万元 同比 环比 以上 以下 之间 一步 重要 主要 通过
新华社 证券时报 中国证券报 上海证券报 财联社 央视 记者 表示
""".split()
)

TOKEN_RE = re.compile(r"[一-龥]{2,}|[A-Za-z][A-Za-z0-9+\-]{1,}")


# ---------------------------------------------------------------- 词库口径

def match_glossary(texts: Iterable[str]) -> Counter:
    """统计词库热词在文本流中的出现次数 (含别名归并到主词)."""
    gloss = load_glossary()
    name2term = {}
    for t in gloss:
        for n in t.all_names:
            name2term[n] = t.term
    cnt: Counter = Counter()
    for text in texts:
        seen = set()
        for name, main in name2term.items():
            if name in text and main not in seen:
                seen.add(main)
                cnt[main] += 1
    return cnt


def combined_hotwords(rows: list[dict]) -> Counter:
    """热词榜的实际口径：词库主词 + 规则命中词（config/keywords.yaml）合并。

    词库主词保证"能对上考点"，规则词保证"覆盖面"（降准、白名单这类
    还没单独立词条的高频提法也能上榜）。
    """
    from .tagger import load_rules

    rules = load_rules()
    # 只有"实质议题"类的词才算热词；发文主体、政策动作、噪音词只用于打分
    allow: set[str] = set()
    for cat in ("monetary", "capital_market", "banking", "opening", "theme"):
        allow.update((rules.get(cat) or {}).get("words") or [])

    cnt = match_glossary(f"{r.get('title', '')} {r.get('summary', '')}" for r in rows)
    for r in rows:
        for w in r.get("hotwords") or []:
            if w in allow:
                cnt[w] += 1
    return cnt


def glossary_trend(rows: list[dict], bucket: str = "week") -> dict[str, Counter]:
    """按时间桶统计词库热词, 返回 {桶: Counter}."""
    out: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        d = (r.get("pub_date") or "")[:10]
        if not d:
            continue
        if bucket == "month":
            key = d[:7]
        elif bucket == "day":
            key = d
        else:  # week -> ISO 周
            from datetime import date

            try:
                y, m, dd = (int(x) for x in d.split("-"))
                iso = date(y, m, dd).isocalendar()
                key = f"{iso[0]}-W{iso[1]:02d}"
            except Exception:  # noqa: BLE001
                key = d
        text = f"{r.get('title','')} {r.get('summary','')}"
        for w, n in match_glossary([text]).items():
            out[key][w] += n
    return dict(out)


# ---------------------------------------------------------------- 发现口径

def discover_new_words(
    texts: Iterable[str], top: int = 40, min_len: int = 3, min_count: int = 3
) -> list[tuple[str, int]]:
    """朴素 n-gram 新词发现: 找出高频、不在词库、非停用词的中文串.

    目的不是替代分词, 而是提示"最近反复出现但词库里没有"的新提法,
    人工确认后补进 glossary。
    """
    gloss_names = set()
    for t in load_glossary():
        gloss_names.update(t.all_names)

    cnt: Counter = Counter()
    for text in texts:
        toks = TOKEN_RE.findall(text or "")
        for tok in toks:
            if tok in STOPWORDS or len(tok) < min_len:
                continue
            cnt[tok] += 1
        # 相邻二元组, 捕捉 "买断式逆回购" 这类组合
        for a, b in zip(toks, toks[1:]):
            if a in STOPWORDS or b in STOPWORDS:
                continue
            bi = a + b
            if min_len <= len(bi) <= 12:
                cnt[bi] += 1

    res = [
        (w, n)
        for w, n in cnt.most_common(top * 8)
        if n >= min_count and not any(g in w or w in g for g in gloss_names)
    ]
    return res[:top]
