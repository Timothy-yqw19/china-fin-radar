"""名词档案与候选新词挖掘.

用户要的是"很多不同名词解释，而且能从过去新闻政策里抓关键词出来分析"，所以这里
把静态词库和真实语料拼在一起:

  * term_dossiers —— 每个词条 = 词库里的定义/考点/答法/追问 + 它在政策语料里的
    出现次数、首见年份、逐年走势、代表文件。46 个词条一次性都有"档案"，不用逐条手写。
  * mine_candidates —— 反过来，从语料里挖词库里还没有的提法，附上年度走势和例句，
    人工确认后就能补成词条或专题。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Iterable

from ..models import Insight, Term

# 政策语料里挖出来的候选, 有两大类噪音必须滤掉:
#   1) 机构名 —— 商务部、市场监管总局、农业农村部… 它们不是"提法"
#   2) 公文常用语 —— 决策部署、支持力度、等部门、工作情况…
ORG_SUFFIX = (
    "部", "委", "局", "总局", "署", "办", "厅", "院", "银行", "公司", "集团",
    "中心", "协会", "交易所", "办公室", "委员会", "管理局", "监管总局",
)
STOP_SUFFIX = (
    "部门", "工作", "力度", "情况", "建设", "发展", "要求", "有关", "方面", "地区",
    "单位", "企业", "机构", "人员", "时间", "内容", "问题", "任务", "措施", "举措",
    "决策部署", "领导小组", "人民政府", "直属机构", "管理工作", "相关工作",
)
STOP_WORDS = {
    "等部门", "有关部门", "相关单位", "人民政府", "直属机构", "领导小组",
    "中共中央", "国务院办公厅", "国务院新闻办", "办公厅",
    # 公文类型不是"提法", 但会命中 办法/方案/指引 这些中心词
    "管理办法", "暂行办法", "实施方案", "行动方案", "工作方案", "指导意见",
    "实施细则", "实施细则", "规定", "方案", "办法", "指引", "通知", "公告",
    "实施意见", "试点方案", "发展规划", "工作计划", "政策措施", "政策工具",
    "政策措施", "政策措施", "若干措施", "政策措施",
    # 太泛的通用语, 做成词条没有信息量
    "金融服务", "金融政策", "金融产品", "金融业", "金融等", "支持政策", "政策支持",
    "优惠政策", "政策性", "银行业", "保险业", "银行保险", "资产管理", "资本市场",
    "数字化", "企业融资", "绿色低碳", "财政金融", "本办法", "等政策", "性金融",
    "化金融", "税政策", "中央预算内投资", "国家发展改革委等",
}
# 候选的首尾出现这些虚词, 基本是跨词边界拼出来的碎片
BOUNDARY_CHARS = set("等性化的是本及与和该各有为在对中了的把被或")
# 只有"像金融政策提法"的候选才值得人工看: 至少含一个领域词
FINANCE_TOKENS = (
    "金融", "货币", "资本", "债", "股", "险", "银行", "基金", "信贷", "利率", "汇率",
    "支付", "清算", "结算", "监管", "风险", "资产", "融资", "投资", "改革", "试点",
    "开放", "账户", "工具", "机制", "体系", "政策", "制度", "办法", "指引", "规划",
    "方案", "标准", "平台", "走廊", "额度", "储备", "债券", "上市", "退市", "回购",
    "化债", "财政", "税收", "补贴", "养老", "保险", "理财", "信托", "租赁", "担保",
    "征信", "信用", "数据", "数字", "科技", "创新", "绿色", "普惠", "跨境", "外资",
)
# 政策名词的"中心词": 候选必须落在这些后缀上, 精度比通用 n-gram 高得多
POLICY_HEADS = (
    "机制", "制度", "试点", "改革", "体系", "工具", "额度", "账户", "平台", "标准",
    "指引", "办法", "规划", "方案", "行动", "计划", "走廊", "通道", "清单", "名单",
    "工程", "市场", "基金", "债券", "票据", "贷款", "保险", "理财", "信托", "租赁",
    "担保", "补贴", "税", "费", "率", "板", "池", "通", "手段",
)


def _blob(row: dict) -> str:
    return f"{row.get('title') or ''} {row.get('summary') or ''}"


def _clean_rows(rows: Iterable[dict]) -> list[dict]:
    out = []
    for r in rows:
        if "example.invalid" in (r.get("url") or ""):  # demo 假数据
            continue
        if not (r.get("pub_date") or ""):
            continue
        out.append(r)
    return out


def term_dossiers(
    terms: Iterable[Term],
    rows: Iterable[dict],
    insights: Iterable[Insight] = (),
    top_docs: int = 4,
    min_score: float = 40.0,
) -> list[dict]:
    """给每个词条生成一份"档案": 词库内容 + 语料统计."""
    rows = _clean_rows(rows)
    by_term: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        text = _blob(r)
        for t in terms:
            if any(n and n in text for n in t.all_names):
                by_term[t.id].append(r)

    insight_of: dict[str, list[str]] = defaultdict(list)
    for it in insights:
        for name in it.related_terms:
            insight_of[name].append(it.id)

    out: list[dict] = []
    for t in terms:
        hits = by_term.get(t.id, [])
        by_year = Counter((r.get("pub_date") or "")[:4] for r in hits)
        dated = sorted((r.get("pub_date") or "")[:10] for r in hits if r.get("pub_date"))
        # 代表文件: 只看政策分够高的, 再按时间倒序, 同年同文号去重
        cand = [r for r in hits if float(r.get("policy_score") or 0) >= min_score]
        cand.sort(key=lambda r: (-float(r.get("policy_score") or 0), r.get("pub_date") or ""))
        seen: set[str] = set()
        docs: list[dict] = []
        for r in cand:
            key = (r.get("title") or "")[:24]
            if key in seen:
                continue
            seen.add(key)
            docs.append(
                {
                    "date": (r.get("pub_date") or "")[:10],
                    "title": r.get("title"),
                    "url": r.get("url"),
                    "score": r.get("policy_score"),
                    "doc_no": r.get("doc_no") or "",
                    "source": r.get("source_name") or "",
                }
            )
            if len(docs) >= top_docs:
                break
        d = t.to_dict()
        d["stats"] = {
            "n": len(hits),
            "first_year": (dated[0][:4] if dated else ""),
            "first_date": (dated[0] if dated else ""),
            "latest_date": (dated[-1] if dated else ""),
            "by_year": [{"year": y, "n": n} for y, n in sorted(by_year.items()) if y],
        }
        d["docs"] = docs
        d["insights"] = insight_of.get(t.term, [])
        out.append(d)
    # 语料里出现得多的排前面, 没出现的按热度
    out.sort(key=lambda d: (-d["stats"]["n"], -d.get("heat", 0)))
    return out


def mine_candidates(
    rows: Iterable[dict],
    glossary_names: Iterable[str],
    insight_keywords: Iterable[str] = (),
    top: int = 40,
    min_count: int = 5,
    max_df_ratio: float = 0.08,
    min_score: float = 40.0,
    samples: int = 3,
    require_domain: bool = True,
    recent_years: int = 3,
) -> list[dict]:
    """从政策语料里挖"词库里还没有"的关键词, 并给出年度走势与例句.

    两条腿走路:
      1) **后缀定向抽取**(主力) —— 政策名词基本都落在"…机制/体系/试点/额度/账户/平台"
         这类中心词上, 按后缀找候选精度高得多;
      2) **通用 n-gram**(补漏) —— 复用 `finradar hot --discover` 那一套, 用来捞
         不带后缀的提法(比如"买断式逆回购")。
    两条都会过: 词库/专题已收录、机构名、公文常用语、无关领域、以及"只有旧年份出现"的过滤。
    """
    rows = [r for r in _clean_rows(rows) if float(r.get("policy_score") or 0) >= min_score]
    skip = {g for g in glossary_names if g}
    skip |= {k for k in insight_keywords if k}
    skip = {s for s in skip if s}

    def clean(w: str) -> bool:
        if w in skip or len(w) < 3 or len(w) > 14:
            return False
        if w.endswith(ORG_SUFFIX) or w in STOP_WORDS or w.endswith(STOP_SUFFIX):
            return False
        if w[0] in BOUNDARY_CHARS or w[-1] in BOUNDARY_CHARS:
            return False
        if require_domain and not any(tok in w for tok in FINANCE_TOKENS):
            return False
        return True

    # ---------- 1) 后缀定向抽取(只看标题) ----------
    # 只挖标题: 政策提法几乎都会写进标题, 而且标题没有跨句碎片
    # ("政策的通""性金融"这类都是摘要里跨词边界拼出来的)
    df: Counter = Counter()
    all_df: Counter = Counter()
    for r in rows:
        text = r.get("title") or ""
        seen: set[str] = set()
        seen_all: set[str] = set()
        for run in __import__("re").findall(r"[一-龥]{4,40}", text):
            for i in range(len(run)):
                for ln in range(3, min(13, len(run) - i) + 1):
                    w = run[i : i + ln]
                    if w not in seen_all:
                        seen_all.add(w)
                        all_df[w] += 1
                    if w in seen or not clean(w):
                        continue
                    if not any(w.endswith(h) for h in POLICY_HEADS):
                        continue
                    seen.add(w)
                    df[w] += 1

    # 碎片剔除: 只要存在一个"更长且同样常见"的 n-gram 包含它, 它就是这个长串的碎片。
    # 对照集用**全量标题 n-gram**: 机构名"国家发展改革委"会被机构后缀过滤掉,
    # 但它的碎片"展改革""国家发展改革"不能因此留下来。
    explain: set[str] = set()
    for k, c in sorted(all_df.items(), key=lambda kv: (-len(kv[0]), -kv[1])):
        for i in range(len(k)):
            for ln in range(3, len(k) - i + 1):
                if i == 0 and ln == len(k):
                    continue
                sub = k[i : i + ln]
                if sub in all_df and all_df[sub] <= c * 1.25 and sub not in explain:
                    explain.add(sub)
    words: list[tuple[str, int]] = [
        (w, n) for w, n in df.items() if w not in explain and n >= min_count
    ]

    # ---------- 2) 通用 n-gram 补漏 ----------
    from .hotwords import discover_new_words

    extra = discover_new_words(
        [_blob(r) for r in rows],
        top=max(top * 6, 150),
        min_count=min_count,
        max_df_ratio=max_df_ratio,
    )
    have = {w for w, _ in words}
    for w, n in extra:
        if w not in have and clean(w):
            words.append((w, n))

    words.sort(key=lambda kv: -kv[1])

    out: list[dict] = []
    for w, n in words:
        by_year: Counter = Counter()
        samples_titles: list[dict] = []
        for r in rows:
            if w not in _blob(r):
                continue
            by_year[(r.get("pub_date") or "")[:4]] += 1
            if len(samples_titles) < samples:
                samples_titles.append(
                    {
                        "date": (r.get("pub_date") or "")[:10],
                        "title": r.get("title"),
                        "url": r.get("url"),
                        "score": r.get("policy_score"),
                    }
                )
        years = [y for y in sorted(by_year) if y]
        if require_domain and years:
            # "新提法"应该最近还在用: 近 N 年的出现次数要占到一半以上
            from ..utils import now_cn

            cut = now_cn().year - recent_years
            recent = sum(by_year[y] for y in years if int(y) >= cut)
            if recent * 5 < sum(by_year.values()) * 2:  # 近 3 年占比 < 40% 说明是旧提法
                continue
        out.append(
            {
                "word": w,
                "n": n,
                "first_year": years[0] if years else "",
                "latest_year": years[-1] if years else "",
                "by_year": [{"year": y, "n": by_year[y]} for y in years],
                "samples": samples_titles,
            }
        )
        if len(out) >= top:
            break
    return out
