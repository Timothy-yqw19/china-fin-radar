"""大众热榜抓取器（今日头条 / 抖音 / 百度热搜）.

为什么要这一类源: 官方政策文件里挖出来的是"政策提法", 而市场情绪和大众关注
往往先在社交平台发酵。把热榜拉下来、过滤出与金融相关的条目, 就能看到
"普通人此刻在关心什么", 这对判断政策的市场关注度、以及找新词很有用。

三个源都是 JSON 接口, 无需登录（微博热搜 403、知乎热榜 401, 实测拿不到, 故未接入）。

注意: 热榜噪音极高(娱乐体育占九成), 所以:
  * kind="trend", 来源先验分为 0, 不参与政策打分排序
  * 默认**不参与**日报与热词统计, 只看 `finradar trends`
"""

from __future__ import annotations

from ..models import NewsItem
from ..utils import parse_time
from .base import BaseCrawler, register


class _HotBoard(BaseCrawler):
    """热榜基类: 统一构造条目, 热度写进 summary 方便排序与展示."""

    kind = "trend"
    base_score = 0.0
    board = "热榜"

    def make(self, title: str, hot: int | str | None, url: str = "") -> NewsItem:
        hot_txt = f"热度 {hot}" if hot not in (None, "") else ""
        # 热榜是实时榜: 每天留一份快照(去重键带日期), 才能统计"连续上榜天数"。
        # 同一天重复抓仍然去重, 不会膨胀。
        from ..utils import now_cn

        return self.item(
            title=title,
            url=url,
            published_at=parse_time(None),
            summary=hot_txt,
            channel=self.board,
            dedupe_key=f"{self.source_id}:{now_cn().date().isoformat()}:{title}",
        )


@register
class ToutiaoHot(_HotBoard):
    """今日头条热榜."""

    source_id = "toutiao_hot"
    source_name = "今日头条·热榜"
    homepage = "https://www.toutiao.com/hot/"
    API = "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc"

    def fetch(self) -> list[NewsItem]:
        data = self.f.get_json(self.API, referer=self.homepage)
        rows = (data or {}).get("data") or []
        out = []
        for r in rows:
            title = (r.get("Title") or "").strip()
            if not title:
                continue
            out.append(self.make(title, r.get("HotValue"), r.get("Url") or ""))
        return out


@register
class DouyinHot(_HotBoard):
    """抖音热榜."""

    source_id = "douyin_hot"
    source_name = "抖音·热榜"
    homepage = "https://www.douyin.com/hot"
    API = "https://www.douyin.com/aweme/v1/web/hot/search/list/"

    def fetch(self) -> list[NewsItem]:
        data = self.f.get_json(self.API, referer=self.homepage)
        rows = ((data or {}).get("data") or {}).get("word_list") or []
        out = []
        for r in rows:
            title = (r.get("word") or "").strip()
            if not title:
                continue
            out.append(self.make(title, r.get("hot_value")))
        return out


@register
class BaiduHot(_HotBoard):
    """百度热搜（实时榜）."""

    source_id = "baidu_hot"
    source_name = "百度·热搜"
    homepage = "https://top.baidu.com/board?tab=realtime"
    API = "https://top.baidu.com/api/board?platform=wise&tab=realtime"

    def fetch(self) -> list[NewsItem]:
        data = self.f.get_json(self.API, referer=self.homepage)
        cards = ((data or {}).get("data") or {}).get("cards") or []
        out: list[NewsItem] = []
        for card in cards:
            for block in card.get("content") or []:
                for r in block.get("content") or []:
                    title = (r.get("word") or "").strip()
                    if not title:
                        continue
                    out.append(self.make(title, r.get("hotScore"), r.get("url") or ""))
        return out
