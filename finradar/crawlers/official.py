"""官方政策站点抓取器.

优先使用各站点自身的 JSON 接口 (稳定、结构化), HTML 解析作为兜底。
接口结构随时可能调整, 用 `finradar doctor` 逐源体检。

接口清单 (2026-09-12 在真实网络下逐个实测通过):
  * 国家金融监督管理总局 nfra.gov.cn
      /cbircweb/DocInfo/SelectDocByItemIdAndChild?itemId=<栏目>&pageSize=&pageIndex=
  * 中国证监会 csrc.gov.cn
      /searchList/<channelId>?_isAgg=true&_isJson=true&_pageSize=&page=
  * 中国政府网 sousuo.www.gov.cn
      /search-gov/data?t=zhengcelibrary&q=<关键词>&...   (政策文件库)
      注意: 结果挂在 searchVO.catMap 下, 旧版曾直接挂 catMap, 两种都兼容
  * 中国人民银行 pbc.gov.cn —— HTML 表格, 列表项是 a[istitle="true"] + span.hui12 日期
  * 国家外汇管理局 safe.gov.cn —— HTML <li><dt><a>标题</a></dt><dd>日期</dd></li>
"""

from __future__ import annotations

import re

from ..models import NewsItem
from ..utils import LOG, abs_url, parse_time
from .base import BaseCrawler, register

DATE_RE = re.compile(r"(20\d{2})[-./年](\d{1,2})[-./月](\d{1,2})")
# href 里常嵌日期: /202609/t20260909_1965263.html、/2026091115515046822/index.html
HREF_DATE_RE = re.compile(r"(20\d{2})(\d{2})(\d{2})")
# 部分站点的列表只给 MM-DD (年份靠当前时间推断)
MD_RE = re.compile(r"(?<!\d)(\d{1,2})[-./月](\d{1,2})(?!\d)")
# 实时快讯列表常只写 HH:MM（当天）
HM_RE = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")
# 列表把日期写在 <a> 内部(如 <i>09-02</i>), 取文本会把日期粘在标题尾部
TAIL_DATE_RE = re.compile(r"(?:20\d{2}[-./年])?\d{1,2}[-./月]\d{1,2}日?$")


def strip_trailing_date(title: str) -> str:
    """去掉粘在标题尾巴上的日期, 如 '协会举办培训09-02' → '协会举办培训'."""
    return TAIL_DATE_RE.sub("", title or "").strip()


def extract_date(text: str, href: str = "") -> str:
    """尽力还原发布日期: 文本完整日期 → href 内嵌 YYYYMMDD → 文本 MM-DD (推断年份)."""
    t = text or ""
    m = DATE_RE.search(t)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    hm = HREF_DATE_RE.search(href or "")
    if hm:
        y, mo, d = int(hm.group(1)), int(hm.group(2)), int(hm.group(3))
        if 2000 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y}-{mo:02d}-{d:02d}"
    md = MD_RE.search(t)
    if md:
        from datetime import date, timedelta

        mo, d = int(md.group(1)), int(md.group(2))
        today = date.today()
        if 1 <= mo <= 12 and 1 <= d <= 31:
            try:
                cand = date(today.year, mo, d)
            except ValueError:
                return ""
            if (cand - today).days > 31:  # 明显在未来 → 属于上一年
                cand = cand - timedelta(days=365)
            return cand.isoformat()
    # 只有 HH:MM 的实时列表: 按"今天"处理（北京时间的今天）
    hm = HM_RE.search(t)
    if hm:
        from ..utils import now_cn

        hh, mm = int(hm.group(1)), int(hm.group(2))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return now_cn().date().isoformat()
    return ""

# ---------------------------------------------------------------- 金融监管总局


@register
class NFRACrawler(BaseCrawler):
    """国家金融监督管理总局 —— 政策法规 / 新闻发布 / 行政处罚."""

    source_id = "nfra"
    source_name = "国家金融监督管理总局"
    kind = "official"
    homepage = "https://www.nfra.gov.cn/"
    base_score = 28.0

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
    base_score = 28.0

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
class GovYaowenCrawler(BaseCrawler):
    """中国政府网·要闻 —— 政府直接的新闻出口.

    要闻列表页是 JS 渲染的, 但它读的是一个静态 JSON（实测 400 条, 含标题/链接/日期）,
    所以直接抓那个 JSON 比解析 HTML 稳。
    """

    source_id = "gov_yaowen"
    source_name = "中国政府网·要闻"
    kind = "official"
    homepage = "https://www.gov.cn/yaowen/liebiao/"
    # 先验分仍守在 30 以内: 要闻里大量是例行的会见、会议, 靠关键词信号决定排序
    base_score = 30.0
    API = "https://www.gov.cn/yaowen/liebiao/YAOWENLIEBIAO.json"

    def fetch(self) -> list[NewsItem]:
        data = self.f.get_json(self.API, referer=self.homepage)
        rows = data if isinstance(data, list) else ((data or {}).get("data") or [])
        out = []
        for r in rows[: self.pages * 200]:
            title = (r.get("TITLE") or "").strip()
            url = (r.get("URL") or "").strip()
            if not title or not url:
                continue
            out.append(
                self.item(
                    title=title,
                    url=url,
                    published_at=parse_time(r.get("DOCRELPUBTIME")),
                    summary=(r.get("SUB_TITLE") or "").strip(),
                    channel="要闻",
                )
            )
        return out


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
    base_score = 30.0

    API = "https://sousuo.www.gov.cn/search-gov/data"
    QUERIES = ["金融", "货币政策", "资本市场", "银行", "保险", "债券", "基金"]
    # 历史回捞用的宽口径关键词(全文检索), 覆盖"标题里没写金融但内容是金融"的文件
    HISTORY_QUERIES = ["金融", "货币政策", "资本市场"]

    @staticmethod
    def _cat_map(data: dict | None) -> dict:
        """政策库返回体里, 结果可能在 searchVO.catMap 或顶层 catMap."""
        if not isinstance(data, dict):
            return {}
        sv = data.get("searchVO") or {}
        return sv.get("catMap") or data.get("catMap") or {}

    def fetch(self) -> list[NewsItem]:
        """日常抓取: 按关键词检索, 只要标题命中的(精度优先), 结果里最新的在前."""
        out: list[NewsItem] = []
        seen: set[str] = set()
        for q in self.QUERIES:
            for page in range(1, self.pages + 1):
                items, cat = self._query(q, page, searchfield="title")
                before = len(out)
                for it in items:
                    if it.url and it.url not in seen:
                        seen.add(it.url)
                        out.append(it)
                if len(out) == before:  # 该页没有新内容, 不再翻页
                    break
        return out

    # ------------------------------------------------------------ 历史回捞

    def _query(
        self,
        q: str,
        page: int,
        searchfield: str | None = "title",
        mintime: str | None = None,
        maxtime: str | None = None,
        page_size: int = 20,
    ) -> tuple[list[NewsItem], dict]:
        params: dict = {
            "t": "zhengcelibrary",
            "q": q,
            "p": page,
            "n": page_size,
            "sort": "pubtime",
            "sortType": 1,
            "timetype": "timezd",
        }
        if searchfield:
            params["searchfield"] = searchfield
        # 政策库支持按日精确取历史: mintime/maxtime (timetype=timezd)
        if mintime:
            params["mintime"] = mintime
        if maxtime:
            params["maxtime"] = maxtime
        data = self.f.get_json(self.API, params=params, referer=self.homepage)
        cat = self._cat_map(data)
        items: list[NewsItem] = []
        for _, block in cat.items():
            for r in (block or {}).get("listVO") or []:
                url = r.get("url") or ""
                if not url:
                    continue
                puborg = (r.get("puborg") or "").strip()
                items.append(
                    self.item(
                        title=r.get("title") or "",
                        url=url,
                        published_at=parse_time((r.get("pubtimeStr") or "").replace(".", "-")),
                        summary=(r.get("summary") or "")[:300],
                        # 发文机关进 channel: 打分规则的"发文主体"能命中, 报告里也能看到是谁发的文
                        channel=f"政策文件·{puborg}" if puborg else "政策文件",
                        doc_no=r.get("pcode") or r.get("wenhao") or "",
                    )
                )
        return items, cat

    def fetch_range(
        self,
        start: str,
        end: str,
        queries: list[str] | None = None,
        max_pages: int = 4,
        searchfield: str | None = None,
        page_size: int = 50,
        progress=None,  # noqa: ANN001
    ) -> list[NewsItem]:
        """按日期范围回捞历史政策文件.

        全文检索(searchfield=None)能捞到标题里没有关键词的文件, 覆盖更广;
        max_pages 是对"每个关键词在每个时段"的翻页上限, 用来控制总请求量。
        """
        out: list[NewsItem] = []
        seen: set[str] = set()
        for q in queries or self.HISTORY_QUERIES:
            for page in range(1, max_pages + 1):
                items, cat = self._query(
                    q, page, searchfield=searchfield,
                    mintime=start, maxtime=end, page_size=page_size,
                )
                fresh = 0
                for it in items:
                    if it.url and it.url not in seen:
                        seen.add(it.url)
                        out.append(it)
                        fresh += 1
                if progress:
                    progress(q, page, len(items), fresh, len(out), cat)
                if not items or fresh == 0:
                    break
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
    base_score = 28.0

    # 常用栏目 (index.html 列表页), 2026-09-12 实测均可访问
    CHANNELS = {
        "https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/index.html": "新闻发布",
        "https://www.pbc.gov.cn/tiaofasi/144941/index.html": "法律法规",
        "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125440/index.html": "利率政策",
        # 公开市场业务交易公告: 每天一篇, 面试问"今天央行做了什么"的标准答案
        "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/index.html": (
            "公开市场业务交易公告"
        ),
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
            # 央行列表页的正文链接统一带 istitle="true"; 兜底用"路径里 15 位以上数字 + /index.html"
            anchors = soup.select('a[istitle="true"]')
            if not anchors:
                anchors = [
                    a
                    for a in soup.select('a[href$="/index.html"]')
                    if re.search(r"/(?:20\d{2}|\d{10,})/index\.html$", a.get("href", ""))
                ]
            for a in anchors:
                href = a.get("href", "")
                # 列表标题会被截断成"…", 完整标题在 title 属性里
                title = (a.get("title") or a.get_text(strip=True)).strip()
                if len(title) < 6 or not href:
                    continue
                row = a.find_parent(["tr", "li"]) or a.parent
                date_txt = extract_date(row.get_text(" ") if row else "", href)
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
        cat = ((data or {}).get("searchVO") or {}).get("catMap") or (data or {}).get("catMap") or {}
        for _, block in cat.items():
            for r in (block or {}).get("listVO") or []:
                out.append(
                    self.item(
                        title=r.get("title") or "",
                        url=r.get("url") or "",
                        published_at=parse_time((r.get("pubtimeStr") or "").replace(".", "-")),
                        summary=(r.get("summary") or "")[:300],
                        channel="中国人民银行(经政府网)"
                        + (f"·{r.get('puborg')}" if r.get("puborg") else ""),
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
    base_score = 28.0

    # 栏目 URL 2026-09-12 实测 (旧版的 /safe/xwfb/ 已改名为 /safe/whxw/)
    LIST_PAGES = {
        "https://www.safe.gov.cn/safe/zcfg/index.html": "政策法规",
        "https://www.safe.gov.cn/safe/whxw/index.html": "外汇新闻",
        "https://www.safe.gov.cn/safe/ywfb/index.html": "要闻发布",
    }
    # 列表页翻页规则: index.html → index_2.html → index_3.html ...
    PAGE_PATTERN = "https://www.safe.gov.cn/safe/{col}/index_{n}.html"

    def fetch(self) -> list[NewsItem]:
        from bs4 import BeautifulSoup

        out: list[NewsItem] = []
        for url, channel in self.LIST_PAGES.items():
            col = url.rstrip("/").split("/")[-2]
            urls = [url] + [
                self.PAGE_PATTERN.format(col=col, n=n) for n in range(2, self.pages + 1)
            ]
            for u in urls:
                r = self.f.get(u, referer=self.homepage, encoding="utf-8")
                if r is None:
                    continue
                soup = BeautifulSoup(r.text, "html.parser")
                for li in soup.select("li"):
                    a = li.find("a", href=True)
                    if not a:
                        continue
                    title = (a.get("title") or a.get_text(strip=True)).strip()
                    if len(title) < 8:
                        continue
                    date_txt = extract_date(li.get_text(" "), a["href"])
                    # 栏目页会把下级栏目做成无日期的 li, 只保留真正带日期的新闻条目
                    if not date_txt:
                        continue
                    out.append(
                        self.item(
                            title=title,
                            url=abs_url(u, a["href"]),
                            published_at=parse_time(date_txt),
                            channel=channel,
                        )
                    )
        return out


# ---------------------------------------------------------------- 通用配置化抓取


class ConfigListCrawler(BaseCrawler):
    """由 config/sources.yaml 驱动的通用 HTML 列表抓取器.

    这样新增一个政务站点不用写代码, 只要在 yaml 里配 selector。

    每个 page 支持:
      url              列表页地址
      channel          栏目名
      item_selector    列表项选择器 (默认 li)
      link_selector    列表项里的链接选择器 (默认 a[href])
      container_selector 可选, 先圈定正文区域, 避免命中导航
      require_date     true 时丢弃没有日期的条目 (导航菜单几乎都没有日期)
      min_title_len    标题最短长度 (默认 8)
      page_pattern     翻页模板, 含 {n}, 如 https://x/safe/whxw/index_{n}.html
      page_start       翻页起始页号 (默认 1)
    """

    def __init__(self, cfg: dict, fetcher=None, pages: int = 1) -> None:  # noqa: ANN001
        super().__init__(fetcher, pages)
        self.cfg = cfg
        self.source_id = cfg["id"]
        self.source_name = cfg.get("name", cfg["id"])
        self.kind = cfg.get("kind", "official")
        self.base_score = float(cfg.get("base_score", 25))
        self.homepage = cfg.get("homepage", "")

    def fetch(self) -> list[NewsItem]:
        from bs4 import BeautifulSoup

        out: list[NewsItem] = []
        for page in self.cfg.get("pages", []):
            first = page["url"]
            pattern = page.get("page_pattern")
            page_start = int(page.get("page_start", 1))
            urls = [first]
            if pattern:
                urls += [
                    pattern.format(n=n)
                    for n in range(page_start + 1, page_start + max(1, self.pages))
                ]
            seen: set[str] = set()
            require_date = bool(page.get("require_date", False))
            min_len = int(page.get("min_title_len", 8))
            for url in urls:
                r = self.f.get(url, referer=self.homepage or url, encoding=page.get("encoding"))
                if r is None:
                    continue
                soup = BeautifulSoup(r.text, "html.parser")
                scope = soup.select_one(page["container_selector"]) if page.get(
                    "container_selector"
                ) else soup
                if scope is None:
                    continue
                for node in scope.select(page.get("item_selector", "li")):
                    a = node.select_one(page.get("link_selector", "a[href]"))
                    if not a or not a.get("href"):
                        continue
                    title = strip_trailing_date((a.get("title") or a.get_text(strip=True)).strip())
                    if len(title) < min_len:
                        continue
                    href = a["href"]
                    full = abs_url(url, href)
                    if full in seen:
                        continue
                    date_txt = extract_date(node.get_text(" "), href)
                    if require_date and not date_txt:
                        continue
                    seen.add(full)
                    out.append(
                        self.item(
                            title=title,
                            url=full,
                            published_at=parse_time(date_txt) if date_txt else "",
                            channel=page.get("channel", ""),
                        )
                    )
        return out
