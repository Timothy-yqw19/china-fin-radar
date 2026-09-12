"""AkShare 适配层.

akshare 已经把大量新闻/宏观接口的反爬和字段清洗做好了, 作为:
  1) 快讯的第二条腿 (直连接口失败时的兜底)
  2) 宏观数据 (LPR / M2 / CPI / 社融 / 国债收益率) 的唯一来源

akshare 是可选依赖: `pip install akshare`
"""

from __future__ import annotations

from ..models import NewsItem
from ..utils import LOG, parse_time
from .base import BaseCrawler, register


def _ak():
    try:
        import akshare as ak  # noqa: PLC0415

        return ak
    except ImportError:
        LOG.warning("未安装 akshare, 跳过。安装: pip install akshare")
        return None


@register
class AkShareNews(BaseCrawler):
    """通过 akshare 拉取多个来源的财经快讯 (兜底通道)."""

    source_id = "akshare"
    source_name = "AkShare 聚合快讯"
    kind = "aggregate"
    base_score = 8.0

    def fetch(self) -> list[NewsItem]:
        ak = _ak()
        if ak is None:
            return []
        out: list[NewsItem] = []
        jobs = [
            ("stock_info_global_em", "东方财富快讯", {}),
            ("stock_info_global_ths", "同花顺直播", {}),
            ("stock_info_global_cls", "财联社电报", {"symbol": "重点"}),
            ("stock_info_cjzc_em", "财经早餐", {}),
        ]
        for fn_name, channel, kw in jobs:
            fn = getattr(ak, fn_name, None)
            if fn is None:
                continue
            try:
                df = fn(**kw)
            except Exception as e:  # noqa: BLE001
                LOG.debug("akshare.%s 失败: %s", fn_name, e)
                continue
            for _, row in df.iterrows():
                d = row.to_dict()
                title = str(d.get("标题") or d.get("内容") or "")[:80]
                if not title:
                    continue
                when = d.get("发布时间") or d.get("时间") or ""
                if d.get("发布日期") is not None and ":" in str(when):
                    when = f"{d.get('发布日期')} {when}"
                out.append(
                    self.item(
                        title=title,
                        url=str(d.get("链接") or ""),
                        published_at=parse_time(when),
                        summary=str(d.get("摘要") or d.get("内容") or ""),
                        channel=channel,
                    )
                )
        return out


# ---------------------------------------------------------------- 宏观数据

MACRO_JOBS = {
    "lpr": ("macro_china_lpr", "LPR 贷款市场报价利率"),
    "money_supply": ("macro_china_money_supply", "货币供应量 M0/M1/M2"),
    "cpi": ("macro_china_cpi", "居民消费价格指数 CPI"),
    "ppi": ("macro_china_ppi", "工业生产者出厂价格指数 PPI"),
    "gdp": ("macro_china_gdp", "国内生产总值 GDP"),
    "pmi": ("macro_china_pmi", "采购经理指数 PMI"),
    "shibor": ("macro_china_shibor_all", "SHIBOR"),
    "shrzgm": ("macro_china_shrzgm", "社会融资规模增量"),
    "credit": ("macro_china_new_financial_credit", "新增人民币贷款"),
    "leverage": ("macro_cnbs", "宏观杠杆率 (国家资产负债表研究中心)"),
    "bond_rate": ("bond_zh_us_rate", "中美国债收益率"),
    "bond_curve": ("bond_china_yield", "中债收益率曲线 (含国债)"),
}

# 需要显式时间窗的接口: key -> 参数构造函数
_DATE_WINDOW_DAYS = {"bond_china_yield": 21}


def _date_col(df) -> str | None:  # noqa: ANN001
    """找出 DataFrame 里的时间列 (akshare 各接口命名不统一)."""
    for c in ("日期", "月份", "时间", "TRADE_DATE", "统计时间", "季度"):
        if c in df.columns:
            return c
    return None


def latest_rows(df, n: int = 8):  # noqa: ANN001, ANN201
    """取最新 n 行.

    坑: akshare 各接口排序方向不一致 —— money_supply/CPI 是"新→旧",
    LPR/中美国债是"旧→新"。所以不能无脑 head/tail, 必须先按时间列排序。
    """
    import pandas as pd

    if df is None or len(df) == 0:
        return df
    col = _date_col(df)
    if not col:
        return df.tail(n)
    s = df[col].astype(str).str.replace(r"[年月]", "", regex=True).str.strip()
    key = pd.to_datetime(s, errors="coerce", format="mixed")
    if key.isna().all():  # 形如 201501 的月份串
        key = pd.to_numeric(s.str.replace(r"\D", "", regex=True), errors="coerce")
    if key.isna().all():
        return df.tail(n)
    out = df.assign(_k=key).sort_values("_k", ascending=False, kind="stable")
    return out.drop(columns=["_k"]).head(n)


def _call(fn, kw: dict | None = None, retries: int = 2):  # noqa: ANN001, ANN202
    """akshare 部分接口偶发超时(实测 LPR 接口约 60s 且会断), 给它重试."""
    import time

    last: Exception | None = None
    for i in range(max(1, retries)):
        try:
            return fn(**(kw or {}))
        except Exception as e:  # noqa: BLE001
            last = e
            LOG.debug("akshare %s 第%d次失败: %s", getattr(fn, "__name__", fn), i + 1, e)
            time.sleep(1.5 * (i + 1))
    raise last if last else RuntimeError("unknown")


def fetch_macro(keys: list[str] | None = None, tail: int = 8) -> dict[str, object]:
    """拉取宏观数据, 返回 {key: DataFrame}. 用于生成"最新数据速查表"."""
    ak = _ak()
    if ak is None:
        return {}
    keys = keys or list(MACRO_JOBS)
    out: dict[str, object] = {}
    for k in keys:
        if k not in MACRO_JOBS:
            continue
        fn_name, label = MACRO_JOBS[k]
        fn = getattr(ak, fn_name, None)
        if fn is None:
            LOG.warning("当前 akshare 版本没有 %s", fn_name)
            continue
        kw: dict = {}
        if fn_name in _DATE_WINDOW_DAYS:
            from datetime import date, timedelta

            days = _DATE_WINDOW_DAYS[fn_name]
            kw = {
                "start_date": (date.today() - timedelta(days=days)).strftime("%Y%m%d"),
                "end_date": date.today().strftime("%Y%m%d"),
            }
        try:
            df = _call(fn, kw, retries=3)
            latest = latest_rows(df, tail)
            as_of = ""
            col = _date_col(df)
            if col is not None and len(df):
                as_of = str(latest[col].iloc[0]) if len(latest) else ""
            out[k] = {"label": label, "df": latest, "as_of": as_of}
            LOG.info("宏观数据 %-12s ✓ %s", k, label)
        except Exception as e:  # noqa: BLE001
            LOG.warning("宏观数据 %s 获取失败: %s", k, e)
    return out
