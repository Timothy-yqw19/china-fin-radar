"""抓取器包 —— 导入即完成注册."""

from __future__ import annotations

import functools

import yaml

from ..utils import CONFIG_DIR, LOG
from .base import BaseCrawler, get_crawler, register, registry  # noqa: F401
from . import official, flash, ak_source, trends  # noqa: F401,E402
from .official import ConfigListCrawler  # noqa: E402

__all__ = [
    "BaseCrawler", "get_crawler", "register", "registry", "build_all",
    "OFFICIAL", "FLASH", "TRENDS",
]

OFFICIAL = ["gov", "pbc", "csrc", "nfra", "safe"]
FLASH = ["em_flash", "em_breakfast", "cls", "ths_flash", "sina_flash"]
# 大众热榜: 噪音大, 默认不抓, 用 --source trends 或 --source all
TRENDS = ["toutiao_hot", "douyin_hot", "baidu_hot"]


@functools.lru_cache(maxsize=1)
def config_sources() -> tuple[dict, ...]:
    """读 config/sources.yaml —— 配置化的 HTML 列表源, 加站点不用写代码."""
    path = CONFIG_DIR / "sources.yaml"
    if not path.exists():
        return ()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        LOG.warning("sources.yaml 解析失败: %s", e)
        return ()
    return tuple(c for c in (raw.get("sources") or []) if c.get("id"))


def config_source_ids() -> list[str]:
    return [str(c["id"]) for c in config_sources()]


def source_base_score(source_id: str) -> float:
    """查一个数据源的先验分 (重算打分时要还原这个下限)."""
    cls = registry().get(source_id)
    if cls is not None:
        return float(getattr(cls, "base_score", 0.0))
    for cfg in config_sources():
        if str(cfg.get("id")) == source_id:
            return float(cfg.get("base_score", 25))
    return 0.0


def build_all(only: list[str] | None = None, pages: int = 1, fetcher=None):  # noqa: ANN001
    """实例化抓取器. only 可传 source_id 列表, 或 'official' / 'flash' / 'config' / 'all'."""
    reg = registry()
    want_all = not only or only == ["all"]
    if want_all:
        ids = list(reg)
    else:
        ids = []
        for name in only:
            if name == "official":
                ids += OFFICIAL
            elif name == "flash":
                ids += FLASH
            elif name == "trends":
                ids += TRENDS
            elif name in reg:
                ids.append(name)
    seen, ordered = set(), []
    for i in ids:
        if i in reg and i not in seen:
            seen.add(i)
            ordered.append(i)
    out = [reg[i](fetcher=fetcher, pages=pages) for i in ordered]

    # 配置化数据源: all / config / official 时一并带上, 或按 id 精确指定
    cfg_list = config_sources()
    for cfg in cfg_list:
        cid = str(cfg["id"])
        if cid in seen:
            continue
        kind = cfg.get("kind", "official")
        wanted = want_all or "config" in (only or []) or cid in (only or []) or (
            "official" in (only or []) and kind == "official"
        )
        if not wanted:
            continue
        seen.add(cid)
        out.append(ConfigListCrawler(cfg, fetcher=fetcher, pages=pages))
    return out
