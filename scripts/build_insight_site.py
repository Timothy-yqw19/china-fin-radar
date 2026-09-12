"""生成「金融政策洞察」单文件网页: 专题洞察 + 名词档案 + 候选新词。

    python scripts/build_insight_site.py [--out output/insights.html] [--min-score 30]

数据来源三块:
  * 专题洞察 finradar/data/insights/*.yaml（人工写的脉络与展望）
  * 名词档案 finradar/data/glossary/*.yaml + 库内语料统计（自动生成, 覆盖全部词条）
  * 候选新词 从政策语料里挖出来的、词库还没有的提法（待人工确认）

真正的组装逻辑在 finradar/analysis/insight_site.py, 命令行 `finradar insight --html` 复用同一套。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finradar.analysis.insight_site import build_payload, render_site  # noqa: E402
from finradar.storage import Store  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "output" / "insights.html"))
    ap.add_argument("--min-score", type=float, default=30.0, help="关联文件的最低政策分")
    ap.add_argument("--db", default=None)
    a = ap.parse_args()

    rows = Store(a.db).query(limit=10**6)
    payload = build_payload(rows, min_score=a.min_score)
    html = render_site(payload)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(
        f"已生成 {out}（{len(html):,} 字符；"
        f"{len(payload['insights'])} 条专题 / {len(payload['terms'])} 条名词档案 / "
        f"{len(payload['candidates'])} 个候选新词）"
    )


if __name__ == "__main__":
    main()
