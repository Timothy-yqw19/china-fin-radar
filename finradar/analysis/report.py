"""政策日报 / 周报生成 (Markdown + 单文件 HTML)."""

from __future__ import annotations

import html
import re

from ..utils import now_cn, workdir
from .hotwords import combined_hotwords

BOARD_ORDER = ["政策动作", "货币政策", "资本市场", "银行保险", "对外开放", "主题热词", "发文主体"]

_PUNCT = re.compile(r"[\s　·、，,。.；;：:！!？?“”\"'（）()《》〈〉\[\]【】\-—_/\\|]+")
# 公文标题里的"包装词": 同一份文件被不同站点转载时, 常常只差这些词
_PACKAGING = ("关于印发", "关于做好", "关于", "印发", "的通知", "通知", "的公告", "公告",
              "的批复", "批复", "发布", "出台")


def _norm_title(title: str) -> str:
    return _PUNCT.sub("", title or "")


def _core_title(title: str) -> str:
    s = _norm_title(title)
    for w in _PACKAGING:
        s = s.replace(w, "")
    return s


def dedupe_rows(rows: list[dict]) -> list[dict]:
    """把"同一件事被多个源抓到"的条目合并成一条.

    实测里同一个文件常常同时出现在 政府网政策库 / 财政部 / 财联社, 标题还会
    略有差异(带不带"关于印发…的通知"), 所以除了完全相同, 还做一次包含式匹配;
    合并后保留政策分最高的一条, 并记下其他来源。
    """
    kept: list[dict] = []
    index: list[str] = []
    cores: list[str] = []
    doc_index: dict[str, int] = {}
    for r in sorted(rows, key=lambda x: -float(x.get("policy_score") or 0)):
        norm = _norm_title(r.get("title") or "")
        if not norm:
            continue
        core = _core_title(r.get("title") or "")
        # 同一份文件被两个源转载时, 文号是最可靠的合并依据
        doc_no = (r.get("doc_no") or "").strip()
        dup_at = None
        if doc_no and doc_no in doc_index:
            dup_at = doc_index[doc_no]
        for i, k in enumerate(index):
            if dup_at is not None:
                break
            if norm == k:
                dup_at = i
                break
            # 去掉"关于印发…的通知"这类包装词后再比: 标题重复才算同一件事,
            # 但数字必须完全一致 —— "公告第178号"和"第177号"不是一条。
            ck = cores[i]
            short, long_ = (core, ck) if len(core) <= len(ck) else (ck, core)
            if len(short) >= 12 and short in long_:
                dup_at = i
                break
        if dup_at is None:
            r = dict(r)
            r["also_from"] = []
            index.append(norm)
            cores.append(core)
            kept.append(r)
            if doc_no:
                doc_index[doc_no] = len(kept) - 1
        else:
            host = kept[dup_at]
            src = r.get("source_name") or r.get("source") or ""
            if src and src != (host.get("source_name") or host.get("source")):
                host.setdefault("also_from", [])
                if src not in host["also_from"]:
                    host["also_from"].append(src)
    return kept


def _group(rows: list[dict]) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        tags = r.get("tags") or []
        key = next((t for t in BOARD_ORDER if t in tags), "其他")
        buckets.setdefault(key, []).append(r)
    return buckets


def build_markdown(rows: list[dict], title: str = "金融政策日报", top_hot: int = 15) -> str:
    now = now_cn().strftime("%Y-%m-%d %H:%M")
    raw_n = len(rows)
    rows = dedupe_rows(rows)
    hot = combined_hotwords(rows)
    sub = f"> 生成时间 {now}（北京时间） · 共 {len(rows)} 条"
    if raw_n != len(rows):
        sub += f"（已合并 {raw_n - len(rows)} 条跨源重复）"
    lines = [f"# {title}", "", sub, ""]

    if hot:
        lines += ["## 一、热词榜", ""]
        lines += [
            "| # | 热词 | 出现次数 |",
            "|---|------|---------|",
        ]
        for i, (w, n) in enumerate(hot.most_common(top_hot), 1):
            lines.append(f"| {i} | {w} | {n} |")
        lines.append("")

    lines += ["## 二、按板块梳理", ""]
    for board, items in _group(rows).items():
        lines.append(f"### {board}（{len(items)}）")
        lines.append("")
        for r in sorted(items, key=lambda x: -float(x.get("policy_score") or 0))[:20]:
            date = (r.get("pub_date") or "")[:10]
            src = r.get("source_name") or r.get("source")
            t = r.get("title") or ""
            url = r.get("url") or ""
            head = f"- **{t}**" if not url else f"- **[{t}]({url})**"
            lines.append(head)
            meta = f"  - {date} · {src} · 政策分 {r.get('policy_score')}"
            if r.get("doc_no"):
                meta += f" · {r['doc_no']}"
            lines.append(meta)
            if r.get("also_from"):
                lines.append(f"  - 同源报道：{'、'.join(r['also_from'])}")
            if r.get("hotwords"):
                lines.append(f"  - 命中热词：{'、'.join(r['hotwords'][:8])}")
            if r.get("impact"):
                lines.append(f"  - 传导逻辑：{r['impact']}")
        lines.append("")

    lines += [
        "## 三、怎么用这份日报准备面试",
        "",
        "1. 只精读政策分 ≥ 60 的条目，其余扫标题即可。",
        "2. 每条只问自己三个问题：**谁发的 / 改了什么 / 谁受影响**。",
        "3. 把「传导逻辑」一行背下来——面试官追问的永远是「所以呢」。",
        "4. 用 `finradar quiz --board <板块>` 把当天热词对应的考点刷一遍。",
        "",
    ]
    return "\n".join(lines)


HTML_TPL = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
:root{{--bg:#fbfaf7;--fg:#1f2328;--mut:#6a7280;--line:#e6e3dc;--card:#fff;--acc:#8a5a2b}}
@media (prefers-color-scheme:dark){{:root{{--bg:#14161a;--fg:#e8e6e1;--mut:#9aa1ab;--line:#2a2e35;--card:#1b1e23;--acc:#d9a441}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}}
.wrap{{max-width:900px;margin:0 auto;padding:32px 20px 64px}}
h1{{font-size:26px;margin:0 0 6px}} .sub{{color:var(--mut);font-size:13px;margin-bottom:26px}}
h2{{font-size:18px;margin:34px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--line)}}
h3{{font-size:15px;margin:22px 0 10px;color:var(--acc)}}
.chips{{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:18px}}
.chip{{background:var(--card);border:1px solid var(--line);border-radius:99px;padding:3px 10px;font-size:12px}}
.chip b{{color:var(--acc)}}
.item{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:9px}}
.item a{{color:inherit;text-decoration:none;font-weight:600}} .item a:hover{{color:var(--acc)}}
.meta{{color:var(--mut);font-size:12px;margin-top:5px}}
.imp{{font-size:12.5px;margin-top:6px;padding-left:9px;border-left:2px solid var(--acc);color:var(--mut)}}
.score{{float:right;font-size:12px;color:var(--acc);font-weight:700}}
</style></head><body><div class="wrap">
<h1>{title}</h1><div class="sub">{now}</div>
{body}
</div></body></html>"""


def build_html(rows: list[dict], title: str = "金融政策日报") -> str:
    now = now_cn().strftime("%Y-%m-%d %H:%M")
    raw_n = len(rows)
    rows = dedupe_rows(rows)
    hot = combined_hotwords(rows)
    parts = []
    if hot:
        chips = "".join(
            f'<span class="chip">{html.escape(w)} <b>{n}</b></span>'
            for w, n in hot.most_common(20)
        )
        parts.append(f"<h2>热词榜</h2><div class='chips'>{chips}</div>")
    parts.append("<h2>按板块梳理</h2>")
    for board, items in _group(rows).items():
        parts.append(f"<h3>{html.escape(board)}（{len(items)}）</h3>")
        for r in sorted(items, key=lambda x: -float(x.get("policy_score") or 0))[:25]:
            t = html.escape(r.get("title") or "")
            url = html.escape(r.get("url") or "")
            title_html = f'<a href="{url}" target="_blank" rel="noopener">{t}</a>' if url else t
            meta = f"{(r.get('pub_date') or '')[:10]} · {html.escape(r.get('source_name') or '')}"
            if r.get("doc_no"):
                meta += " · " + html.escape(str(r["doc_no"]))
            if r.get("also_from"):
                meta += " · 亦见：" + html.escape("、".join(r["also_from"]))
            if r.get("hotwords"):
                meta += " · " + html.escape("、".join(r["hotwords"][:6]))
            imp = (
                f"<div class='imp'>{html.escape(r['impact'])}</div>" if r.get("impact") else ""
            )
            parts.append(
                f"<div class='item'><span class='score'>{r.get('policy_score')}</span>"
                f"{title_html}<div class='meta'>{meta}</div>{imp}</div>"
            )
    sub = f"生成时间 {now}（北京时间） · 共 {len(rows)} 条"
    if raw_n != len(rows):
        sub += f"（已合并 {raw_n - len(rows)} 条跨源重复）"
    return HTML_TPL.format(title=html.escape(title), now=sub, n=len(rows), body="".join(parts))


def write_report(rows: list[dict], title: str = "金融政策日报", stem: str | None = None) -> dict:
    out = workdir() / "reports"
    out.mkdir(parents=True, exist_ok=True)
    stem = stem or f"report_{now_cn():%Y%m%d}"
    md_path = out / f"{stem}.md"
    html_path = out / f"{stem}.html"
    md_path.write_text(build_markdown(rows, title), encoding="utf-8")
    html_path.write_text(build_html(rows, title), encoding="utf-8")
    return {"markdown": str(md_path), "html": str(html_path)}
