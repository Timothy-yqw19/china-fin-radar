"""运行状态: 记住"上次跑到什么时候", 让 `finradar update` 能增量跑.

状态存成 workdir()/state.json, 内容形如:

    {
      "last_run": "2026-09-14T15:30:12+08:00",       # 上次成功跑完的时间(北京时间)
      "last_new_items": 821,                          # 上次新增条数
      "runs": 3,                                      # 累计运行次数
      "history": [{"at": "...", "mode": "incremental", "new": 821, "span": "2026-09-13 ~ 2026-09-14"}]
    }

为什么用文件而不是数据库表: 用户可以直接打开看, 也可以删掉重来(等于重置为"首次全量")。
"""

from __future__ import annotations

import json
from pathlib import Path

from .utils import now_cn, workdir

STATE_FILE = "state.json"


def state_path() -> Path:
    return workdir() / STATE_FILE


def load_state() -> dict:
    p = state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}


def save_state(state: dict) -> Path:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def record_run(state: dict, mode: str, new_items: int, span: str = "", extra: dict | None = None) -> dict:
    """记一次运行, 返回更新后的 state."""
    now = now_cn().isoformat(timespec="seconds")
    state = dict(state or {})
    hist = list(state.get("history") or [])
    entry = {"at": now, "mode": mode, "new": new_items, "span": span}
    if extra:
        entry.update(extra)
    hist.append(entry)
    state.update(
        {
            "last_run": now,
            "last_mode": mode,
            "last_new_items": new_items,
            "last_span": span,
            "runs": int(state.get("runs") or 0) + 1,
            "history": hist[-30:],  # 只留最近 30 次
        }
    )
    return state


def incremental_start(state: dict, default_years: int = 10) -> tuple[str, str]:
    """返回 (起始日期, 模式说明).

    首次运行(没有状态): 从 default_years 年前开始 —— 一次性把历史补齐;
    之后每次: 从上次运行日的**前一天**开始(重叠一天), 重复的靠 uid 去重。
    """
    from datetime import date, timedelta

    today = now_cn().date()
    last = (state or {}).get("last_run") or ""
    if not last:
        start = date(today.year - default_years, today.month, today.day)
        return start.isoformat(), "首次全量"
    try:
        last_date = date.fromisoformat(last[:10])
    except ValueError:
        last_date = today - timedelta(days=1)
    return (last_date - timedelta(days=1)).isoformat(), "增量"


def describe(state: dict) -> str:
    """给用户看的一行状态摘要."""
    if not state.get("last_run"):
        return "还没有运行过（第一次 finradar update 会自动补齐过去 10 年）"
    return (
        f"上次运行 {state.get('last_run')}（{state.get('last_mode','-')}，"
        f"新增 {state.get('last_new_items',0)} 条，覆盖 {state.get('last_span') or '-'}），"
        f"累计 {state.get('runs',0)} 次"
    )
