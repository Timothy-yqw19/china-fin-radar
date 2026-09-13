"""把英文条目标题翻成中文（让报告是完整可读的中文）.

三个后端:
  * mymemory  —— 免费公共翻译接口, 无需 key（默认）。匿名额度有限, 所以内置
                 字数预算与缓存, 只翻标题这类短文本。
  * codex     —— 调用本机 codex CLI 让模型批量翻译, 质量最好; 但要求
                 codex 命令可用（终端里 `codex exec` 能跑）, 且会消耗额度。
  * none      —— 不翻译。

缓存写在 output/translate_cache.json: 同一个标题只翻一次, 重复生成报告不再消耗额度。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

from .utils import LOG, workdir

CACHE_FILE = "translate_cache.json"
DAILY_CHAR_BUDGET = 4000  # mymemory 匿名额度约 5000 字符/天, 留点余量


def _cache_path() -> Path:
    return workdir() / CACHE_FILE


def load_cache() -> dict:
    p = _cache_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}


def save_cache(cache: dict) -> None:
    try:
        _cache_path().write_text(
            json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except OSError as e:  # noqa: BLE001
        LOG.debug("翻译缓存写入失败: %s", e)


def _key(text: str, backend: str) -> str:
    return hashlib.md5(f"{backend}:{text}".encode()).hexdigest()


def _used_today(cache: dict) -> int:
    today = time.strftime("%Y-%m-%d")
    return int(((cache.get("_budget") or {}).get(today)) or 0)


def _add_used(cache: dict, n: int) -> None:
    today = time.strftime("%Y-%m-%d")
    budget = dict(cache.get("_budget") or {})
    budget[today] = int(budget.get(today) or 0) + n
    # 只留最近 7 天
    for k in sorted(budget)[:-7]:
        budget.pop(k, None)
    cache["_budget"] = budget


def _mymemory(text: str, timeout: int = 20) -> str:
    import requests

    r = requests.get(
        "https://api.mymemory.translated.net/get",
        params={"q": text[:480], "langpair": "en|zh-CN"},
        timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    data = r.json()
    zh = ((data.get("responseData") or {}).get("translatedText") or "").strip()
    if not zh or "MYMEMORY WARNING" in zh.upper():
        raise RuntimeError(data.get("responseDetails") or "空译文")
    return zh


def _codex(texts: list[str], timeout: int = 240) -> list[str]:
    """用本机 codex CLI 批量翻译(一次调用翻多条, 省额度也更快)."""
    exe = "/Applications/ChatGPT.app/Contents/Resources/codex"
    if not Path(exe).exists():
        exe = "codex"
    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
    prompt = (
        "把下面的英文新闻标题逐条翻成简洁、专业的中文（金融语境）。"
        "严格按 `序号. 译文` 的格式输出，每条一行，不要解释、不要合并：\n\n" + numbered
    )
    out = subprocess.run(
        [exe, "exec", "--skip-git-repo-check", "-m", "deepseek-v4-flash", prompt, "-o", "-"],
        capture_output=True, text=True, timeout=timeout,
    )
    lines = [ln.strip() for ln in (out.stdout or "").splitlines() if ln.strip()]
    res: list[str] = []
    for ln in lines:
        # 去掉 "1. " 这样的序号
        if "." in ln[:4]:
            ln = ln.split(".", 1)[1].strip()
        res.append(ln)
    if len(res) != len(texts):
        raise RuntimeError(f"codex 返回 {len(res)} 条, 期望 {len(texts)} 条")
    return res


def translate_many(
    texts: list[str],
    backend: str = "auto",
    timeout: int = 20,
) -> dict[str, str]:
    """批量翻译, 返回 {原文: 译文}. 命中缓存的不再请求.

    backend=auto: 有缓存就用缓存; 否则用 mymemory(省事), codex 需要显式指定。
    """
    texts = [t for t in dict.fromkeys(texts) if t and not _is_chinese(t)]
    if not texts or backend == "none":
        return {}
    cache = load_cache()
    out: dict[str, str] = {}
    todo: list[str] = []
    for t in texts:
        hit = cache.get(_key(t, "mymemory")) or cache.get(_key(t, "codex"))
        if hit:
            out[t] = hit.get("zh", "") if isinstance(hit, dict) else str(hit)
        else:
            todo.append(t)
    if not todo:
        return out

    backend = backend if backend != "auto" else "mymemory"
    if backend == "codex":
        try:
            for t, zh in zip(todo, _codex(todo)):
                out[t] = zh
                cache[_key(t, "codex")] = {"zh": zh, "at": time.strftime("%Y-%m-%d %H:%M")}
        except Exception as e:  # noqa: BLE001
            LOG.warning("codex 翻译失败(%s), 回退 mymemory", e)
            backend = "mymemory"

    if backend == "mymemory":
        used = _used_today(cache)
        for t in todo:
            if used + len(t) > DAILY_CHAR_BUDGET:
                LOG.warning("今天翻译额度已用满(%d 字符), 剩余条目跳过翻译", used)
                break
            try:
                zh = _mymemory(t, timeout=timeout)
                out[t] = zh
                cache[_key(t, "mymemory")] = {"zh": zh, "at": time.strftime("%Y-%m-%d %H:%M")}
                used += len(t)
                _add_used(cache, len(t))
                time.sleep(0.4)  # 公共接口, 限速
            except Exception as e:  # noqa: BLE001
                LOG.debug("翻译失败: %s", e)
    save_cache(cache)
    return out


def _is_chinese(text: str) -> bool:
    zh = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return zh >= max(4, len(text) // 4)
