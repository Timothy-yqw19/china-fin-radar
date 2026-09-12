"""离线演示：塞入一批样例政策新闻，跑通打分 → 入库 → 日报 → 热词榜。

用途：网络不通、或者想先看看输出长什么样时使用。
    python scripts/demo_seed.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finradar.analysis import annotate, combined_hotwords, write_report  # noqa: E402
from finradar.crawlers import source_base_score  # noqa: E402
from finradar.models import NewsItem  # noqa: E402
from finradar.storage import Store  # noqa: E402

SAMPLES = [
    ("pbc", "中国人民银行", "中国人民银行决定下调金融机构存款准备金率0.5个百分点",
     "2026-09-05 17:00:00", "为保持银行体系流动性充裕，人民银行决定于近日下调存款准备金率，释放长期资金约1万亿元。"),
    ("gov", "中国政府网·政策文件库", "国务院办公厅关于做好金融“五篇大文章”的指导意见",
     "2026-09-04 09:00:00", "围绕科技金融、绿色金融、普惠金融、养老金融、数字金融作出系统部署。"),
    ("csrc", "中国证券监督管理委员会", "证监会就修订《上市公司重大资产重组管理办法》公开征求意见",
     "2026-09-04 18:00:00", "进一步支持上市公司并购重组，提高对未盈利资产的包容度，简化审核程序。"),
    ("nfra", "国家金融监督管理总局", "金融监管总局发布关于扩大金融资产投资公司股权投资试点的通知",
     "2026-09-03 15:30:00", "扩大AIC股权投资试点城市范围，提高表内资金投资比例。"),
    ("safe", "国家外汇管理局", "外汇局发放新一轮QDII额度，向公募产品倾斜",
     "2026-08-31 16:00:00", "本轮新增合格境内机构投资者投资额度，多家公募基金获批。"),
    ("cls", "财联社·电报", "央行行长：继续实施适度宽松的货币政策，促进物价合理回升",
     "2026-09-05 10:12:00", "灵活高效运用降准降息等多种货币政策工具，保持流动性充裕。"),
    ("em_flash", "东方财富·全球财经快讯", "午评：三大指数集体高开，AI概念股涨停，主力资金净流入",
     "2026-09-05 11:31:00", "盘面上题材轮动加快。"),
    ("gov", "中国政府网·政策文件库", "关于推动中长期资金入市工作的实施方案",
     "2026-09-02 09:00:00", "提升商业保险资金A股投资比例与稳定性，优化全国社保基金、基本养老保险基金长周期考核。"),
    ("pbc", "中国人民银行", "人民银行印发《中国人民银行“十五五”改革发展规划》",
     "2026-08-10 10:00:00", "加快完善中央银行制度，健全中国特色现代货币政策框架，推进债券市场“科技板”建设。"),
    ("nfra", "国家金融监督管理总局", "关于加强商业银行净息差管理与存款利率自律的通知",
     "2026-08-28 14:00:00", "规范同业存款定价，严禁通过手工补息等方式变相高息揽储。"),
]


def main() -> None:
    items = [
        NewsItem(source=s, source_name=n, title=t, published_at=p, summary=sm,
                 url=f"https://example.invalid/{i}")
        for i, (s, n, t, p, sm) in enumerate(SAMPLES)
    ]
    items = annotate(items)
    # 与真实抓取保持一致: 来源先验分是打分下限(crawl 时由 BaseCrawler.run 施加)
    for it in items:
        it.policy_score = max(it.policy_score, source_base_score(it.source))

    print("=== 政策相关度打分结果 ===")
    for it in sorted(items, key=lambda x: -x.policy_score):
        print(f"[{it.policy_score:5.1f}] {it.source_name:<18} {it.title[:34]}")
        if it.impact:
            print(f"         └ {it.impact[:80]}")

    store = Store()
    print(f"\n入库新增 {store.save_news(items)} 条 → {store.path}")

    rows = store.query(since="2026-01-01", min_score=0, limit=100)
    print("\n=== 热词榜（词库主词 + 规则命中词）===")
    for w, c in combined_hotwords(rows).most_common(15):
        print(f"  {w:<26}{c}")

    rows = [r for r in rows if float(r["policy_score"]) >= 40]
    paths = write_report(rows, title="金融政策日报（演示数据）", stem="demo_report")
    print(f"\n日报已生成：\n  {paths['markdown']}\n  {paths['html']}")


if __name__ == "__main__":
    main()
