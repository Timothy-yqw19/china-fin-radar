"""用离线样本验证各抓取器的解析逻辑（不联网）。

样本结构取自各站点真实响应（2026-09 核对），只保留字段骨架。
接口若改版，这些测试会先失败，提示需要更新 params/字段映射。
"""

from __future__ import annotations

import pytest

from finradar.crawlers.flash import ClsTelegraph, EastmoneyFlash, SinaFlash, ThsFlash
from finradar.crawlers.official import CSRCCrawler, GovPolicyCrawler, NFRACrawler


class FakeFetcher:
    """按 URL 关键字返回预置 JSON。"""

    def __init__(self, mapping: dict):
        self.mapping = mapping

    def get_json(self, url, **kw):
        for key, val in self.mapping.items():
            if key in url:
                return val
        return None

    def get(self, url, **kw):
        return None


NFRA_JSON = {
    "rptCode": 200,
    "data": {
        "total": 1703,
        "rows": [
            {
                "docId": 1265691,
                "docSubtitle": "国家金融监督管理总局就《行政复议办法（征求意见稿）》公开征求意见的公告",
                "docTitle": "关于公开征求意见的公告",
                "publishDate": "2026-07-24 17:08:20",
                "docSummary": None,
            }
        ],
    },
}

CSRC_JSON = {
    "data": {
        "page": 1,
        "total": 3189,
        "results": [
            {
                "title": "中国证监会召开专题会议",
                "url": "//www.csrc.gov.cn/csrc/c100028/c7656853/content.shtml",
                "publishedTimeStr": "2026-09-04 18:49:39",
                "memo": "近日，中国证监会召开会议…",
                "channelName": "证监会要闻",
            }
        ],
    }
}

GOV_JSON = {
    "code": 200,
    "listVO": None,
    "catMap": {
        "gongwen": {
            "totalCount": 28,
            "listVO": [
                {
                    "title": "国务院办公厅关于做好金融“五篇大文章”的指导意见",
                    "pcode": "国办发〔2025〕8号",
                    "pubtimeStr": "2025.03.05",
                    "summary": "为贯彻落实党中央、国务院决策部署…",
                    "url": "https://www.gov.cn/zhengce/zhengceku/202503/content_7010605.htm",
                    "childtype": "财政、金融、审计\\其他",
                }
            ],
        }
    },
}

EM_JSON = {
    "data": {
        "fastNewsList": [
            {
                "title": "央行开展逆回购操作",
                "summary": "为维护流动性合理充裕…",
                "showTime": "2026-09-05 09:20:00",
                "code": "202609053456789",
            }
        ]
    }
}

THS_JSON = {
    "data": {
        "list": [
            {
                "title": "金融监管总局发布新规",
                "digest": "内容摘要…",
                "rtime": 1757035200,
                "url": "https://news.10jqka.com.cn/x.html",
            }
        ]
    }
}

SINA_JSON = {
    "result": {
        "data": {
            "feed": {
                "list": [
                    {
                        "rich_text": "【央行开展买断式逆回购】人民银行今日开展…",
                        "create_time": "2026-09-05 10:00:00",
                        "docurl": "",
                    }
                ]
            }
        }
    }
}

CLS_JSON = {
    "data": {
        "roll_data": [
            {
                "id": 123456,
                "title": "证监会就修订办法公开征求意见",
                "content": "证监会今日发布…",
                "ctime": 1757035200,
                "level": "A",
            }
        ]
    }
}


@pytest.mark.parametrize(
    "cls, mapping, expect_title, expect_url_part",
    [
        (NFRACrawler, {"SelectDocByItemIdAndChild": NFRA_JSON}, "行政复议办法", "docId=1265691"),
        (CSRCCrawler, {"searchList": CSRC_JSON}, "专题会议", "csrc.gov.cn"),
        (GovPolicyCrawler, {"search-gov": GOV_JSON}, "五篇大文章", "gov.cn"),
        (EastmoneyFlash, {"getFastNewsList": EM_JSON}, "逆回购", "eastmoney.com"),
        (ThsFlash, {"news/push/stock": THS_JSON}, "金融监管总局", "10jqka"),
        (SinaFlash, {"zhibo/feed": SINA_JSON}, "买断式逆回购", ""),
        (ClsTelegraph, {"get_roll_list": CLS_JSON}, "征求意见", "cls.cn"),
    ],
)
def test_parser(cls, mapping, expect_title, expect_url_part):
    c = cls(fetcher=FakeFetcher(mapping), pages=1)
    items = c.fetch()
    assert items, f"{cls.__name__} 未解析出条目"
    it = items[0]
    assert expect_title in it.title or expect_title in it.summary
    if expect_url_part:
        assert expect_url_part in it.url
    assert len(it.published_at) == 19, f"时间解析异常: {it.published_at}"


def test_cls_marks_important():
    c = ClsTelegraph(fetcher=FakeFetcher({"get_roll_list": CLS_JSON}))
    items = c.fetch()
    assert items[0].policy_score == 22.0  # 来源先验分 10 + 重点标记 12
    assert "重点" in items[0].channel


def test_crawler_handles_none_gracefully():
    """接口挂掉返回 None 时不应抛异常。"""
    for cls in (NFRACrawler, CSRCCrawler, GovPolicyCrawler, EastmoneyFlash, ClsTelegraph):
        assert cls(fetcher=FakeFetcher({})).run() == []


# ---------------------------------------------------------------- 2026-09-12 实测修正

# 政府网政策库真实返回体的骨架: 结果在 searchVO.catMap 下(旧代码读顶层 catMap, 抓 0 条)
GOV_REAL_JSON = {
    "code": 200,
    "searchVO": {
        "totalCount": 0,
        "catMap": {
            "gongwen": {
                "totalCount": 28,
                "listVO": [
                    {
                        "title": "国务院办公厅关于做好<em>金融</em>“五篇大文章”的指导意见",
                        "pcode": "国办发〔2025〕8号",
                        "pubtimeStr": "2025.03.05",
                        "summary": "为贯彻落实党中央、国务院决策部署…",
                        "url": "https://www.gov.cn/zhengce/zhengceku/202503/content_7010605.htm",
                        "childtype": "财政、金融、审计\\其他",
                        "puborg": "国务院办公厅",
                    }
                ],
            }
        },
    },
}

# 央行列表页骨架: 正文链接带 istitle="true", 完整标题在 title 属性里, 日期在 span.hui12
PBC_HTML = """
<html><body><table>
  <tr><td><a href="/rmyh/109339/index.html">术语表</a></td></tr>
  <tr><td width="15"></td><td>
    <font class="newslist_style"><a href="/goutongjiaoliu/113456/113469/2026091115515046822/index.html"
      istitle="true" title="中国人民银行副行长宣昌能会见标普全球总裁玛蒂娜">中国人民银行副行长宣昌能会见标普…</a></font>
    <span class="hui12">2026-09-11</span>
  </td></tr>
  <tr><td width="15"></td><td>
    <font class="newslist_style"><a href="/goutongjiaoliu/113456/113469/2026091018550019961/index.html"
      istitle="true" title="国新办举行金融领域新闻发布会">国新办举行金融领域新闻发…</a></font>
    <span class="hui12">2026-09-10</span>
  </td></tr>
</table></body></html>
"""


def test_gov_reads_searchvo_catmap():
    """回归: 政府网把结果挪进 searchVO 后, 旧代码会静默抓到 0 条。"""
    c = GovPolicyCrawler(fetcher=FakeFetcher({"search-gov": GOV_REAL_JSON}), pages=1)
    items = c.fetch()
    assert items, "searchVO.catMap 结构未被识别"
    assert items[0].title == "国务院办公厅关于做好金融“五篇大文章”的指导意见"  # <em> 被清掉
    assert items[0].doc_no == "国办发〔2025〕8号"
    assert items[0].date == "2025-03-05"
    assert "国务院办公厅" in items[0].channel  # 发文机关进 channel, 参与打分


def test_gov_legacy_catmap_still_supported():
    """旧的顶层 catMap 结构继续兼容。"""
    c = GovPolicyCrawler(fetcher=FakeFetcher({"search-gov": {"catMap": GOV_REAL_JSON["searchVO"]["catMap"]}}))
    assert c.fetch()


class FakeHtmlFetcher:
    """按 URL 返回预置 HTML(列表页)。"""

    def __init__(self, mapping: dict):
        self.mapping = mapping

    def get(self, url, **kw):
        body = self.mapping.get(url)
        if body is None:
            return None

        class R:
            text = body
            status_code = 200

        return R()

    def get_json(self, url, **kw):  # pragma: no cover - 列表源不用 JSON
        return None


def test_pbc_list_parser():
    from finradar.crawlers.official import PBCCrawler

    url = "https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/index.html"
    c = PBCCrawler(fetcher=FakeHtmlFetcher({url: PBC_HTML}), pages=1)
    items = [i for i in c.fetch() if i.channel == "新闻发布"]
    assert len(items) == 2, "旧代码把所有含 index 的链接都过滤掉了"
    assert items[0].title.startswith("中国人民银行副行长宣昌能")
    assert items[0].date == "2026-09-11"
    assert "术语表" not in " ".join(i.title for i in items)  # 导航项不该进来


CONFIG_CFG = {
    "id": "demo",
    "name": "演示站",
    "kind": "official",
    "base_score": 25,
    "homepage": "https://x.test/",
    "pages": [
        {
            "url": "https://x.test/list/index.html",
            "channel": "通知",
            "item_selector": "li",
            "link_selector": "a[href]",
            "container_selector": "div.content",
            "require_date": True,
            "page_pattern": "https://x.test/list/index_{n}.html",
            "page_start": 0,
        }
    ],
}

CONFIG_PAGE1 = """
<html><body>
 <div class="nav"><ul><li><a href="/about/">关于我们</a></li></ul></div>
 <div class="content"><ul>
   <li><a href="./202609/t20260902_1.html" title="关于印发科创板改革实施方案的通知">关于印发科创板改革实施方案的通知<i>09-02</i></a></li>
   <li><a href="/column/">下级栏目</a></li>
 </ul></div>
</body></html>
"""

CONFIG_PAGE2 = """
<html><body><div class="content"><ul>
  <li><a href="./202608/t20260820_2.html" title="关于修订管理办法的公告">关于修订管理办法的公告</a></li>
</ul></div></body></html>
"""


def test_config_crawler_filters_and_paginates():
    from finradar.crawlers.official import ConfigListCrawler

    fetcher = FakeHtmlFetcher(
        {
            "https://x.test/list/index.html": CONFIG_PAGE1,
            "https://x.test/list/index_1.html": CONFIG_PAGE2,
        }
    )
    c = ConfigListCrawler(CONFIG_CFG, fetcher=fetcher, pages=2)
    items = c.fetch()
    titles = [i.title for i in items]
    assert "关于印发科创板改革实施方案的通知" in titles, "标题里粘的日期没被剥掉"
    assert "关于修订管理办法的公告" in titles, "翻页 URL 没生成对"
    assert all("关于我们" != t and "下级栏目" != t for t in titles), "导航项被 require_date 过滤掉了?"
    assert all(len(i.date) == 10 for i in items), "href 里的日期没解析出来"


def test_extract_date_variants():
    from finradar.crawlers.official import extract_date, strip_trailing_date

    assert extract_date("2026-09-11 发布", "") == "2026-09-11"
    assert extract_date("", "/202609/t20260909_1965263.html") == "2026-09-09"
    assert extract_date("协会要闻 09-02", "")[5:] == "09-02"  # MM-DD 按年份推断
    assert extract_date("没有任何日期", "") == ""
    assert strip_trailing_date("协会举办培训09-02") == "协会举办培训"
    assert strip_trailing_date("正常标题") == "正常标题"
