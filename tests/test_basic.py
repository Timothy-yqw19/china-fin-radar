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
    # 去掉"人民银行/中国人民银行"这类子串重复计分后, 这条是 57.5 分
    # (政策动作类动词一个都没有, 本来就该低于"印发/通知"类文件)
    assert it.policy_score >= 55
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


# ---------------------------------------------------------------- 时间窗口与多周期统计

def test_window_presets():
    from datetime import date

    from finradar.analysis.periods import parse_window, resolve_window

    assert parse_window("3m") == 90
    assert parse_window("6m") == 182
    assert parse_window("1y") == 365
    assert parse_window("5y") == 1826
    assert parse_window("90d") == 90
    assert parse_window("2w") == 14
    assert parse_window("all") is None
    with pytest.raises(ValueError):
        parse_window("半年")

    today = date(2026, 9, 13)
    since, label, span = resolve_window("1y", today=today)
    assert since == "2025-09-13" and span == 365 and "1 年" in label
    # --window 优先于 --days
    assert resolve_window("3m", 999, today=today)[2] == 90
    # 都不给时默认 30 天
    assert resolve_window(None, None, today=today)[2] == 30
    assert resolve_window("all", None, today=today)[0] is None


def test_auto_bucket_gets_coarser_with_window():
    """用户的原话是"以天为时间看太琐碎了" —— 长窗口必须自动换粗粒度。"""
    from finradar.analysis.periods import auto_bucket

    assert auto_bucket(7) == "week"
    assert auto_bucket(90) == "month"
    assert auto_bucket(182) == "month"
    assert auto_bucket(365) == "month"
    assert auto_bucket(1095) == "quarter"
    assert auto_bucket(1826) == "year"
    assert auto_bucket(None) == "year"


def test_period_key_and_fill():
    from finradar.analysis.periods import fill_periods, iter_periods, period_key

    assert period_key("2026-09-13", "month") == "2026-09"
    assert period_key("2026-09-13", "quarter") == "2026-Q3"
    assert period_key("2026-09-13", "year") == "2026"
    assert period_key("2026-09-13", "day") == "2026-09-13"
    assert period_key("2026-01-05", "quarter") == "2026-Q1"

    # 中间缺月份要补齐, 否则演变矩阵会"跳月"
    assert fill_periods(["2026-03", "2026-06"], "month") == [
        "2026-03", "2026-04", "2026-05", "2026-06",
    ]
    assert fill_periods(["2024-Q3", "2025-Q1"], "quarter") == [
        "2024-Q3", "2024-Q4", "2025-Q1",
    ]
    assert fill_periods(["2024", "2026"], "year") == ["2024", "2025", "2026"]

    segs = list(iter_periods("2024-11-01", "2025-02-15", "quarter"))
    assert [s[0] for s in segs] == ["2024-Q4", "2025-Q1"]
    assert segs[0][1] == "2024-11-01" and segs[0][2] == "2024-12-31"
    assert segs[1][2] == "2025-02-15"  # 最后一段截到结束日
    years = list(iter_periods("2021-01-01", "2023-06-30", "year"))
    assert [y[0] for y in years] == ["2021", "2022", "2023"]
    assert years[-1][2] == "2023-06-30"


def test_trend_matrix_and_render():
    from finradar.analysis.hotwords import display_width, render_trend, trend_matrix

    rows = [
        {"title": "金融强国与五篇大文章", "summary": "", "pub_date": "2021-03-01",
         "tags": [], "hotwords": []},
        {"title": "金融强国", "summary": "", "pub_date": "2023-05-01",
         "tags": [], "hotwords": []},
        {"title": "金融强国 五篇大文章", "summary": "", "pub_date": "2024-06-01",
         "tags": [], "hotwords": []},
    ]
    keys, out = trend_matrix(rows, bucket="year", top=5, periods=8)
    assert keys == ["2021", "2022", "2023", "2024"]
    words = {w: c for w, c in out}
    assert words["金融强国"] == [1, 0, 1, 1]

    txt = render_trend(rows, bucket="year", top=5, periods=8)
    lines = [ln for ln in txt.splitlines() if ln.strip()]
    assert "2021" in lines[0] and "2024" in lines[0]
    # 表头与数据行必须在同一列开始数字列: 用显示宽度(中文算 2 列)校验
    width = max(display_width(ln) for ln in lines if "·" in ln or "1" in ln)
    assert width > 0
    starts = set()
    for ln in lines[2:]:
        for ch in ("·", "1"):
            idx = ln.find(ch)
            if idx > 0:
                starts.add(display_width(ln[:idx]))
                break
    assert len(starts) <= 3, f"矩阵列没对齐: {starts}"


def test_report_time_grouping():
    from finradar.analysis.periods import period_key
    from finradar.analysis.report import _group_by_time

    rows = [
        {"title": f"文件{i}", "pub_date": d, "policy_score": 50 + i, "tags": [],
         "hotwords": [], "summary": "", "url": ""}
        for i, d in enumerate(["2025-12-01", "2024-06-01", "2025-03-05"])
    ]
    groups = _group_by_time(rows, "year")
    assert [k for k, _ in groups] == ["2024", "2025"]  # 按时间正序
    assert period_key("2025-03-05", "year") == "2025"

    md = build_markdown(rows, title="回顾", group_by="time", bucket="year")
    assert "热词演变" in md and "分年回顾" in md
    html = build_html(rows, title="回顾", group_by="time", bucket="year")
    assert "trend" in html and "分年回顾" in html


def test_keyword_substring_not_double_counted():
    """'中国人民银行'命中时不该再把'人民银行'算一次; 债券回购不该算成上市公司回购。"""
    it = NewsItem(
        source="gov", source_name="政府网",
        title="中国人民银行 中国证监会 国家外汇局公告〔2025〕第21号",
        summary="就境外机构投资者开展债券回购业务有关事项公告。",
    )
    score_and_tag(it)
    assert "人民银行" not in it.hotwords, it.hotwords
    assert "中国人民银行" in it.hotwords
    # 债券回购的语境下不该套用"上市公司回购"的传导逻辑
    assert "股东回报" not in (it.impact or "")

    listed = NewsItem(
        source="em_flash", source_name="快讯",
        title="某公司拟以注销式回购提升股东回报",
    )
    score_and_tag(listed)
    assert "股东回报" in (listed.impact or "")


def test_synonym_terms_merged_in_hotwords():
    from finradar.analysis.hotwords import _merge_substring_terms
    from collections import Counter

    cnt = Counter({
        '金融"五篇大文章"': 65,   # 词库口径的主词
        "五篇大文章": 20,          # 被长词完全覆盖 → 丢掉
        "逆回购": 15,              # 次数高于长词, 说明有独立语境 → 保留
        "买断式逆回购": 10,
        "金融强国": 10,
    })
    merged = _merge_substring_terms(cnt)
    assert "五篇大文章" not in merged
    assert merged['金融"五篇大文章"'] == 65
    assert "逆回购" in merged and merged["逆回购"] == 15
    assert merged["金融强国"] == 10


# ---------------------------------------------------------------- 专题洞察

def test_insights_load():
    from finradar.knowledge import insights as I

    items = I.load_insights()
    assert len(items) >= 4, "专题太少"
    ids = [i.id for i in items]
    assert len(ids) == len(set(ids)), "专题 id 重复"


@pytest.mark.parametrize("it", __import__("finradar.knowledge.insights", fromlist=["x"]).load_insights(),
                         ids=lambda t: t.id)
def test_insight_fields(it):
    """专题必须能回答"脉络是什么、现状如何、各方接下来可能做什么"。"""
    assert it.topic and it.thesis, f"{it.id} 缺 topic/thesis"
    assert len(it.actors) >= 2, f"{it.id} 主体太少"
    assert len(it.keywords) >= 3, f"{it.id} 关键词太少（无法关联库内新闻）"
    assert len(it.timeline) >= 3, f"{it.id} 脉络太短"
    for ev in it.timeline:
        assert ev.get("when") and ev.get("event"), f"{it.id} 脉络缺少时间或事件"
    assert len(it.outlook) >= 3, f"{it.id} 展望太短"
    for o in it.outlook:
        assert o.get("actor") and o.get("action"), f"{it.id} 展望缺少主体或动作"
    assert it.watchlist, f"{it.id} 没有观察信号"
    assert len(it.interview_take) >= 120, f"{it.id} 面试口径太短"


def test_insight_related_terms_exist_in_glossary():
    """专题里引用的热词必须真实存在, 否则"关联热词"会指向空气。"""
    from finradar.knowledge import insights as I

    names = {t.term for t in G.load_glossary()}
    for it in I.load_insights():
        for term in it.related_terms:
            assert term in names, f"{it.id} 引用了不存在的热词: {term}"


def test_insight_lookup_and_match():
    from finradar.analysis.insights import match_insights
    from finradar.knowledge import insights as I

    assert I.get("QDII") is not None            # 按关键词
    assert I.get("crossborder-qdii-qfii") is not None  # 按 id
    assert I.get("跨境投资开放").id == "crossborder-qdii-qfii"
    assert I.get("不存在的东西") is None

    matched = match_insights(
        "外汇局发放 QDII 额度，互联互通扩容，QFII 备案", I.load_insights()
    )
    assert matched and matched[0].id == "crossborder-qdii-qfii"


def test_insight_corpus_join_filters_junk():
    """低分快讯('算力互联互通')和 demo 假数据都不该进专题脉络。"""
    from finradar.analysis.insights import corpus_timeline, related_rows
    from finradar.knowledge import insights as I

    it = I.get("QDII")
    rows = [
        {"title": "外汇局发放新一轮 QDII 额度", "summary": "", "pub_date": "2026-08-31",
         "policy_score": 70, "url": "https://safe.gov.cn/x", "doc_no": ""},
        {"title": "国家算力互联互通节点启用", "summary": "", "pub_date": "2026-09-12",
         "policy_score": 8, "url": "https://news/x", "doc_no": ""},          # 低分噪声
        {"title": "外汇局发放 QDII 额度", "summary": "", "pub_date": "2026-08-31",
         "policy_score": 99, "url": "https://example.invalid/4", "doc_no": ""},  # demo 假数据
    ]
    rel = related_rows(it, rows)
    assert len(rel) == 1 and "safe.gov.cn" in rel[0]["url"]
    by_year, latest = corpus_timeline(it, rows)
    assert list(by_year) == ["2026"] and len(latest) == 1


def test_insight_render_and_report_section():
    from finradar.analysis.insights import render_brief, render_insight
    from finradar.knowledge import insights as I

    it = I.get("公募")
    txt = render_insight(it, [])
    for section in ("核心判断", "脉络（人工整理）", "现状快照", "未来可能的动作", "盯这几个信号"):
        assert section in txt, f"渲染缺少 {section}"
    assert "触发条件" in txt

    rows = [
        {"title": "关于印发《推动公募基金高质量发展行动方案》的通知",
         "summary": "", "pub_date": "2025-05-07", "policy_score": 94,
         "url": "https://csrc.gov.cn/x", "doc_no": "证监发〔2025〕21号", "tags": [], "hotwords": []},
    ]
    brief = render_brief(it, rows)
    assert "公募" in brief and "未来可能的动作" in brief

    md = build_markdown(rows, title="回顾", insights=[it])
    assert "专题洞察与展望" in md and "证监发〔2025〕21号" in md
    html = build_html(rows, title="回顾", insights=[it])
    assert "专题洞察与展望" in html and "<li>" in html


# ---------------------------------------------------------------- 名词档案与网页

def _rows_fixture():
    return [
        {"title": "关于扩大金融资产投资公司股权投资试点的通知", "summary": "",
         "pub_date": "2024-09-25", "policy_score": 80, "url": "https://gov.cn/a",
         "doc_no": "金办便函〔2024〕1210号", "source_name": "金融监管总局"},
        {"title": "金融资产投资公司股权投资试点扩容至更多城市", "summary": "",
         "pub_date": "2025-03-05", "policy_score": 82, "url": "https://gov.cn/b",
         "doc_no": "金办发〔2025〕19号", "source_name": "金融监管总局"},
        {"title": "多地推进股权投资试点，金融资产投资公司参与", "summary": "",
         "pub_date": "2025-06-01", "policy_score": 60, "url": "https://gov.cn/c",
         "doc_no": "", "source_name": "政府网"},
        {"title": "商务部关于印发某某办法的通知", "summary": "",
         "pub_date": "2025-06-02", "policy_score": 90, "url": "https://gov.cn/d",
         "doc_no": "", "source_name": "商务部"},
        # demo 假数据不该进任何统计
        {"title": "金融资产投资公司股权投资试点", "summary": "",
         "pub_date": "2026-09-03", "policy_score": 99, "url": "https://example.invalid/3",
         "doc_no": "", "source_name": "演示"},
    ]


def test_term_dossiers_join_corpus():
    from finradar.analysis.dossiers import term_dossiers
    from finradar.knowledge import insights as I

    terms = [t for t in G.load_glossary() if t.id == "bank_aic"]
    if not terms:
        pytest.skip("词库里没有 AIC 词条")
    d = term_dossiers(terms, _rows_fixture(), I.load_insights())[0]
    assert d["stats"]["n"] == 3, "demo 假数据和无关文件不该被算进来"
    assert d["stats"]["first_year"] == "2024"
    assert d["stats"]["latest_date"] == "2025-06-01"
    assert d["docs"] and "股权投资试点" in d["docs"][0]["title"]
    assert "tech-finance-aic-bond" in d["insights"], "应当关联到科技金融专题"


def test_mine_candidates_finds_policy_terms_and_drops_junk():
    from finradar.analysis.dossiers import mine_candidates

    rows = _rows_fixture() * 2  # 让词频够高
    got = {c["word"]: c for c in mine_candidates(rows, glossary_names=[], top=30, min_count=3)}
    assert any("股权投资试点" in w for w in got), f"没挖到政策提法: {list(got)[:10]}"
    for w in got:
        assert not w.endswith(("部", "委", "局", "总局", "办公厅")), f"机构名没滤掉: {w}"
        assert w not in ("管理办法", "实施方案", "行动方案"), f"公文类型没滤掉: {w}"
    # 候选要带年度走势与例句
    sample = next(iter(got.values()))
    assert sample["by_year"] and sample["samples"] and sample["first_year"]


def test_insight_site_payload_and_render():
    from finradar.analysis.insight_site import build_payload, render_site
    from finradar.knowledge import insights as I

    it = I.get("QDII")
    terms = [t for t in G.load_glossary() if t.term in set(it.related_terms)]
    payload = build_payload(
        _rows_fixture(), insights=[it], terms=terms, candidates=False, min_score=30
    )
    assert len(payload["insights"]) == 1 and len(payload["terms"]) == len(terms)
    assert payload["candidates"] == []
    assert payload["coverage"] and payload["n_news"] == len(_rows_fixture())

    html = render_site(payload)
    assert "__INSIGHT_DATA__" not in html and "__STYLE__" not in html
    assert "金融政策洞察" in html and "未来可能的动作" in html
    assert it.topic in html
    assert "var(--seal)" in html or "--seal" in html  # 样式被注入


# ---------------------------------------------------------------- 专题报道（成篇）

def test_all_insights_have_feature_article():
    """每个专题都要有成篇报道: 导语 + 至少 4 个段落, 且正文够长(不是条目堆)。"""
    from finradar.knowledge import insights as I

    for it in I.load_insights():
        feat = it.feature or {}
        assert feat.get("lead"), f"{it.id} 没有导语"
        secs = feat.get("sections") or []
        assert len(secs) >= 4, f"{it.id} 报道段落太少: {len(secs)}"
        for s in secs:
            assert s.get("title") and s.get("body"), f"{it.id} 段落缺标题或正文"
        body = "".join(s.get("body", "") for s in secs)
        assert len(body) >= 600, f"{it.id} 正文太短({len(body)} 字)，还是条目不是报道"
        # 展望类段落必须出现"各方"的主体视角, 而不是只有结论
        blob = body + (feat.get("lead") or "")
        assert any(a in blob for a in ("监管", "公募", "券商", "银行", "保险")), (
            f"{it.id} 报道里没有分主体的动作描述"
        )


def test_feature_render_markdown_and_html():
    from finradar.analysis.features import build_feature, render_feature_html, render_feature_markdown
    from finradar.knowledge import insights as I

    it = I.get("QDII")
    qdii_rows = [
        {"title": "内地与香港利率互换市场互联互通合作管理暂行办法", "summary": "",
         "pub_date": "2023-04-29", "policy_score": 72, "url": "https://gov.cn/x",
         "doc_no": "中国人民银行公告〔2023〕第8号", "source_name": "政府网"},
        {"title": "关于延续实施沪港、深港股票市场交易互联互通机制的通知", "summary": "",
         "pub_date": "2023-08-26", "policy_score": 68, "url": "https://gov.cn/y",
         "doc_no": "财政部公告〔2023〕第x号", "source_name": "政府网"},
    ]
    art = build_feature(it, qdii_rows, min_score=30)
    assert art["has_feature"] and art["sections"]

    md = render_feature_markdown(art)
    assert md.startswith(f"# {it.topic}")
    for sec in art["sections"]:
        assert f"## {sec['title']}" in md
    assert "## 数据支撑" in md and "## 主要文件（供核对）" in md

    html = render_feature_html(art)
    assert "<article class='feature'" in html and "<h3>" in html


def test_feature_data_paragraph_uses_corpus():
    """数据支撑段要用真实语料说话: 条数、年份、最近一份正式文件。"""
    from finradar.analysis.features import build_feature
    from finradar.knowledge import insights as I

    it = I.get("tech-finance-aic-bond")
    rows = _rows_fixture() + [
        {"title": "国家金融监督管理总局办公厅关于进一步扩大金融资产投资公司股权投资试点的通知",
         "summary": "", "pub_date": "2025-03-05", "policy_score": 82,
         "url": "https://gov.cn/e", "doc_no": "金办发〔2025〕19号", "source_name": "金融监管总局"},
    ]
    art = build_feature(it, rows, min_score=30)
    para = art["data_paragraph"]
    assert "涉及这条线的有" in para and "2025" in para
    assert "金办发〔2025〕19号" in para or "金融资产投资公司" in para
    # 没有语料时不应编造段落
    assert build_feature(it, [], min_score=30)["data_paragraph"] == ""


def test_feature_without_article_falls_back_to_thesis():
    """还没写正文的专题: 渲染要退回核心判断并给出提示, 而不是空白页。"""
    from finradar.analysis.features import build_feature, render_feature_markdown
    from finradar.models import Insight

    it = Insight(id="x", topic="示例专题", thesis="这是一句核心判断。")
    art = build_feature(it, [])
    assert art["has_feature"] is False
    md = render_feature_markdown(art)
    assert "这是一句核心判断。" in md
    assert "feature.sections" in md  # 提示怎么补正文


def test_site_payload_includes_features():
    from finradar.analysis.insight_site import build_payload
    from finradar.knowledge import insights as I

    payload = build_payload(_rows_fixture(), insights=[I.get("QDII")], terms=[], candidates=False)
    assert len(payload["features"]) == 1
    art = payload["features"][0]
    assert art["lead"] and art["sections"] and "data_paragraph" in art


# ---------------------------------------------------------------- 大众热榜与规模化挖掘

def test_finance_topics_filter_and_merge():
    """热榜九成是娱乐体育, 只留财经话题并跨平台合并."""
    from finradar.analysis.trends import finance_topics, hot_value, is_finance

    rows = [
        {"title": "AI巨头按下“IPO暂停键”", "summary": "热度 7827541",
         "source": "douyin_hot", "source_name": "抖音·热榜", "url": ""},
        {"title": "某明星演唱会门票售罄", "summary": "热度 9999999",
         "source": "toutiao_hot", "source_name": "今日头条·热榜", "url": ""},
        {"title": "外资抢抓三大新机遇", "summary": "热度 87381929",
         "source": "toutiao_hot", "source_name": "今日头条·热榜", "url": "https://x"},
        {"title": "楼市新政落地，房贷利率下调", "summary": "热度 5000",
         "source": "baidu_hot", "source_name": "百度·热搜", "url": ""},
    ]
    assert is_finance("楼市新政落地") and not is_finance("某明星演唱会门票售罄")
    assert hot_value(rows[0]) == 7827541

    topics = finance_topics(rows)
    words = [t["topic"] for t in topics]
    assert any("IPO" in w for w in words)
    assert not any("演唱会" in w for w in words), "娱乐话题不该进来"
    assert sum(1 for t in topics if "热度" not in t["topic"]) == len(topics)


def test_trend_crawlers_parse_json():
    """三个热榜都是 JSON 接口, 用离线样本验证字段映射."""
    from finradar.crawlers.trends import BaiduHot, DouyinHot, ToutiaoHot

    class F:
        def __init__(self, payload):
            self.payload = payload

        def get_json(self, url, **kw):
            return self.payload

    tt = ToutiaoHot(fetcher=F({"data": [{"Title": "金砖国家领导人集体合影",
                                         "HotValue": "106716489",
                                         "Url": "https://www.toutiao.com/trending/1/"}]})).run()
    assert tt and tt[0].title.startswith("金砖") and tt[0].source == "toutiao_hot"
    assert tt[0].summary == "热度 106716489"
    assert ToutiaoHot.kind == "trend" and ToutiaoHot.base_score == 0.0  # 不参与政策打分
    assert len(tt[0].published_at) == 19

    dy = DouyinHot(fetcher=F({"data": {"word_list": [{"word": "服贸会14年成绩单亮眼",
                                                      "hot_value": 11395761}]}})).run()
    assert dy and dy[0].title.startswith("服贸会")

    bd = BaiduHot(fetcher=F({"data": {"cards": [{"content": [{"content": [
        {"word": "筑牢金砖合作根基", "url": "https://m.baidu.com/s?word=x"}]}]}]}})).run()
    assert bd and bd[0].title.startswith("筑牢") and "baidu.com" in bd[0].url


def test_trends_excluded_from_query_by_default():
    """热榜噪音大: 日报/热词统计默认要排除, 但可以显式包含."""
    import tempfile
    from pathlib import Path

    from finradar.models import NewsItem
    from finradar.storage import Store

    with tempfile.TemporaryDirectory() as d:
        s = Store(Path(d) / "t.db")
        s.save_news([
            NewsItem(source="gov", source_name="政府网", title="政策文件"),
            NewsItem(source="douyin_hot", source_name="抖音·热榜", title="娱乐话题"),
        ])
        assert len(s.query()) == 2
        assert [r["source"] for r in s.query(exclude_sources=["douyin_hot"])] == ["gov"]


def test_mine_candidates_rejects_fragments():
    """规模化挖掘的精度约束: 机构名、公文体裁、动词开头的标题片段都要滤掉."""
    from finradar.analysis.dossiers import mine_candidates

    rows = [
        {"title": "关于促进资本市场健康发展的通知", "summary": "", "pub_date": "2024-01-01",
         "policy_score": 70, "url": "https://x/1", "doc_no": ""},
        {"title": "商务部关于印发管理办法的通知", "summary": "", "pub_date": "2024-02-01",
         "policy_score": 70, "url": "https://x/2", "doc_no": ""},
        {"title": "资本市场深化改革实施方案", "summary": "", "pub_date": "2024-03-01",
         "policy_score": 70, "url": "https://x/3", "doc_no": ""},
    ] * 2
    got = [c["word"] for c in mine_candidates(rows, glossary_names=[], top=40, min_count=2)]
    for w in got:
        assert not w.startswith(("促进", "推动", "支持", "深化")), f"动词片段没滤掉: {w}"
        assert not w.endswith(("管理办法", "实施方案", "的通知")), f"公文体裁没滤掉: {w}"
        assert not w.endswith(("部", "委", "局", "总局")), f"机构名没滤掉: {w}"
        assert not any(ch in w for ch in "的了和与"), f"标题片段没滤掉: {w}"
