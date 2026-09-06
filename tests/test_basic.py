"""基础自检：数据文件格式、打分逻辑、存储去重、报告生成。"""

from __future__ import annotations

import pytest

from finradar.analysis.tagger import score_and_tag
from finradar.analysis.report import build_html, build_markdown
from finradar.knowledge import glossary as G
from finradar.knowledge import qbank as Q
from finradar.models import NewsItem
from finradar.storage import Store
from finradar.utils import parse_time


# ---------------------------------------------------------------- 数据完整性

def test_glossary_loads():
    terms = G.load_glossary()
    assert len(terms) >= 30, "词条数量过少"
    ids = [t.id for t in terms]
    assert len(ids) == len(set(ids)), f"词条 id 重复: {[i for i in ids if ids.count(i) > 1]}"


@pytest.mark.parametrize("t", G.load_glossary(), ids=lambda t: t.id)
def test_term_fields(t):
    assert t.term and t.category and t.definition, f"{t.id} 缺少必填字段"
    assert 1 <= t.heat <= 5
    assert t.exam_points, f"{t.id} 没有考点"
    assert t.interview_answer, f"{t.id} 没有口语化答法"


def test_questions_load():
    qs = Q.load_questions()
    assert len(qs) >= 40, "题目数量过少"
    ids = [q.id for q in qs]
    assert len(ids) == len(set(ids)), "题目 id 重复"


@pytest.mark.parametrize("q", Q.load_questions(), ids=lambda q: q.id)
def test_question_fields(q):
    assert q.question and q.answer, f"{q.id} 缺题干或答案"
    assert q.exam_point, f"{q.id} 没有考点"
    assert q.spoken, f"{q.id} 没有口语化表达"
    if q.qtype in ("single", "multi"):
        assert q.options and q.correct, f"{q.id} 选择题缺选项或答案"
        letters = {o.strip()[0] for o in q.options}
        assert set(q.correct) <= letters, f"{q.id} 正确项不在选项中"
    if q.qtype == "single":
        assert len(q.correct) == 1, f"{q.id} 单选题只能有一个答案"


def test_boards_and_categories():
    assert len(Q.boards()) >= 5
    assert len(G.categories()) >= 5


# ---------------------------------------------------------------- 打分

def test_scoring_policy_news_high():
    it = NewsItem(
        source="pbc", source_name="中国人民银行",
        title="中国人民银行决定下调金融机构存款准备金率0.5个百分点",
        summary="为保持流动性充裕，人民银行决定降准，释放长期资金。",
    )
    score_and_tag(it)
    assert it.policy_score >= 60
    assert "货币政策" in it.tags
    assert it.impact, "应该给出传导逻辑"


def test_scoring_noise_news_low():
    it = NewsItem(
        source="em_flash", source_name="快讯",
        title="午评：三大指数集体高开，某某概念股涨停，主力资金流入",
    )
    score_and_tag(it)
    assert it.policy_score < 30


def test_scoring_bounded():
    it = NewsItem(
        source="gov", source_name="政府网",
        title="国务院印发通知：降准降息LPR存款准备金率再贷款MLF逆回购公开市场操作社会融资规模",
    )
    score_and_tag(it)
    assert 0 <= it.policy_score <= 100


# ---------------------------------------------------------------- 存储

def test_store_dedup(tmp_path):
    db = tmp_path / "t.db"
    s = Store(db)
    a = NewsItem(source="x", source_name="X", title="同一条", url="http://a")
    b = NewsItem(source="x", source_name="X", title="同一条", url="http://a")
    c = NewsItem(source="x", source_name="X", title="另一条", url="http://b")
    assert s.save_news([a]) == 1
    assert s.save_news([b]) == 0  # 去重
    assert s.save_news([c]) == 1
    assert s.stats()["total"] == 2


def test_quiz_log(tmp_path):
    s = Store(tmp_path / "q.db")
    s.log_quiz("macro-001", "宏观与货币政策", False)
    s.log_quiz("macro-002", "宏观与货币政策", True)
    assert "macro-001" in s.wrong_qids()
    assert s.quiz_stats()["answered"] == 2


# ---------------------------------------------------------------- 工具

@pytest.mark.parametrize(
    "raw",
    ["2026-09-06 10:30:00", "2026/09/06", "20260906", 1757000000, "2026年9月6日"],
)
def test_parse_time(raw):
    out = parse_time(raw)
    assert len(out) == 19 and out[4] == "-"


# ---------------------------------------------------------------- 报告

def test_report_renders():
    rows = [
        {
            "title": "中国人民银行下调存款准备金率", "url": "http://x",
            "pub_date": "2026-09-05", "source_name": "中国人民银行",
            "policy_score": 88.0, "tags": ["货币政策"],
            "hotwords": ["降准", "存款准备金率"], "summary": "",
            "impact": "释放长期资金", "doc_no": "",
        }
    ]
    md = build_markdown(rows)
    html = build_html(rows)
    assert "降准" in md and "<html" in html
