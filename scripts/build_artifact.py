"""把词库/题库注入 HTML 模板，产出可发布的单文件网页。

    python scripts/build_artifact.py [--out output/kb.html]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finradar.knowledge import glossary as G  # noqa: E402
from finradar.knowledge import qbank as Q  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "web" / "template.html"


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    if isinstance(o, str):
        return re.sub(r"\s+", " ", o).strip()
    if isinstance(o, (int, float)) or o is None:
        return o
    return str(o)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "output" / "kb.html"))
    a = ap.parse_args()

    data = {
        "terms": _clean([t.to_dict() for t in G.load_glossary()]),
        "questions": _clean([q.to_dict() for q in Q.load_questions()]),
    }
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    html = TEMPLATE.read_text(encoding="utf-8").replace("__KB_DATA__", payload)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(
        f"已生成 {out}（{len(html):,} 字符；"
        f"{len(data['terms'])} 词 / {len(data['questions'])} 题）"
    )


if __name__ == "__main__":
    main()
