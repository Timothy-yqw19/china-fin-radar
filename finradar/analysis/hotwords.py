"""热词统计与趋势分析.

两种口径:
  1) 词库口径 —— 用 finradar/data/glossary 里的热词表去匹配, 结果可解释、可对齐考点
  2) 发现口径 —— 用 n-gram + 停用词过滤, 发现词库里还没有的新词 (供人工补录)
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Iterable

from ..knowledge.glossary import load_glossary

STOPWORDS = set(
    """
的 了 和 与 及 或 在 是 为 对 中 上 下 有 将 把 被 等 就 也 并 而 从 到 由 以 其 该 各 个
我们 他们 表示 指出 认为 强调 要求 提出 进一步 加大 加快 推动 支持 促进 完善 加强 有关
方面 情况 问题 工作 相关 目前 记者 报道 消息 今日 昨日 今天 昨天 上午 下午 发布 举行
持续 有效 积极 稳定 增长 下降 亿元 万元 同比 环比 以上 以下 之间 一步 重要 主要 通过
新华社 证券时报 中国证券报 上海证券报 财联社 央视 记者 表示
当地时间 央视新闻 同比增长 环比增长 亿美元 亿元人民币 月以来 月份以来 据统计 数据显示
国家统计局 有关部门 相关人士 业内人士 分析师 研究员 编辑 责任编辑 免责声明 风险提示
更多精彩 点击查看 详细内容 全文如下 具体情况 有关情况 年度报告 季度报告
日报 周报 月报 早报 早餐 快讯 电报 直播 会议 通知 公告 活动 发布会 新闻 消息 报道
举行 召开 出席 参加 拜访 会见 洽谈 签约 落地 上线 完成 实现 达到 超过 突破 触及
关于 根据 按照 位于 属于 包括 包含 涉及 影响 预计 有望 可能 或将 已经 正在 将继续
第一 第二 第三 第四 第五 之一 之二 方面 领域 行业 企业 公司 机构 部门 地区 城市
领导 同志 主席 总裁 董事 高管 经理 总监 首席 专家 学者 教授 院长 主任 局长 部长
据报道 据新华社 的消息 的通知 的规定 的有关 的公告 的意见 本次 同比 环比 百分点
今年以来 去年以来 现报 美元 港元 人民币 消息人士 有关负责人 自治区 直辖市 省份
有关负责 有关事 关负责 关问题 有关事项 局有关 行有关 发展有关 联合印发 贯彻落实
金管理 业保险 行行长 行发布 保基金 金融支持 投资基金 印发 发布 方案 指导意见
实施方案 党中央 国务院 中国人民银行 中国证监会 金融监管总局 国家外汇管理局
""".split()
)

TOKEN_RE = re.compile(r"[一-龥]{2,}|[A-Za-z][A-Za-z0-9+\-]{1,}")
# 新词候选的形态约束: 中文 2-12 字, 或全大写缩写(CPI / QDII / ETF), 不含数字
CAND_RE = re.compile(r"^[一-龥]{2,12}$|^[A-Z][A-Z0-9]{1,9}$")
# "9月10日下午" 这类日期/时间会被当成一个中文串的边界, 留着会产出 "日下午""日报道" 这种碎片
DATE_LIKE_RE = re.compile(r"\d+\s*[年月日时分秒号]")
# 以转述动词结尾的串基本是"人名+动作"的句子碎片, 不是术语
REPORT_VERB_TAIL = ("指出", "表示", "强调", "认为", "称", "说", "宣布", "透露", "回应")
# 虚词: 术语里几乎不会出现, 出现了基本是跨词边界的碎片("意见的""总局关于")
STOP_CHARS = set("的了和与及在为对就也并而从而由以其该各个将把被于还又很更再都只才便则即之是或给让使能会要应可须得向往且但如若虽既所者地")


# ---------------------------------------------------------------- 词库口径

def match_glossary(texts: Iterable[str]) -> Counter:
    """统计词库热词在文本流中的出现次数 (含别名归并到主词)."""
    gloss = load_glossary()
    name2term = {}
    for t in gloss:
        for n in t.all_names:
            name2term[n] = t.term
    cnt: Counter = Counter()
    for text in texts:
        seen = set()
        for name, main in name2term.items():
            if name in text and main not in seen:
                seen.add(main)
                cnt[main] += 1
    return cnt


def combined_hotwords(rows: list[dict]) -> Counter:
    """热词榜的实际口径：词库主词 + 规则命中词（config/keywords.yaml）合并。

    词库主词保证"能对上考点"，规则词保证"覆盖面"（降准、白名单这类
    还没单独立词条的高频提法也能上榜）。
    """
    from .tagger import load_rules

    rules = load_rules()
    # 只有"实质议题"类的词才算热词；发文主体、政策动作、噪音词只用于打分
    allow: set[str] = set()
    for cat in ("monetary", "capital_market", "banking", "opening", "theme"):
        allow.update((rules.get(cat) or {}).get("words") or [])

    cnt = match_glossary(f"{r.get('title', '')} {r.get('summary', '')}" for r in rows)
    for r in rows:
        for w in r.get("hotwords") or []:
            if w in allow:
                cnt[w] += 1
    return cnt


def glossary_trend(rows: list[dict], bucket: str = "week") -> dict[str, Counter]:
    """按时间桶统计词库热词, 返回 {桶: Counter}."""
    out: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        d = (r.get("pub_date") or "")[:10]
        if not d:
            continue
        if bucket == "month":
            key = d[:7]
        elif bucket == "day":
            key = d
        else:  # week -> ISO 周
            from datetime import date

            try:
                y, m, dd = (int(x) for x in d.split("-"))
                iso = date(y, m, dd).isocalendar()
                key = f"{iso[0]}-W{iso[1]:02d}"
            except Exception:  # noqa: BLE001
                key = d
        text = f"{r.get('title','')} {r.get('summary','')}"
        for w, n in match_glossary([text]).items():
            out[key][w] += n
    return dict(out)


# ---------------------------------------------------------------- 发现口径

def discover_new_words(
    texts: Iterable[str],
    top: int = 40,
    min_len: int = 3,
    min_count: int = 3,
    max_len: int = 8,
    max_df_ratio: float = 0.15,
) -> list[tuple[str, int]]:
    """新词发现: 从新闻里捞出"反复出现、词库里还没有"的提法.

    做法是字符 n-gram + 四条过滤, 不需要分词器:
      1) **文档频率** —— 至少在 min_count 条新闻里出现过 (同一篇里重复提不算)
      2) **左右邻多样性** —— 真词两侧都能搭配不同的字; "的通""治区"这种碎片
         总有一侧是固定的, 过不了这一关
      3) **通用词剔除** —— 出现在超过 max_df_ratio 比例新闻里的词(中国、国务院、
         美国)是背景词, 不是"新提法"
      4) **碎片剔除** —— 若某个更长的候选几乎同样常见, 就丢掉短的那个
         (有"公开市场业务交易公告"时不要再报"公开市场")

    目的不是替代分词, 而是提示需要人工确认的新提法, 确认后补进 glossary。
    """
    gloss_names = set()
    for t in load_glossary():
        gloss_names.update(t.all_names)

    df: Counter = Counter()
    left: dict[str, set[str]] = {}
    right: dict[str, set[str]] = {}
    texts = list(texts)
    n_docs = len(texts)
    for text in texts:
        seen: set[str] = set()
        for run in TOKEN_RE.findall(DATE_LIKE_RE.sub(" ", text or "")):
            n_run = len(run)
            for i in range(n_run):
                for ln in range(min_len, min(max_len, n_run - i) + 1):
                    w = run[i : i + ln]
                    if w in seen:
                        continue
                    seen.add(w)
                    df[w] += 1
                    if i > 0:
                        left.setdefault(w, set()).add(run[i - 1])
                    if i + ln < n_run:
                        right.setdefault(w, set()).add(run[i + ln])

    def ok(w: str, n: int) -> bool:
        if n < min_count or w in STOPWORDS or not CAND_RE.match(w):
            return False
        if w.endswith(REPORT_VERB_TAIL):
            return False
        if any(ch in STOP_CHARS for ch in w):
            return False
        # 词库里已有(或它是某个词条的一部分)就不用报了
        return not any(g in w or w in g for g in gloss_names)

    cands = {w: n for w, n in df.items() if ok(w, n)}

    # 碎片剔除: 如果某个更长的 n-gram 几乎同样常见, 短的就是它的碎片
    # ("新闻发" 之于 "新闻发布会", "续实施" 之于 "继续实施")。
    # 这里拿"所有 n-gram"而不是过滤后的候选来对照 —— 长词可能因为词库里已有
    # (中国证监会)或左右邻不足而被过滤掉, 不该因此让它的碎片冒出来。
    explained: set[str] = set()
    for k, c in sorted(df.items(), key=lambda kv: (-len(kv[0]), -kv[1])):
        if len(k) < min_len + 1:
            continue
        for i in range(len(k)):
            for ln in range(min_len, len(k) - i + 1):
                if i == 0 and ln == len(k):  # 跳过自己
                    continue
                sub = k[i : i + ln]
                if sub in cands and sub not in explained and cands[sub] <= c * 1.4:
                    explained.add(sub)

    def entropy_ok(w: str) -> bool:
        """真词两侧都能搭配不同的字; 句子碎片总有一侧是固定的.

        标题末尾的词天然没有右邻居, 所以再放宽一档: 一侧 ≥4 另一侧 ≥1 也算。
        """
        nl, nr = len(left.get(w, ())), len(right.get(w, ()))
        return (nl >= 2 and nr >= 2) or (nl >= 4 and nr >= 1) or (nl >= 1 and nr >= 4)

    out = [
        (w, n)
        for w, n in cands.items()
        if w not in explained and entropy_ok(w)
        # 通用词(中国、国务院、美国…)是背景音, 不是新提法
        # (语料太小时这个比例没意义, 直接不启用)
        and (n_docs < 50 or n <= max_df_ratio * n_docs)
    ]
    out.sort(key=lambda kv: (-kv[1], -len(kv[0])))
    return out[:top]
