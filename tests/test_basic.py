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


# ---------------------------------------------------------------- 2026-09-12 实测修正

def test_routine_news_scores_below_policy():
    """例行会见/座谈有来源分撑着, 但不能和正式发文一个量级。"""
    routine = NewsItem(
        source="pbc", source_name="中国人民银行",
        title="中国人民银行副行长会见标普全球总裁",
    )
    routine.policy_score = 28.0  # 来源先验分, 相当于爬虫跑完的状态
    score_and_tag(routine)
    policy = NewsItem(
        source="gov", source_name="政府网",
        title="国务院办公厅关于做好金融“五篇大文章”的指导意见",
    )
    policy.policy_score = 30.0
    score_and_tag(policy)
    assert routine.policy_score < 55, f"例行新闻分过高: {routine.policy_score}"
    assert policy.policy_score > routine.policy_score
    assert policy.policy_score >= 75
    # 负向类别的命中词不该混进热词/标签
    assert not {"会见", "涨停", "午评"} & set(routine.hotwords)


def test_source_priors_stay_low():
    """来源先验分只作为下限: 官方站 70-80 分的老口径会让日报失去区分度。"""
    from finradar.crawlers import registry, source_base_score

    for sid in registry():
        assert source_base_score(sid) <= 30, f"{sid} 来源先验分过高"


def test_dedupe_merges_cross_source():
    from finradar.analysis.report import dedupe_rows

    rows = [
        {
            "title": "关于印发《关于加快农业保险高质量发展的实施方案》的通知",
            "source_name": "中国政府网·政策文件库", "policy_score": 85.0,
            "doc_no": "财金〔2026〕88号", "tags": [], "hotwords": [],
        },
        {
            "title": "《关于加快农业保险高质量发展的实施方案》印发",
            "source_name": "财政部", "policy_score": 57.0, "doc_no": "",
            "tags": [], "hotwords": [],
        },
        {
            "title": "证监会就修订《管理办法》公开征求意见",
            "source_name": "中国证券监督管理委员会", "policy_score": 90.0,
            "doc_no": "", "tags": [], "hotwords": [],
        },
        {
            "title": "证监会就修订《管理办法》公开征求意见",
            "source_name": "财联社·电报", "policy_score": 62.0, "doc_no": "",
            "tags": [], "hotwords": [],
        },
    ]
    out = dedupe_rows(rows)
    assert len(out) == 2
    top = out[0]
    assert top["source_name"] == "中国证券监督管理委员会"
    assert "财联社·电报" in top["also_from"]


def test_macro_latest_rows_ordering():
    """akshare 各接口排序方向不一致: 取最新几行不能无脑 head/tail。"""
    pd = pytest.importorskip("pandas")
    from finradar.crawlers.ak_source import latest_rows

    newest_first = pd.DataFrame(
        {"月份": ["2026年08月", "2026年07月", "2026年06月"], "值": [1, 2, 3]}
    )
    oldest_first = pd.DataFrame(
        {"TRADE_DATE": ["1991-04-21", "2026-07-20", "2026-08-20"], "值": [1, 2, 3]}
    )
    assert list(latest_rows(newest_first, 2)["月份"]) == ["2026年08月", "2026年07月"]
    assert list(latest_rows(oldest_first, 1)["TRADE_DATE"]) == ["2026-08-20"]
    # 形如 201501 的月份串
    compact = pd.DataFrame({"月份": [201501, 202608, 202512], "值": [1, 2, 3]})
    assert list(latest_rows(compact, 1)["月份"]) == [202608]


def test_dates_follow_beijing_time():
    """境内站点按北京时间发布: 用本机时区(可能是纽约)会凭空差一天。"""
    from finradar.utils import CN_TZ, days_ago, now_cn, parse_time

    assert CN_TZ.utcoffset(None).total_seconds() == 8 * 3600
    now = now_cn()
    assert now.utcoffset().total_seconds() == 8 * 3600
    # 秒级时间戳按北京时间解释
    assert parse_time(1789000000).startswith("2026-09")
    assert len(days_ago(3)) == 10


def test_config_sources_are_wired_up():
    """回归: config/sources.yaml 曾经是死配置, ConfigListCrawler 从未被实例化。"""
    from finradar.crawlers import build_all, config_source_ids

    ids = config_source_ids()
    assert ids, "sources.yaml 没有读到任何数据源"
    built = {c.source_id for c in build_all(["all"], pages=1)}
    assert set(ids) <= built, f"配置源没有进 build_all: {set(ids) - built}"
    assert {"mof", "ndrc", "stats", "amac"} <= built


def test_discover_new_words_filters_noise():
    from finradar.analysis.hotwords import discover_new_words

    texts = [
        "当地时间9月10日下午，央行开展买断式逆回购操作维护流动性",
        "本次买断式逆回购操作规模为1400亿元，期限三个月",
        "市场关注买断式逆回购操作力度，资金面整体平稳",
        "机构认为买断式逆回购操作节奏将影响短端利率",
        "公开市场业务交易公告显示，买断式逆回购操作延续净投放",
        "央行公告称买断式逆回购操作延续，投放中长期流动性",
    ]
    words = dict(discover_new_words(texts, top=20, min_len=3, min_count=2))
    assert "买断式逆回购操作" in words
    # 碎片(更长的词几乎同样常见时会顶掉短的)、日期碎片、单位都不该出现
    for junk in ("式逆回购", "回购操作", "日下午", "日报道", "亿元", "1500"):
        assert junk not in words, f"碎片词混进去了: {junk}"
