"""时间窗口与统计周期.

项目最早只看"最近几天"，但政策热词的价值恰恰在长周期：某个提法是哪一年
冒出来的、哪一年开始高频、哪一年被新提法替代。所以统一在这里定义：

  * 窗口（看多久）——  3m / 6m / 1y / 3y / 5y / all
  * 周期（怎么分桶）—— day / week / month / quarter / year

两者是独立的：看 5 年可以按月分桶（画趋势），也可以按年分桶（写回顾）。
不指定时按窗口自动选一个不"太碎"的周期（见 auto_bucket）。
"""

from __future__ import annotations

from datetime import date, timedelta

# 窗口预设: 名称 -> 天数
WINDOWS: dict[str, int] = {
    "3m": 90,
    "6m": 182,
    "1y": 365,
    "3y": 1095,
    "5y": 1826,
}

WINDOW_LABEL: dict[str, str] = {
    "3m": "近 3 个月",
    "6m": "近半年",
    "1y": "近 1 年",
    "3y": "近 3 年",
    "5y": "近 5 年",
    "all": "全部",
}

BUCKETS = ("day", "week", "month", "quarter", "year")
BUCKET_LABEL = {
    "day": "日",
    "week": "周",
    "month": "月",
    "quarter": "季",
    "year": "年",
}


def parse_window(spec: str | None) -> int | None:
    """把 3m/6m/1y/3y/5y/all 或 90d/18m/2y 转成天数; all/None -> None."""
    if not spec:
        return None
    s = spec.strip().lower()
    if s in ("all", "max", "0"):
        return None
    if s in WINDOWS:  # 预设以表里的天数为准, 不要按 30 天/月折算
        return WINDOWS[s]
    if s.isdigit():
        return int(s)
    unit = s[-1]
    num = s[:-1]
    if not num.isdigit():
        raise ValueError(f"无法识别的窗口: {spec}（可用 3m / 6m / 1y / 3y / 5y / all）")
    n = int(num)
    if unit == "d":
        return n
    if unit == "w":
        return n * 7
    if unit == "m":
        return n * 30
    if unit == "y":
        return n * 365
    raise ValueError(f"无法识别的窗口: {spec}（可用 3m / 6m / 1y / 3y / 5y / all）")


def resolve_window(
    window: str | None, days: int | None = None, today: date | None = None
) -> tuple[str | None, str, int | None]:
    """返回 (起始日期 or None, 中文标签, 跨度天数 or None).

    优先级: --window 显式指定 > --days > 默认 30 天。
    """
    from ..utils import now_cn

    today = today or now_cn().date()
    if window:
        span = parse_window(window)
        label = WINDOW_LABEL.get(window.strip().lower(), f"近 {window}")
    elif days:
        span = days
        label = f"近 {days} 天"
    else:
        span = 30
        label = "近 30 天"
    if span is None:
        return None, label, None
    since = (today - timedelta(days=span)).isoformat()
    return since, label, span


def auto_bucket(span_days: int | None) -> str:
    """按窗口跨度选一个"不过于琐碎"的分桶粒度.

    用户原话是"以天为时间看太琐碎了"，所以这里的默认值刻意偏粗:
    3 个月以内按周、半年到 1 年按月、1 年以上按季度、3 年以上按年。
    """
    if span_days is None:
        return "year"
    if span_days <= 45:
        return "week"
    if span_days <= 400:
        # 3 个月 / 半年 / 1 年: 按月。政策文件一周也就几条, 按周看全是 0 和 1
        return "month"
    if span_days <= 1100:
        return "quarter"
    return "year"


def period_key(day: str, bucket: str) -> str:
    """把 'YYYY-MM-DD' 归到某个周期键: 2026-09 / 2026-Q3 / 2026 / 2026-W37."""
    d = (day or "")[:10]
    if len(d) < 7:
        return ""
    y, m = d[:4], int(d[5:7])
    if bucket == "year":
        return y
    if bucket == "quarter":
        return f"{y}-Q{(m - 1) // 3 + 1}"
    if bucket == "month":
        return f"{y}-{m:02d}"
    if bucket == "day":
        return d
    try:  # week
        yy, mm, dd = (int(x) for x in d.split("-"))
        iso = date(yy, mm, dd).isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    except Exception:  # noqa: BLE001
        return d


def period_sort_key(key: str) -> tuple:
    """周期键的排序键 (周期键本身按字符串排也对, 这里显式处理季度)."""
    if "-Q" in key:
        y, q = key.split("-Q")
        return (int(y), int(q))
    if "W" in key:
        y, w = key.split("-W")
        return (int(y), int(w))
    parts = key.split("-")
    return tuple(int(p) for p in parts)


def last_periods(keys: list[str], n: int) -> list[str]:
    return sorted({k for k in keys if k}, key=period_sort_key)[-n:]


def fill_periods(keys: list[str], bucket: str, limit: int = 24) -> list[str]:
    """把中间没有数据的周期补齐.

    不补的话, 6 月没有命中词的矩阵会直接跳到 7 月, 看上去像"时间线断了",
    而实际上只是那个月没有词库热词命中。
    """
    ks = sorted({k for k in keys if k}, key=period_sort_key)
    if not ks:
        return []
    if bucket in ("day", "week"):  # 补周/日意义不大, 直接取最近 limit 个
        return ks[-limit:]
    start, end = period_sort_key(ks[0]), period_sort_key(ks[-1])
    out: list[str] = []
    if bucket == "year":
        out = [str(y) for y in range(start[0], end[0] + 1)]
    elif bucket == "quarter":
        y, q = start[0], start[1]
        while (y, q) <= (end[0], end[1]):
            out.append(f"{y}-Q{q}")
            q += 1
            if q > 4:
                y, q = y + 1, 1
    else:  # month
        y, m = start[0], start[1]
        while (y, m) <= (end[0], end[1]):
            out.append(f"{y}-{m:02d}")
            m += 1
            if m > 12:
                y, m = y + 1, 1
    return out[-limit:]


def iter_periods(start: str, end: str, step: str = "year"):
    """把 [start, end] 切成连续时段, 产出 (标签, 起, 止), 用于历史回捞.

    step: year / quarter / month —— 回捞政务站历史时, 按年切最省请求。
    """
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    cur = s
    while cur <= e:
        if step == "year":
            nxt = date(cur.year + 1, 1, 1)
            label = str(cur.year)
        elif step == "quarter":
            q = (cur.month - 1) // 3
            nxt = date(cur.year + (1 if q == 3 else 0), (q + 1) % 4 * 3 + 1, 1)
            label = f"{cur.year}-Q{q + 1}"
        elif step == "month":
            nxt = date(cur.year + (1 if cur.month == 12 else 0), cur.month % 12 + 1, 1)
            label = f"{cur.year}-{cur.month:02d}"
        else:
            raise ValueError(f"不支持的步长: {step}（可用 year / quarter / month）")
        seg_end = min(nxt - timedelta(days=1), e)
        yield label, cur.isoformat(), seg_end.isoformat()
        cur = nxt
