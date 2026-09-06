"""官方政策站点抓取器.

优先使用各站点自身的 JSON 接口 (稳定、结构化), HTML 解析作为兜底。
接口结构随时可能调整, 用 `finradar doctor` 逐源体检。

已验证可用的 JSON 接口:
  * 国家金融监督管理总局 nfra.gov.cn
      /cbircweb/DocInfo/SelectDocByItemIdAndChild?itemId=<栏目>&pageSize=&pageIndex=
  * 中国证监会 csrc.gov.cn
      /searchList/<channelId>?_isAgg=true&_isJson=true&_pageSize=&page=
  * 中国政府网 sousuo.www.gov.cn
      /search-gov/data?t=zhengcelibrary&q=<关键词>&...   (政策文件库)
"""

from __future__ import annotations

from ..models import NewsItem
from ..utils import LOG, abs_url, parse_time
from .base import BaseCrawler, register

# ---------------------------------------------------------------- 金融监管总局


@register
class NFRACrawler(BaseCrawler):
    """国家金融监督管理总局 —— 政策法规 / 新闻发布 / 行政处罚."""

    source_id = "nfra"
    source_name = "国家金融监督管理总局"
    kind = "official"
    homepage = "https://www.nfra.gov.cn/"
    base_score = 70.0

    API = "https://www.nfra.gov.cn/cbircweb/DocInfo/SelectDocByItemIdAndChild"
    DETAIL = "https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?docId={doc_id}&itemId={item_id}"
    # itemId 对应官网左侧栏目, 可在官网 URL 里读到
    CHANNELS = {
        "925": "政策法规",
        "951": "新闻发布",
        "4113": "行政处罚",
    }

    def fetch(self) -> list[NewsItem]:
        out: list[NewsItem] = []
        for item_id, channel in self.CHANNELS.items():
            for page in range(1, self.pages + 1):
                data = self.f.get_json(
                    self.API,
                    params={"itemId": item_id, "pageSize": 18, "pageIndex": page},
                    referer=self.homepage,
                )
                rows = ((data or {}).get("data") or {}).get("rows") or []
                if not rows:
                    break
                for r in rows:
                    doc_id = r.get("docId")
                    out.append(
                        self.item(
                            title=r.get("docSubtitle") or r.get("docTitle") or "",
                            url=self.DETAIL.format(doc_id=doc_id, item_id=item_id),
                            published_at=parse_time(r.get("publishDate")),
                            summary=r.get("docSummary") or "",
                            channel=channel,
                        )
                    )
        return out


# ---------------------------------------------------------------- 证监会


@register
class CSRCCrawler(BaseCrawler):
    """中国证监会 —— 要闻 / 政策解读 / 新闻发布会."""

    source_id = "csrc"
    source_name = "中国证券监督管理委员会"
    kind = "official"
    homepage = "https://www.csrc.gov.cn/"
    base_score = 70.0

    API = "https://www.csrc.gov.cn/searchList/{channel_id}"
    CHANNELS = {
        "a1a078ee0bc54721ab6b148884c784a8": "证监会要闻",
        "5dd8a3ce6b134e3c88d21dabf0b6d4a5": "政策解读",
    }

    def fetch(self) -> list[NewsItem]:
        out: list[NewsItem] = []
        for cid, channel in self.CHANNELS.items():
            for page in range(1, self.pages + 1):
                data = self.f.get_json(
                    self.API.format(channel_id=cid),
                    params={
                        "_isAgg": "true",
                        "_isJson": "true",
                        "_pageSize": 18,
                        "_template": "index",
                        "_rangeTimeGte": "",
                        "_channelName": "",
                        "page": page,
                    },
                    referer=self.homepage,
                )
                rows = ((data or {}).get("data") or {}).get("results") or []
                if not rows:
                    break
                for r in rows:
                    out.append(
                        self.item(
                            title=r.get("title") or r.get("subTitle") or "",
                            url=abs_url("https://www.csrc.gov.cn/", r.get("url") or ""),
                            published_at=parse_time(r.get("publishedTimeStr")),
                            summary=(r.get("memo") or r.get("content") or "")[:300],
                            channel=r.get("channelName") or channel,
                        )
                    )
        return out


# ---------------------------------------------------------------- 中国政府网


@register
class GovPolicyCrawler(BaseCrawler):
    """中国政府网政策文件库 —— 按关键词检索金融口径政策.

    这是抓 "国务院 / 央行 / 金融监管总局 / 证监会 发文" 最稳的一条路:
    政策库把各部委的正式文件统一收口, 且带文号。
    """

    source_id = "gov"
    source_name = "中国政府网·政策文件库"
    kind = "official"
    homepage = "https://www.gov.cn/zhengce/"
    base_score = 75.0

    API = "https://sousuo.www.gov.cn/search-gov/data"
    QUERIES = ["金融", "货币政策", "资本市场", "银行", "保险", "债券", "基金"]

    def fetch(self) -> list[NewsItem]:
        out: list[NewsItem] = []
        seen: set[str] = set()
        for q in self.QUERIES:
            data = self.f.get_json(
                self.API,
                params={
                    "t": "zhengcelibrary",
                    "q": q,
                    "p": 1,
                    "n": 20,
                    "sort": "pubtime",
                    "sortType": 1,
                    "searchfield": "title",
                    "timetype": "timezd",
                },
                referer=self.homepage,
            )
            cat = ((data or {}).get("catMap") or {})
            for _, block in cat.items():
                for r in (block or {}).get("listVO") or []:
                    url = r.get("url") or ""
                    if not url or url in seen:
                        continue
                    seen.add(url)
                    out.append(
                        self.item(
                            title=r.get("title") or "",
                            url=url,
                            published_at=parse_time(
                                (r.get("pubtimeStr") or "").replace(".", "-")
                            ),
                            summary=(r.get("summary") or "")[:300],
                            channel=r.get("childtype") or "政策文件",
                            doc_no=r.get("pcode") or r.get("wenhao") or "",
                        )
                    )
        return out


# ---------------------------------------------------------------- 人民银行


@register
class PBCCrawler(BaseCrawler):
    """中国人民银行 —— 政策发布 / 公开市场 / 新闻稿.

    央行官网有较强的 WAF (wzws), 直连 requests 常被 403。
    本抓取器做了三层兜底:
      1) 直接抓栏目 HTML
      2) 失败则回退到 "中国政府网政策库 + puborg=中国人民银行"
      3) 仍失败则返回空并在 doctor 里报告, 建议改用浏览器/代理
    """

    source_id = "pbc"
    source_name = "中国人民银行"
    kind = "official"
    homepage = "http://www.pbc.gov.cn/"
    base_score = 80.0

    # 常用栏目 (index.html 列表页)
    CHANNELS = {
        "http://www.pbc.gov.cn/goutongjiaoliu/113456/113469/index.html": "新闻发布",
        "http://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125440/index.html": "货币政策司",
    }
    GOV_FALLBACK = "https://sousuo.www.gov.cn/search-gov/data"

    def fetch(self) -> list[NewsItem]:
        out = self._fetch_html()
        if out:
            return out
        LOG.info("[pbc] 官网直连失败, 回退中国政府网政策库")
        return self._fetch_gov_fallback()

    def _fetch_html(self) -> list[NewsItem]:
        from bs4 import BeautifulSoup

        out: list[NewsItem] = []
        for url, channel in self.CHANNELS.items():
            r = self.f.get(url, referer=self.homepage, encoding="utf-8")
            if r is None:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.select("a[href]"):
                href, title = a.get("href", ""), a.get_text(strip=True)
                if len(title) < 8 or "index" in href:
                    continue
                if not any(k in href for k in (".html", ".htm")):
                    continue
                # 同级 td/li 里常带日期
                date_txt = ""
                parent = a.find_parent(["td", "li", "tr", "div"])
                if parent:
                    import re

                    m = re.search(r"(20\d{2})[-./年](\d{1,2})[-./月](\d{1,2})", parent.get_text(" "))
                    if m:
                        date_txt = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
                out.append(
                    self.item(
                        title=title,
                        url=abs_url(url, href),
                        published_at=parse_time(date_txt) if date_txt else "",
                        channel=channel,
                    )
                )
        return out

    def _fetch_gov_fallback(self) -> list[NewsItem]:
        out: list[NewsItem] = []
        data = self.f.get_json(
            self.GOV_FALLBACK,
            params={
                "t": "zhengcelibrary",
                "q": "金融",
                "p": 1,
                "n": 20,
                "sort": "pubtime",
                "sortType": 1,
                "searchfield": "title",
                "puborg": "中国人民银行",
                "timetype": "timezd",
            },
            referer="https://www.gov.cn/",
        )
        for _, block in ((data or {}).get("catMap") or {}).items():
            for r in (block or {}).get("listVO") or []:
                out.append(
                    self.item(
                        title=r.get("title") or "",
                        url=r.get("url") or "",
                        published_at=parse_time((r.get("pubtimeStr") or "").replace(".", "-")),
                        summary=(r.get("summary") or "")[:300],
                        channel="中国人民银行(经政府网)",
                        doc_no=r.get("pcode") or "",
                    )
                )
        return out


# ---------------------------------------------------------------- 外汇局


@register
class SAFECrawler(BaseCrawler):
    """国家外汇管理局 —— 政策法规与数据 (QDII 额度表就在这里)."""

    source_id = "safe"
    source_name = "国家外汇管理局"
    kind = "official"
    homepage = "https://www.safe.gov.cn/"
    base_score = 70.0

    LIST_PAGES = {
        "https://www.safe.gov.cn/safe/zcfg/index.html": "政策法规",
        "https://www.safe.gov.cn/safe/xwfb/index.html": "新闻发布",
    }

    def fetch(self) -> list[NewsItem]:
        from bs4 import BeautifulSoup
        import re

        out: list[NewsItem] = []
        for url, channel in self.LIST_PAGES.items():
            r = self.f.get(url, referer=self.homepage, encoding="utf-8")
            if r is None:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for li in soup.select("li"):
                a = li.find("a", href=True)
                if not a:
                    continue
                title = a.get_text(strip=True)
                if len(title) < 8:
                    continue
                m = re.search(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})", li.get_text(" "))
                out.append(
                    self.item(
                        title=title,
                        url=abs_url(url, a["href"]),
                        published_at=parse_time(
                            f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
                        ) if m else "",
                        channel=channel,
                    )
                )
        return out


# ---------------------------------------------------------------- 通用配置化抓取


class ConfigListCrawler(BaseCrawler):
    """由 config/sources.yaml 驱动的通用 HTML 列表抓取器.

    这样新增一个政务站点不用写代码, 只要在 yaml 里配 selector。
    """

    def __init__(self, cfg: dict, fetcher=None, pages: int = 1) -> None:  # noqa: ANN001
        super().__init__(fetcher, pages)
        self.cfg = cfg
        self.source_id = cfg["id"]
        self.source_name = cfg.get("name", cfg["id"])
        self.kind = cfg.get("kind", "official")
        self.base_score = float(cfg.get("base_score", 60))
        self.homepage = cfg.get("homepage", "")

    def fetch(self) -> list[NewsItem]:
        from bs4 import BeautifulSoup
        import re

        out: list[NewsItem] = []
        for page in self.cfg.get("pages", []):
            url = page["url"]
            r = self.f.get(url, referer=self.homepage or url, encoding=page.get("encoding"))
            if r is None:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for node in soup.select(page.get("item_selector", "li")):
                a = node.select_one(page.get("link_selector", "a[href]"))
                if not a or not a.get("href"):
                    continue
                title = a.get_text(strip=True)
                if len(title) < int(page.get("min_title_len", 8)):
                    continue
                text = node.get_text(" ")
                m = re.search(r"(20\d{2})[-./年](\d{1,2})[-./月](\d{1,2})", text)
                out.append(
                    self.item(
                        title=title,
                        url=abs_url(url, a["href"]),
                        published_at=parse_time(
                            f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
                        ) if m else "",
                        channel=page.get("channel", ""),
                    )
                )
        return out
