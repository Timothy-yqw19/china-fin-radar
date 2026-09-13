"""大众热榜里的财经话题.

热榜本身九成是娱乐体育, 直接看没有意义; 这里做两件事:
  1) 用金融/宏观词表把它过滤成"今天大众在关心的财经话题"
  2) 跨平台合并同一话题, 并按热度排序 —— 一个话题同时上三个榜, 说明它真的出圈了

这些词也是"新词发现"的补充来源: 政策文件里挖出来的是政策提法,
热榜里挖出来的是市场与大众的说法, 两者交集往往就是面试里会被追问的词。
"""

from __future__ import annotations

import re
from collections import defaultdict

# 大众财经话题的词面特征(比 dossiers 的 FINANCE_TOKENS 更口语化)
FINANCE_HINTS = (
    "金融", "银行", "存款", "贷款", "房贷", "利率", "降息", "降准", "LPR", "汇率",
    "人民币", "美元", "外汇", "股市", "A股", "港股", "美股", "基金", "债券", "债市",
    "券商", "证券", "保险", "理财", "信托", "黄金", "金价", "油价", "比特币", "加密",
    "楼市", "房价", "房地产", "买房", "公积金", "土地", "城投", "地方债", "化债",
    "IPO", "上市", "退市", "并购", "重组", "分红", "回购", "市值", "注册制",
    "经济", "GDP", "CPI", "PPI", "PMI", "通胀", "通缩", "物价", "消费", "社零",
    "就业", "失业", "工资", "收入", "社保", "养老金", "医保", "补贴", "税收", "减税",
    "财政", "货币", "央行", "政策", "关税", "贸易", "出口", "进口", "外资", "服贸会",
    "十五五", "十四五", "新质生产力", "数字经济", "人工智能", "芯片", "新能源",
)

_NUM = re.compile(r"[^\d]")


def hot_value(row: dict) -> int:
    """从 summary 里把"热度 12345"解析出来, 用于排序."""
    s = row.get("summary") or ""
    digits = _NUM.sub("", s)
    return int(digits) if digits.isdigit() else 0


def is_finance(text: str) -> bool:
    return any(h in (text or "") for h in FINANCE_HINTS)


def finance_topics(rows: list[dict], platforms: dict[str, str] | None = None) -> list[dict]:
    """把热榜条目过滤 + 跨平台合并成话题列表.

    返回 [{topic, platforms:[平台名], hot:最高热度, titles:[各平台原话], url}]
    """
    platforms = platforms or {}
    by_topic: dict[str, dict] = defaultdict(
        lambda: {"topic": "", "platforms": [], "hot": 0, "titles": [], "url": ""}
    )
    for r in rows:
        title = (r.get("title") or "").strip()
        if not title or not is_finance(title):
            continue
        key = re.sub(r"[\s　·、，,。.！!？?~～\-—_/]+", "", title)
        key = key[:14]  # 不同平台的措辞略有差异, 用前 14 字做粗归并
        item = by_topic[key]
        item["topic"] = item["topic"] or title
        pname = platforms.get(r.get("source") or "", r.get("source_name") or "")
        if pname and pname not in item["platforms"]:
            item["platforms"].append(pname)
        if title not in item["titles"]:
            item["titles"].append(title)
        item["hot"] = max(item["hot"], hot_value(r))
        item["url"] = item["url"] or (r.get("url") or "")
    out = [v for v in by_topic.values()]
    out.sort(key=lambda x: (-len(x["platforms"]), -x["hot"]))
    return out


def render_topics(topics: list[dict], top: int = 25) -> str:
    if not topics:
        return "（窗口内没有财经相关的热榜话题）"
    lines = []
    for i, t in enumerate(topics[:top], 1):
        hot = f"热度 {t['hot']:,}" if t["hot"] else ""
        cross = "、".join(t["platforms"])
        lines.append(f"{i:>3}. [{cross}] {t['topic']}")
        if len(t["titles"]) > 1:
            lines.append("     其他平台说法：" + " / ".join(t["titles"][1:3]))
        if hot:
            lines.append(f"     {hot}")
    return "\n".join(lines)
