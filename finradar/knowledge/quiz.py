"""刷题引擎: 随机抽题、按板块练习、错题本、自评."""

from __future__ import annotations

import random

from ..models import Question
from ..storage import Store
from . import qbank


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return "q"


def run_quiz(
    n: int = 10,
    board: str | None = None,
    institution: str | None = None,
    qtype: str | None = None,
    wrong_only: bool = False,
    store: Store | None = None,
    seed: int | None = None,
) -> dict:
    store = store or Store()
    ids = store.wrong_qids() if wrong_only else None
    pool = qbank.select(board=board, institution=institution, qtype=qtype, ids=ids)
    if not pool:
        print("没有匹配的题目。用 `finradar boards` 看有哪些板块。")
        return {"answered": 0}

    rng = random.Random(seed)
    rng.shuffle(pool)
    pool = pool[:n]

    right = 0
    for i, q in enumerate(pool, 1):
        print(f"\n\n===== 第 {i}/{len(pool)} 题 =====")
        print(qbank.render_question(q, show_answer=False))
        if q.qtype in ("single", "multi") and q.correct:
            ans = _ask("你的答案(如 A 或 AC，回车跳过，q 退出)： ").upper()
            if ans == "Q":
                break
            ok = sorted(set(ans)) == sorted({c.upper() for c in q.correct})
        else:
            _ask("先自己组织一下答案，想好后按回车看解析（q 退出）… ")
            print(qbank.render_question(q, show_answer=True))
            v = _ask("自评：答对了吗？(y/n，回车=n) ").lower()
            if v == "q":
                break
            ok = v == "y"
        if q.qtype in ("single", "multi"):
            print(qbank.render_question(q, show_answer=True))
        print("✅ 正确" if ok else "❌ 记入错题本")
        right += int(ok)
        store.log_quiz(q.id, q.board, ok)

    done = i if "i" in dir() else 0
    print(f"\n本轮完成 {done} 题，答对 {right} 题。")
    print("用 `finradar quiz --wrong` 只刷错题。")
    return {"answered": done, "correct": right}


def daily_set(n: int = 5, seed: int | None = None) -> list[Question]:
    """每日一练: 各板块均衡抽题."""
    rng = random.Random(seed)
    out: list[Question] = []
    bs = qbank.boards()
    rng.shuffle(bs)
    for b in bs:
        pool = qbank.select(board=b)
        if pool:
            out.append(rng.choice(pool))
        if len(out) >= n:
            break
    return out
