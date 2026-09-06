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
    assert items[0].policy_score == 35.0  # level A/B 提分
    assert "重点" in items[0].channel


def test_crawler_handles_none_gracefully():
    """接口挂掉返回 None 时不应抛异常。"""
    for cls in (NFRACrawler, CSRCCrawler, GovPolicyCrawler, EastmoneyFlash, ClsTelegraph):
        assert cls(fetcher=FakeFetcher({})).run() == []
