"""财经快讯抓取器 —— 东方财富 / 同花顺 / 新浪 / 财联社.

这些接口都是站点自身前端在用的公开 JSON 接口, 结构相对稳定;
若返回 None, 多半是接口参数调整或触发了风控, 用 `finradar doctor` 定位。
"""

from __future__ import annotations

import hashlib
import time
from urllib.parse import urlencode

from ..models import NewsItem
from ..utils import parse_time
from .base import BaseCrawler, register


@register
class EastmoneyFlash(BaseCrawler):
    """东方财富 7x24 全球财经快讯."""

    source_id = "em_flash"
    source_name = "东方财富·全球财经快讯"
    kind = "flash"
    homepage = "https://kuaixun.eastmoney.com/7_24.html"
    base_score = 8.0

    API = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"

    def fetch(self) -> list[NewsItem]:
        data = self.f.get_json(
            self.API,
            params={
                "client": "web",
                "biz": "web_724",
                "fastColumn": "102",
                "sortEnd": "",
                "pageSize": 100 * max(1, self.pages),
                "req_trace": str(int(time.time() * 1000)),
            },
            referer=self.homepage,
        )
        rows = ((data or {}).get("data") or {}).get("fastNewsList") or []
        out = []
        for r in rows:
            code = r.get("code") or ""
            out.append(
                self.item(
                    title=r.get("title") or (r.get("summary") or "")[:40],
                    url=f"https://finance.eastmoney.com/a/{code}.html" if code else "",
                    published_at=parse_time(r.get("showTime")),
                    summary=r.get("summary") or "",
                    channel="7x24快讯",
                )
            )
        return out


@register
class EastmoneyBreakfast(BaseCrawler):
    """东方财富·财经早餐 (每日政策要闻汇总, 适合做日报素材)."""

    source_id = "em_breakfast"
    source_name = "东方财富·财经早餐"
    kind = "flash"
    homepage = "https://stock.eastmoney.com/a/czpnc.html"
    base_score = 12.0

    API = "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns"

    def fetch(self) -> list[NewsItem]:
        out = []
        for page in range(1, self.pages + 1):
            data = self.f.get_json(
                self.API,
                params={
                    "client": "web",
                    "biz": "web_news_col",
                    "column": "1207",
                    "order": "1",
                    "needInteractData": "0",
                    "page_index": page,
                    "page_size": 50,
                    "req_trace": str(int(time.time() * 1000)),
                    "fields": "code,showTime,title,mediaName,summary,image,url,uniqueUrl,Np_dst",
                },
                referer=self.homepage,
            )
            rows = ((data or {}).get("data") or {}).get("list") or []
            if not rows:
                break
            for r in rows:
                out.append(
                    self.item(
                        title=r.get("title") or "",
                        url=r.get("uniqueUrl") or "",
                        published_at=parse_time(r.get("showTime")),
                        summary=r.get("summary") or "",
                        channel="财经早餐",
                    )
                )
        return out


@register
class ThsFlash(BaseCrawler):
    """同花顺财经·全球财经直播."""

    source_id = "ths_flash"
    source_name = "同花顺·全球财经直播"
    kind = "flash"
    homepage = "https://news.10jqka.com.cn/realtimenews.html"
    base_score = 8.0

    API = "https://news.10jqka.com.cn/tapp/news/push/stock"

    def fetch(self) -> list[NewsItem]:
        out = []
        for page in range(1, self.pages + 1):
            data = self.f.get_json(
                self.API,
                params={"page": page, "tag": "", "track": "website"},
                referer=self.homepage,
            )
            rows = ((data or {}).get("data") or {}).get("list") or []
            if not rows:
                break
            for r in rows:
                out.append(
                    self.item(
                        title=r.get("title") or (r.get("digest") or "")[:40],
                        url=r.get("url") or "",
                        published_at=parse_time(r.get("rtime")),
                        summary=r.get("digest") or "",
                        channel="财经直播",
                    )
                )
        return out


@register
class SinaFlash(BaseCrawler):
    """新浪财经 7x24 快讯."""

    source_id = "sina_flash"
    source_name = "新浪财经·7x24快讯"
    kind = "flash"
    homepage = "https://finance.sina.com.cn/7x24"
    base_score = 8.0

    API = "https://zhibo.sina.com.cn/api/zhibo/feed"

    def fetch(self) -> list[NewsItem]:
        out = []
        for page in range(1, self.pages + 1):
            data = self.f.get_json(
                self.API,
                params={
                    "page": page,
                    "page_size": 50,
                    "zhibo_id": 152,
                    "tag_id": 0,
                    "dire": "f",
                    "dpc": 1,
                    "pagesize": 50,
                    "type": 1,
                },
                referer=self.homepage,
            )
            feed = (((data or {}).get("result") or {}).get("data") or {}).get("feed") or {}
            rows = feed.get("list") or []
            if not rows:
                break
            for r in rows:
                text = (r.get("rich_text") or "").strip()
                out.append(
                    self.item(
                        title=text[:60],
                        url=r.get("docurl") or "",
                        published_at=parse_time(r.get("create_time")),
                        summary=text,
                        channel="7x24快讯",
                    )
                )
        return out


@register
class ClsTelegraph(BaseCrawler):
    """财联社电报 (含重点标记, 政策消息时效性最好)."""

    source_id = "cls"
    source_name = "财联社·电报"
    kind = "flash"
    homepage = "https://www.cls.cn/telegraph"
    base_score = 10.0

    API = "https://www.cls.cn/v1/roll/get_roll_list"

    def fetch(self) -> list[NewsItem]:
        params = {
            "app": "CailianpressWeb",
            "category": "",
            "last_time": int(time.time()),
            "os": "web",
            "refresh_type": "1",
            "rn": 50 * max(1, self.pages),
            "sv": "8.4.6",
        }
        params["sign"] = hashlib.md5(
            hashlib.sha1(urlencode(params).encode()).hexdigest().encode()
        ).hexdigest()
        data = self.f.get_json(self.API, params=params, referer=self.homepage)
        rows = ((data or {}).get("data") or {}).get("roll_data") or []
        out = []
        for r in rows:
            level = r.get("level") or ""
            it = self.item(
                title=(r.get("title") or (r.get("content") or "")[:50]),
                url=f"https://www.cls.cn/detail/{r.get('id')}" if r.get("id") else "",
                published_at=parse_time(r.get("ctime")),
                summary=r.get("content") or "",
                channel="电报-重点" if level in ("A", "B") else "电报",
            )
            if level in ("A", "B"):
                # 财联社自己给的重点标记: 在来源先验分上加点, 不直接给绝对分
                it.policy_score = self.base_score + 12
            out.append(it)
        return out
