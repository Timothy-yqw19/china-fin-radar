"""把面试专题长文渲染成排版页面。

`docs/` 下的 .md 在 GitHub Pages 上是纯文本（浏览器只显示源码），
所以长文另外出一份 .html。CI 里由 `scripts/build_static_site.py` 自动调用，
本脚本用于本地预览或单独重出：

    python scripts/build_interview_doc.py                 # → public/docs/银行面试_投行与金融市场线.html
    python scripts/build_interview_doc.py --out /tmp/x.html
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finradar.analysis.doc_page import render_doc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MD = ROOT / "docs" / "银行面试_投行与金融市场线.md"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", default=str(DEFAULT_MD), help="源 Markdown 路径")
    ap.add_argument(
        "--out",
        default="",
        help="输出 HTML 路径（默认 public/docs/ 下同名 .html）",
    )
    a = ap.parse_args()

    md = Path(a.md)
    if not md.exists():
        sys.exit(f"找不到源文件：{md}")
    out = Path(a.out) if a.out else ROOT / "public" / "docs" / md.with_suffix(".html").name

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_doc(md), encoding="utf-8")
    print(f"已生成排版页面 → {out}（{out.stat().st_size:,} 字节）")


if __name__ == "__main__":
    main()
