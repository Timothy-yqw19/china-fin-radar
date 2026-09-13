"""外部视角与官方新闻出口.

这个项目的定位不是"热词词典"，而是一个政策解析工具：同一条政策，
官方原文怎么说、权威媒体怎么报道、海外机构怎么解读，三者放一起才看得清。
所以除了政策原文，这里再补两类源：

  * kind="media"    —— 权威媒体的新闻出口（新华社、三大证券报、政府网要闻）
  * kind="external" —— 海外机构与媒体的解读（路透/彭博等经 Google News 聚合、
                       美联储、世界银行、Rhodium Group）

实现上用标准库解析 RSS，不引入新依赖。外部源默认**不参与**政策打分排序
（base_score 低、单独的 kind），只在报告与专题里作为"外部视角"出现。
"""

from __future__ import annotations

import re
from xml.etree import ElementTree as ET

from ..models import NewsItem
from ..utils import LOG, parse_time
from .base import BaseCrawler, register

NS = {"atom": "http://www.w3.org/2005/Atom"}


def parse_feed(text: str) -> list[dict]:
    """解析 RSS 2.0 / Atom, 返回 [{title, url, published, summary, source}]."""
    out: list[dict] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        LOG.warning("RSS 解析失败: %s", e)
        return out
    # RSS 2.0
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = item.findtext("pubDate") or item.findtext("date") or ""
        desc = item.findtext("description") or ""
        src = ""
        s = item.find("source")
        if s is not None and (s.text or "").strip():
            src = s.text.strip()
        if title:
            out.append(
                {"title": title, "url": link, "published": pub, "summary": desc, "source": src}
            )
    if out:
        return out
    # Atom
    for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
        title = (entry.findtext("atom:title", namespaces=NS) or "").strip()
        link_el = entry.find("atom:link", NS)
        link = (link_el.get("href") if link_el is not None else "") or ""
        pub = entry.findtext("atom:updated", namespaces=NS) or ""
        summary = entry.findtext("atom:summary", namespaces=NS) or ""
        if title:
            out.append(
                {"title": title, "url": link, "published": pub, "summary": summary, "source": ""}
            )
    return out


def strip_html(s: str, limit: int = 300) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", s).strip()[:limit]


def deep_text(v) -> str:  # noqa: ANN001
    """世界银行等接口的字段可能是 {'cdata!': '...'} 这种包装, 统一取出文本."""
    if isinstance(v, dict):
        for k in ("cdata!", "value", "text", "title"):
            if k in v:
                return deep_text(v[k])
        return " ".join(deep_text(x) for x in v.values())
    if isinstance(v, list):
        return " ".join(deep_text(x) for x in v)
    return str(v or "")


class _FeedSource(BaseCrawler):
    """单个 RSS 源."""

    kind = "external"
    base_score = 0.0
    homepage = ""
    FEED = ""
    region = "海外"

    def fetch(self) -> list[NewsItem]:
        r = self.f.get(self.FEED, referer=self.homepage or None)
        if r is None:
            return []
        rows = parse_feed(r.text)
        out = []
        for it in rows[: self.pages * 60]:
            out.append(
                self.item(
                    title=it["title"],
                    url=it["url"],
                    published_at=parse_time(it["published"]),
                    summary=strip_html(it["summary"]),
                    channel=f"{self.source_name}·{self.region}",
                )
            )
        return out


@register
class RhodiumFeed(_FeedSource):
    """Rhodium Group —— 海外最常被引用的中国政策研究机构之一."""

    source_id = "rhodium"
    source_name = "Rhodium Group"
    FEED = "https://rhg.com/feed/"
    homepage = "https://rhg.com/"
    region = "海外研究"


@register
class FedFeed(_FeedSource):
    """美联储新闻稿 —— 看中美利差、美元流动性与汇率预期的一手来源."""

    source_id = "fed"
    source_name = "美联储"
    FEED = "https://www.federalreserve.gov/feeds/press_all.xml"
    homepage = "https://www.federalreserve.gov/newsevents.htm"
    region = "海外官方"


@register
class GoogleNewsFeed(BaseCrawler):
    """Google News RSS 检索 —— 聚合路透/彭博/南华早报/日经等对外媒报道.

    为什么走这条路: 这些媒体本身有付费墙与反爬, 但 Google News 的 RSS 是公开接口,
    标题 + 来源 + 链接都能拿到, 正好用来做"海外怎么读这条政策"。
    """

    source_id = "google_news"
    source_name = "Google News（海外媒体）"
    kind = "external"
    base_score = 0.0
    homepage = "https://news.google.com/"
    QUERIES = [
        "China monetary policy",
        "China capital markets reform",
        "China stock connect QFII QDII",
        "China bond market",
        "China property policy",
        "China local government debt",
        "PBOC yuan",
        "China insurance funds investment",
        "China green finance carbon",
        "China tech finance venture capital",
    ]
    API = "https://news.google.com/rss/search"

    def fetch(self) -> list[NewsItem]:
        out: list[NewsItem] = []
        seen: set[str] = set()
        for q in self.QUERIES:
            r = self.f.get(
                self.API,
                params={"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"},
                referer=self.homepage,
            )
            if r is None:
                continue
            for it in parse_feed(r.text)[:30]:
                if not it["url"] or it["url"] in seen:
                    continue
                seen.add(it["url"])
                src = it.get("source") or ""
                out.append(
                    self.item(
                        title=it["title"],
                        url=it["url"],
                        published_at=parse_time(it["published"]),
                        summary=strip_html(it["summary"]),
                        channel=f"海外媒体·{src}" if src else "海外媒体",
                    )
                )
        return out


@register
class WorldBankNews(BaseCrawler):
    """世界银行新闻检索 API —— 多边机构的官方视角（无需 key）."""

    source_id = "worldbank"
    source_name = "世界银行"
    kind = "external"
    base_score = 0.0
    homepage = "https://www.worldbank.org/en/news"
    API = "https://search.worldbank.org/api/v2/news"

    def fetch(self) -> list[NewsItem]:
        data = self.f.get_json(
            self.API,
            params={"format": "json", "qterm": "China", "rows": 30, "order": "desc"},
            referer=self.homepage,
        )
        docs = ((data or {}).get("documents") or {}).values()
        out = []
        for d in docs:
            title = deep_text(d.get("title") or d.get("docna")).strip()
            if not title:
                continue
            url = deep_text(d.get("url") or d.get("docurl")).strip()
            if url and not url.startswith("http"):
                url = "https://www.worldbank.org" + url
            out.append(
                self.item(
                    title=title,
                    url=url,
                    published_at=parse_time(d.get("docdt") or d.get("lnchdt") or ""),
                    summary=strip_html(deep_text(d.get("descr") or d.get("txt") or d.get("abstracts"))),
                    channel="世界银行·海外官方",
                )
            )
        return out
