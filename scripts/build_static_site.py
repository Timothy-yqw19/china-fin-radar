"""只用仓库里的数据文件构建静态网页（不依赖本地抓取数据库）。

用途: GitHub Pages / CI 里没有你的 SQLite 库, 但专题、词库、题库都在仓库里,
所以可以生成一个"减去语料统计"的版本给外部访问者看。

    python scripts/build_static_site.py --out-dir public
    → public/index.html  专题报道 + 洞察速查 + 名词档案（语料统计为空）
    → public/kb.html     词库刷题页（52 词 / 62 题）
    → public/.nojekyll
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finradar.analysis.insight_site import build_payload, render_site  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(ROOT / "public"))
    a = ap.parse_args()

    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 1) 专题洞察网页: rows=[] 表示"没有语料统计", 其余内容(专题/报道/名词/题库)照旧
    payload = build_payload([], candidates=False, reports_url="docs/reports/index.html")
    (out / "index.html").write_text(render_site(payload), encoding="utf-8")

    # 2) 词库刷题页: 复用现成的构建脚本(按文件路径加载, 免得依赖 scripts 是包)
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_artifact", ROOT / "scripts" / "build_artifact.py"
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    saved_argv, sys.argv = sys.argv, ["build_artifact", "--out", str(out / "kb.html")]
    try:
        mod.main()
    finally:
        sys.argv = saved_argv

    # 3) GitHub Pages 默认会跑 Jekyll, 关掉它(下划线开头的文件会被忽略)
    (out / ".nojekyll").write_text("", encoding="utf-8")

    # 4) 把 docs/ 整个带上: 手册、使用说明、以及 finradar publish 归档的通报
    docs = ROOT / "docs"
    if docs.exists():
        shutil.copytree(docs, out / "docs", dirs_exist_ok=True)

    print(f"已生成静态站点 → {out}")
    print(f"  index.html（专题 {len(payload['insights'])} 条 / 名词 {len(payload['terms'])} 条）")
    print("  kb.html（词库刷题页）")


if __name__ == "__main__":
    main()
