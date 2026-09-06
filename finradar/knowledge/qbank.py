"""题库加载与检索."""

from __future__ import annotations

import functools
from pathlib import Path

import yaml

from ..models import Question
from ..utils import DATA_DIR

QUESTION_DIR = DATA_DIR / "questions"


@functools.lru_cache(maxsize=1)
def load_questions(directory: str | None = None) -> tuple[Question, ...]:
    d = Path(directory) if directory else QUESTION_DIR
    qs: list[Question] = []
    allowed = Question.__dataclass_fields__.keys()  # type: ignore[attr-defined]
    for f in sorted(d.glob("*.yaml")):
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        board = doc.get("board", f.stem)
        for i, raw in enumerate(doc.get("questions") or [], 1):
            raw.setdefault("board", board)
            raw.setdefault("id", f"{f.stem}-{i:03d}")
            raw.setdefault("qtype", "short")
            qs.append(Question(**{k: v for k, v in raw.items() if k in allowed}))
    return tuple(qs)


def boards() -> list[str]:
    seen: list[str] = []
    for q in load_questions():
        if q.board not in seen:
            seen.append(q.board)
    return seen


def select(
    board: str | None = None,
    institution: str | None = None,
    qtype: str | None = None,
    difficulty: int | None = None,
    tag: str | None = None,
    ids: list[str] | None = None,
) -> list[Question]:
    res = []
    for q in load_questions():
        if ids is not None and q.id not in ids:
            continue
        if board and board not in q.board:
            continue
        if institution and q.institutions and institution not in q.institutions:
            continue
        if qtype and q.qtype != qtype:
            continue
        if difficulty and q.difficulty != difficulty:
            continue
        if tag and tag not in q.tags:
            continue
        res.append(q)
    return res


def render_question(q: Question, show_answer: bool = True, wide: int = 88) -> str:
    import textwrap

    def para(label: str, text: str) -> list[str]:
        if not text:
            return []
        body = textwrap.fill(" ".join(str(text).split()), wide, subsequent_indent="  ")
        return [f"【{label}】", f"  {body}", ""]

    out = [
        "-" * wide,
        f"[{q.id}] {q.board} · {'难度' + '●' * q.difficulty}"
        + (f" · {'/'.join(q.institutions)}" if q.institutions else ""),
        "-" * wide,
        textwrap.fill(q.question, wide),
        "",
    ]
    if q.options:
        out += [f"  {o}" for o in q.options] + [""]
    if not show_answer:
        return "\n".join(out)
    if q.correct:
        out += [f"【正确选项】 {'、'.join(q.correct)}", ""]
    out += para("考点", q.exam_point)
    out += para("标准答案", q.answer)
    out += para("面试口语化表达", q.spoken)
    if q.followups:
        out += ["【追问延伸】"]
        for fu in q.followups:
            out.append(f"  Q: {fu.get('q','')}")
            body = textwrap.fill(
                " ".join(str(fu.get("a", "")).split()), wide - 6, subsequent_indent="     "
            )
            out += [f"  A: {body}", ""]
    return "\n".join(out)
