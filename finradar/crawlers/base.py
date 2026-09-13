"""抓取器基类与注册表."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import NewsItem
from ..utils import LOG, Fetcher

_REGISTRY: dict[str, type[BaseCrawler]] = {}


def register(cls: type[BaseCrawler]) -> type[BaseCrawler]:
    _REGISTRY[cls.source_id] = cls
    return cls


def registry() -> dict[str, type[BaseCrawler]]:
    return dict(_REGISTRY)


def get_crawler(source_id: str) -> type[BaseCrawler] | None:
    return _REGISTRY.get(source_id)


class BaseCrawler(ABC):
    """所有抓取器的父类.

    子类需要提供:
      source_id   数据源唯一 id
      source_name 中文名
      kind        official(官方政策) / flash(快讯) / aggregate(聚合)
      fetch()     返回 list[NewsItem]
    """

    source_id: str = "base"
    source_name: str = "基类"
    kind: str = "official"
    homepage: str = ""
    # 来源先验分 (0-30): 只表示"这条出自官方站/快讯", 是打分的下限而不是主体。
    # 真正的排序交给 config/keywords.yaml 的关键词信号 —— 早期版本把官方站设成
    # 70-80 分, 结果央行领导的一场例行会见也能顶到 100 分, 日报失去区分度。
    base_score: float = 0.0

    def __init__(self, fetcher: Fetcher | None = None, pages: int = 1) -> None:
        self.f = fetcher or Fetcher()
        self.pages = pages

    @abstractmethod
    def fetch(self) -> list[NewsItem]:
        ...

    def run(self) -> list[NewsItem]:
        try:
            items = self.fetch()
        except Exception as e:  # noqa: BLE001
            LOG.warning("[%s] 抓取异常: %s", self.source_id, e)
            return []
        for it in items:
            it.policy_score = max(it.policy_score, self.base_score)
        LOG.info("[%s] %s 抓到 %d 条", self.source_id, self.source_name, len(items))
        return items

    # 便捷构造
    def item(self, **kw) -> NewsItem:
        kw.setdefault("source", self.source_id)
        kw.setdefault("source_name", self.source_name)
        return NewsItem(**kw)
