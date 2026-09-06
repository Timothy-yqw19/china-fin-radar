"""由词库 + 题库生成《秋招金融知识手册》(Markdown)。

    python scripts/build_docs.py            # 输出到 docs/
    python scripts/build_docs.py --out X.md
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finradar.knowledge import glossary as G  # noqa: E402
from finradar.knowledge import qbank as Q  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _clean(s) -> str:
    return " ".join(str(s or "").split())


def build() -> str:
    terms = G.load_glossary()
    qs = Q.load_questions()
    now = datetime.now().strftime("%Y-%m-%d")

    L: list[str] = [
        "# 秋招金融知识手册",
        "",
        f"> 由 `finradar` 词库与题库自动生成 · {now} · "
        f"{len(terms)} 个热词 / {len(qs)} 道题",
        "",
        "> 使用说明：先看**第一部分热词库**建立框架，再用**第二部分题库**自测。",
        "> 每个词条的「面试口语化答法」是可以直接背下来说出口的，控制在 60–90 秒。",
        "> 所有数字都标了截止时点，答题时说清 as-of 比记错数字安全。",
        "",
        "---",
        "",
        "## 目录",
        "",
    ]

    for c in G.categories():
        L.append(f"- 热词库 · {c}（{len(G.search(category=c))} 词）")
    for b in Q.boards():
        L.append(f"- 题库 · {b}（{len(Q.select(board=b))} 题）")
    L += ["", "---", "", "# 第一部分　金融热词与新名词库", ""]

    for cat in G.categories():
        L += [f"## {cat}", ""]
        for t in sorted(G.search(category=cat), key=lambda x: -x.heat):
            L += [
                f"### {t.term}　{'★' * t.heat}",
                "",
                f"`分类` {t.category}　`出现` {t.era}　`阶段` {t.plan}　"
                f"`适用` {'/'.join(t.institutions) or '通用'}",
                "",
            ]
            if t.aliases:
                L += [f"**别名/关联**：{'、'.join(t.aliases)}", ""]
            L += [f"**定义**　{_clean(t.definition)}", ""]
            if t.source:
                L += [f"**出处**　{_clean(t.source)}", ""]
            if t.why_hot:
                L += [f"**为什么热**　{_clean(t.why_hot)}", ""]
            if t.exam_points:
                L += ["**考点清单**", ""]
                L += [f"{i}. {_clean(p)}" for i, p in enumerate(t.exam_points, 1)]
                L.append("")
            if t.interview_answer:
                L += ["**面试口语化答法（60–90 秒）**", "", f"> {_clean(t.interview_answer)}", ""]
            if t.facts_snapshot:
                L += ["**数据快照**", "", "| 项 | 值 |", "|---|---|"]
                L += [f"| {k} | {_clean(v)} |" for k, v in t.facts_snapshot.items()]
                L.append("")
            if t.followups:
                L += ["**可能的追问**", ""]
                for fu in t.followups:
                    L += [f"- **Q：{_clean(fu.get('q'))}**", f"  A：{_clean(fu.get('a'))}"]
                L.append("")
            if t.related:
                L += [f"*关联词：{'、'.join(t.related)}*", ""]
            L += ["", "---", ""]

    L += ["", "# 第二部分　题库", ""]
    for board in Q.boards():
        L += [f"## {board}", ""]
        for q in Q.select(board=board):
            L += [
                f"### [{q.id}] {_clean(q.question)}",
                "",
                f"`题型` {q.qtype}　`难度` {'●' * q.difficulty}　"
                f"`适用` {'/'.join(q.institutions) or '通用'}",
                "",
            ]
            if q.options:
                L += [f"- {_clean(o)}" for o in q.options] + [""]
            if q.correct:
                L += [f"**正确选项**：{'、'.join(q.correct)}", ""]
            L += [f"**考点**　{_clean(q.exam_point)}", ""]
            L += [f"**标准答案**　{_clean(q.answer)}", ""]
            if q.spoken:
                L += ["**面试口语化表达**", "", f"> {_clean(q.spoken)}", ""]
            if q.followups:
                L += ["**追问延伸**", ""]
                for fu in q.followups:
                    L += [f"- **Q：{_clean(fu.get('q'))}**", f"  A：{_clean(fu.get('a'))}"]
                L.append("")
            L += ["---", ""]

    L += [
        "",
        "## 免责声明",
        "",
        "本手册用于求职学习与信息整理，不构成投资建议。政策解读以官方原文为准，",
        "数字请按标注的截止时点核对最新来源。",
        "",
    ]
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs" / "秋招金融知识手册.md"))
    a = ap.parse_args()
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = build()
    p.write_text(text, encoding="utf-8")
    print(f"已生成 {p}（{len(text):,} 字符）")


if __name__ == "__main__":
    main()
