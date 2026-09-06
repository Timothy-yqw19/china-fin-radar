"""政策相关度打分 + 标签 + 影响链解读."""

from __future__ import annotations

import functools
from pathlib import Path

import yaml

from ..models import NewsItem
from ..utils import CONFIG_DIR

CATEGORY_LABEL = {
    "strong": "政策动作",
    "issuer": "发文主体",
    "monetary": "货币政策",
    "capital_market": "资本市场",
    "banking": "银行保险",
    "opening": "对外开放",
    "theme": "主题热词",
}

# 影响链模板: 命中标签 -> 一句话传导逻辑 (面试里"所以呢"的那一步)
IMPACT_RULES = [
    (["降准"], "释放长期低成本资金 → 缓解银行负债成本 → 支撑信贷投放, 利好银行息差与债市短端"),
    (["降息", "LPR"], "政策利率/LPR 下行 → 贷款收益率下降、存款跟随下调 → 银行息差短期承压, 债市走强"),
    (["存款利率"], "负债端成本下行 → 净息差压力缓解 → 但可能加速'存款搬家'流向理财与债基"),
    (["再贷款", "结构性货币政策工具"], "定向低成本资金 → 引导特定领域信贷投放 → 总量影响有限、结构影响明显"),
    (["中长期资金", "险资", "社保基金", "年金"], "增量长钱入市 → 提升高股息与宽基 ETF 需求 → 改善 A 股资金面与波动率"),
    (["IPO", "注册制"], "一级市场供给节奏变化 → 影响投行业务量与二级市场承接压力"),
    (["并购重组"], "IPO 收紧下的替代退出通道 → 投行业务重心转移 → 利好券商并购业务与壳资源分化"),
    (["退市"], "出清劣质公司 → 提升指数质量 → 短期壳价值下降、投资者保护配套需跟上"),
    (["回购", "市值管理", "增持"], "上市公司主动提升股东回报 → 支撑估值中枢, 关注是否为注销式回购"),
    (["公募基金", "浮动费率"], "费率与考核改革 → 主动管理费收入承压 → 加速被动化与买方投顾转型"),
    (["QDII"], "出海额度是硬约束 → 额度紧张时 QDII 基金限购、场内溢价走高"),
    (["QFII", "合格境外投资者"], "流入端便利化 → 外资配置渠道拓宽 (注意: 额度已于 2019 年取消)"),
    (["白名单", "保交房", "存量房贷"], "地产融资与需求端政策 → 缓释开发商与按揭风险 → 拖累银行按揭收益率"),
    (["化债", "隐性债务"], "高息城投资产被低息政府债置换 → 银行资产质量改善但收益率下降 → 加剧'资产荒'"),
    (["资本充足率", "资本管理办法"], "资本约束变化 → 影响风险资产扩张能力与分红空间"),
    (["科创债", "科技金融"], "债权工具支持科创 → 需配套风险分担与分层定价, 关注发行主体扩容"),
    (["稳定币", "数字人民币"], "支付与清算基础设施变化 → 影响跨境结算成本与人民币国际化路径"),
    (["程序化交易"], "量化交易规则趋严 → 影响高频策略容量与券商 PB 业务"),
]


@functools.lru_cache(maxsize=1)
def load_rules(path: str | None = None) -> dict:
    p = Path(path) if path else CONFIG_DIR / "keywords.yaml"
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def score_and_tag(item: NewsItem, rules: dict | None = None) -> NewsItem:
    """给一条新闻打政策分并贴标签 (就地修改并返回)."""
    rules = rules or load_rules()
    text = f"{item.title} {item.summary} {item.channel} {item.doc_no}"
    score = float(item.policy_score)
    tags: list[str] = []
    hits: list[str] = []

    for cat, cfg in rules.items():
        w = float(cfg.get("score", 0))
        words = cfg.get("words") or []
        matched = [k for k in words if k in text]
        if not matched:
            continue
        hits.extend(matched)
        # 同一类别命中多个词按 1 + 0.25*(n-1) 递减计分, 且单类别最多算 2 倍权重,
        # 避免堆砌关键词刷分
        score += w * min(2.0, 1 + 0.25 * (len(matched) - 1))
        if cat != "noise":
            label = CATEGORY_LABEL.get(cat, cat)
            if label not in tags:
                tags.append(label)

    # 协同加分: "官方主体 + 具体政策领域" 同时命中, 才是真正的政策新闻,
    # 单独命中"央行"(可能只是被引用)或单独命中"降准"(可能是评论)都不够。
    domain_tags = {"货币政策", "资本市场", "银行保险", "对外开放"}
    if "发文主体" in tags and domain_tags & set(tags):
        score += 15
    if "政策动作" in tags and "发文主体" in tags:
        score += 10

    item.policy_score = round(max(0.0, min(100.0, score)), 1)
    item.tags = tags
    # 去重保序
    item.hotwords = list(dict.fromkeys(hits))[:12]
    item.impact = explain_impact(item.hotwords)
    return item


def explain_impact(hotwords: list[str]) -> str:
    hits = []
    for keys, text in IMPACT_RULES:
        if any(k in hotwords for k in keys):
            hits.append(text)
    return " | ".join(hits[:2])


def annotate(items: list[NewsItem]) -> list[NewsItem]:
    rules = load_rules()
    return [score_and_tag(i, rules) for i in items]
