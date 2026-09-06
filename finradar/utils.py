"""HTTP / 时间 / 路径 等通用工具."""

from __future__ import annotations

import logging
import os
import random
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

LOG = logging.getLogger("finradar")

PKG_DIR = Path(__file__).resolve().parent
ROOT_DIR = PKG_DIR.parent
DATA_DIR = PKG_DIR / "data"
CONFIG_DIR = ROOT_DIR / "config"

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
]


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def workdir() -> Path:
    """数据落盘目录, 可用 FINRADAR_HOME 覆盖."""
    p = Path(os.environ.get("FINRADAR_HOME", ROOT_DIR / "output"))
    p.mkdir(parents=True, exist_ok=True)
    return p


class Fetcher:
    """带重试、随机 UA、限速的轻量 HTTP 客户端.

    国内政务网站普遍有 WAF, 因此:
      - 默认带 Referer 和完整浏览器头
      - 失败自动重试并退避
      - 支持 verify=False (部分政务站证书链不全)
    """

    def __init__(
        self,
        timeout: int = 20,
        retries: int = 3,
        sleep: float = 0.8,
        verify: bool = True,
        proxies: dict | None = None,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.sleep = sleep
        self.verify = verify
        self.session = requests.Session()
        if proxies:
            self.session.proxies.update(proxies)

    def _headers(self, referer: str | None = None, extra: dict | None = None) -> dict:
        h = {
            "User-Agent": random.choice(UA_POOL),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
        }
        if referer:
            h["Referer"] = referer
        if extra:
            h.update(extra)
        return h

    def get(
        self,
        url: str,
        params: dict | None = None,
        referer: str | None = None,
        headers: dict | None = None,
        encoding: str | None = None,
    ) -> requests.Response | None:
        last: Exception | None = None
        for i in range(self.retries):
            try:
                r = self.session.get(
                    url,
                    params=params,
                    headers=self._headers(referer, headers),
                    timeout=self.timeout,
                    verify=self.verify,
                )
                if r.status_code == 200:
                    if encoding:
                        r.encoding = encoding
                    elif r.encoding in (None, "ISO-8859-1"):
                        r.encoding = r.apparent_encoding or "utf-8"
                    time.sleep(self.sleep)
                    return r
                LOG.debug("GET %s -> HTTP %s (第%d次)", url, r.status_code, i + 1)
            except Exception as e:  # noqa: BLE001
                last = e
                LOG.debug("GET %s 失败: %s (第%d次)", url, e, i + 1)
            time.sleep(self.sleep * (i + 1) * 1.5)
        if last:
            LOG.warning("放弃抓取 %s: %s", url, last)
        else:
            LOG.warning("放弃抓取 %s: HTTP 状态码非 200", url)
        return None

    def get_json(self, url: str, **kw: Any) -> Any | None:
        r = self.get(url, **kw)
        if r is None:
            return None
        text = r.text.strip()
        # 处理 jsonp: callback({...})
        if text and not text.startswith(("{", "[")):
            lb, rb = text.find("("), text.rfind(")")
            if 0 <= lb < rb:
                text = text[lb + 1 : rb]
        try:
            import json

            return json.loads(text)
        except Exception as e:  # noqa: BLE001
            LOG.warning("JSON 解析失败 %s: %s", url, e)
            return None


# ---------------------------------------------------------------- 时间处理

def parse_time(raw: Any, default_today: bool = True) -> str:
    """把各种时间表示统一成 'YYYY-MM-DD HH:MM:SS'."""
    if raw is None or raw == "":
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S") if default_today else ""
    if isinstance(raw, (int, float)) or (isinstance(raw, str) and raw.isdigit() and len(raw) >= 10):
        ts = float(raw)
        if ts > 1e12:  # 毫秒
            ts /= 1000
        try:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        except (OSError, ValueError, OverflowError):
            pass
    s = str(raw).strip().replace("/", "-").replace("年", "-").replace("月", "-").replace("日", "")
    s = s.replace("T", " ").split(".")[0]
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y-%m-%d %H",
        "%Y%m%d",
        "%m-%d %H:%M",
        "%H:%M",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            if fmt in ("%m-%d %H:%M", "%H:%M"):
                now = datetime.now()
                dt = dt.replace(year=now.year, month=dt.month or now.month, day=dt.day or now.day)
                if fmt == "%H:%M":
                    dt = dt.replace(month=now.month, day=now.day)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S") if default_today else str(raw)


def days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")


def abs_url(base: str, href: str) -> str:
    from urllib.parse import urljoin

    if not href:
        return ""
    if href.startswith("//"):
        return "https:" + href
    return urljoin(base, href)
