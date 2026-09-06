"""抓取器包 —— 导入即完成注册."""

from .base import BaseCrawler, get_crawler, register, registry  # noqa: F401
from . import official, flash, ak_source  # noqa: F401,E402

__all__ = ["BaseCrawler", "get_crawler", "register", "registry", "build_all", "OFFICIAL", "FLASH"]

OFFICIAL = ["gov", "pbc", "csrc", "nfra", "safe"]
FLASH = ["em_flash", "em_breakfast", "cls", "ths_flash", "sina_flash"]


def build_all(only: list[str] | None = None, pages: int = 1, fetcher=None):  # noqa: ANN001
    """实例化抓取器. only 可传 source_id 列表, 或 'official' / 'flash' / 'all'."""
    reg = registry()
    if not only or only == ["all"]:
        ids = list(reg)
    else:
        ids = []
        for name in only:
            if name == "official":
                ids += OFFICIAL
            elif name == "flash":
                ids += FLASH
            elif name in reg:
                ids.append(name)
    seen, ordered = set(), []
    for i in ids:
        if i in reg and i not in seen:
            seen.add(i)
            ordered.append(i)
    return [reg[i](fetcher=fetcher, pages=pages) for i in ordered]
