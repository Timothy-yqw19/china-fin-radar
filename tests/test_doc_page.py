"""长文排版页（docs/*.md → *.html）的冒烟测试。

只校验结构与关键内容，不比对整页 HTML（样式会经常调）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from finradar.analysis.doc_page import render_doc

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "银行面试_投行与金融市场线.md"


def test_doc_exists() -> None:
    assert DOC.exists(), "面试专题的 Markdown 源文件不见了"


@pytest.fixture(scope="module")
def page() -> str:
    return render_doc(DOC, generated="2026-10-24 20:00")


def test_render_doc_basic_structure(page: str) -> None:
    assert page.startswith("<!DOCTYPE html>")
    assert "<title>" in page and "银行面试专题" in page
    # 正文段落、标题、表格、引用都真的转换了
    assert "<h2" in page
    assert "<h3" in page
    assert "<strong>" in page
    assert "<table>" in page
    assert 'class="tablewrap"' in page, "宽表格需要横向滚动容器"
    assert "<blockquote>" in page


def test_render_doc_toc_and_links(page: str) -> None:
    assert "目录" in page
    assert 'href="#' in page, "目录里应有锚点链接"
    assert 'href="../kb.html"' in page, "应有回刷题页的入口"
    assert 'href="../index.html"' in page
    assert "原始 Markdown" in page


def test_render_doc_no_unresolved_placeholders(page: str) -> None:
    for bad in ("[TOC]", "__STYLE__", "__INSIGHT_DATA__"):
        assert bad not in page
    # 一级标题只出现在页头，正文里不应重复
    assert page.count("<h1>") == 1


def test_render_doc_uses_committed_mtime_free_output() -> None:
    """同样输入两次渲染应完全一致（不把当前时间写进正文以外的地方）。"""
    a = render_doc(DOC, generated="2026-10-24 20:00")
    b = render_doc(DOC, generated="2026-10-24 20:00")
    assert a == b
