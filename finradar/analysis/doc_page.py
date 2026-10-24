"""把 docs/ 里的长文 Markdown 渲染成与站点同一套视觉的静态页面。

`docs/` 下的 .md 在 GitHub Pages 上是当作纯文本返回的（浏览器只显示源码），
所以长文另外出一份排版好的 .html：

    python scripts/build_interview_doc.py                # 生成到 public/docs/
    python scripts/build_interview_doc.py --out /tmp/x.html

`scripts/build_static_site.py` 在构建静态站时会自动调用本模块，
因此 CI 每次部署都跟着 .md 一起更新，不用手写两份。
"""

from __future__ import annotations

import html as _html
import re
from datetime import datetime
from pathlib import Path

import markdown

# 与 finradar.analysis.insight_site.shared_style() 的变量同名，视觉才统一；
# 这里只写长文排版需要的部分（正文 / 表格 / 引用 / 目录 / 打印）。
DOC_CSS = """
<style>
:root{
  --paper:#F6F3EC; --surface:#FFFDF8; --ink:#17191C; --ink-2:#3D3F44; --muted:#6E6A61;
  --rule:#E2DCCF; --rule-soft:#EDE8DD; --seal:#A3342B; --seal-soft:#F0DFDA;
  --jade:#2E5257; --gold:#8A6A22;
  --shadow:0 1px 2px rgba(23,25,28,.05), 0 10px 30px -18px rgba(23,25,28,.25);
}
@media (prefers-color-scheme:dark){
  :root{
    --paper:#141517; --surface:#1B1D20; --ink:#ECEAE4; --ink-2:#C8C5BD; --muted:#9A968C;
    --rule:#33363A; --rule-soft:#2A2D31; --seal:#E0837A; --seal-soft:#3A2A28;
    --jade:#7FB3B6; --gold:#C9A75E;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 10px 30px -18px rgba(0,0,0,.7);
  }
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0; background:var(--paper); color:var(--ink); line-height:1.9;
  font-family:"Noto Serif SC","Songti SC","PingFang SC",Georgia,serif;
  font-size:16px; letter-spacing:.01em;
}
a{color:var(--jade); text-decoration:none; border-bottom:1px solid color-mix(in srgb,var(--jade) 40%,transparent)}
a:hover{background:color-mix(in srgb,var(--jade) 10%,transparent)}
.wrap{max-width:960px; margin:0 auto; padding:0 20px 72px}
/* ---- 页头 ---- */
header.head{padding:44px 0 22px; border-bottom:2px solid var(--rule)}
.kicker{
  display:inline-block; font-size:12px; letter-spacing:.22em; color:var(--seal);
  border:1px solid var(--seal); padding:3px 10px; border-radius:2px;
  font-family:"IBM Plex Mono",Menlo,monospace; margin-bottom:14px;
}
header.head h1{
  font-size:30px; line-height:1.35; margin:0 0 10px; letter-spacing:-.01em;
  font-family:"Noto Serif SC","Songti SC",Georgia,serif;
}
header.head .lede{color:var(--muted); margin:0 0 14px; font-size:15px; max-width:44em}
header.head .meta{font-size:12.5px; color:var(--muted); font-family:"IBM Plex Mono",Menlo,monospace}
header.head .meta span+span{margin-left:14px}
nav.pagebar{display:flex; flex-wrap:wrap; gap:8px; margin:16px 0 0}
nav.pagebar a{
  font-size:13px; padding:5px 12px; border:1px solid var(--rule); border-radius:999px;
  background:var(--surface); color:var(--ink-2);
}
nav.pagebar a:hover{background:var(--seal-soft); color:var(--seal); border-color:var(--seal)}
/* ---- 目录 ---- */
.toc{
  background:var(--surface); border:1px solid var(--rule); border-left:3px solid var(--seal);
  padding:18px 22px; margin:28px 0 8px; box-shadow:var(--shadow); border-radius:3px;
}
.toc h2{font-size:13px; letter-spacing:.18em; color:var(--muted); margin:0 0 10px;
  border:0; padding:0; font-family:"IBM Plex Mono",Menlo,monospace}
.toc ul{columns:2; column-gap:36px; margin:0; padding-left:18px; font-size:14px; line-height:1.75}
.toc ul ul{columns:1; font-size:13px; opacity:.85}
.toc li{break-inside:avoid; margin:2px 0}
.toc a{border:0}
@media (max-width:720px){ .toc ul{columns:1} }
/* ---- 正文 ---- */
main{padding-top:8px}
h1,h2,h3,h4{font-family:"Noto Serif SC","Songti SC",Georgia,serif; line-height:1.45}
h1{font-size:26px; margin:36px 0 12px}
h2{
  font-size:21px; margin:44px 0 14px; padding:0 0 8px 12px;
  border-left:4px solid var(--seal); border-bottom:1px solid var(--rule-soft);
}
h3{font-size:17px; margin:30px 0 10px; color:var(--jade)}
h4{font-size:15.5px; margin:22px 0 8px}
p{margin:12px 0}
strong{color:var(--ink); font-weight:700}
em{color:var(--ink-2)}
hr{border:0; border-top:1px solid var(--rule); margin:38px 0}
ul,ol{margin:12px 0; padding-left:26px}
li{margin:6px 0}
li>ul,li>ol{margin:6px 0}
blockquote{
  margin:18px 0; padding:14px 20px; background:var(--surface); border:1px solid var(--rule-soft);
  border-left:3px solid var(--gold); border-radius:3px; color:var(--ink-2);
}
blockquote p{margin:6px 0}
blockquote strong{color:var(--ink)}
code{
  font-family:"IBM Plex Mono",Menlo,Consolas,monospace; font-size:.88em;
  background:var(--rule-soft); padding:1px 5px; border-radius:3px;
}
pre{background:var(--surface); border:1px solid var(--rule); border-radius:4px;
  padding:14px 16px; overflow-x:auto; font-size:13.5px; line-height:1.7}
pre code{background:none; padding:0; font-size:inherit}
/* ---- 表格 ---- */
.tablewrap{overflow-x:auto; margin:18px 0; border:1px solid var(--rule); border-radius:4px}
table{border-collapse:collapse; width:100%; font-size:14px; line-height:1.7; background:var(--surface)}
th,td{border-bottom:1px solid var(--rule-soft); border-right:1px solid var(--rule-soft);
  padding:9px 12px; vertical-align:top; text-align:left}
th:last-child,td:last-child{border-right:0}
tr:last-child td{border-bottom:0}
th{background:var(--rule-soft); font-weight:700; white-space:nowrap; font-size:13.5px}
tbody tr:hover{background:color-mix(in srgb,var(--gold) 6%,transparent)}
/* ---- 页脚 ---- */
footer.foot{
  margin-top:52px; padding-top:18px; border-top:1px solid var(--rule);
  color:var(--muted); font-size:12.5px; line-height:1.8;
  font-family:"IBM Plex Mono",Menlo,monospace;
}
footer.foot a{border:0}
@media (max-width:720px){
  body{font-size:15.5px}
  header.head h1{font-size:24px}
  .wrap{padding:0 16px 56px}
  th,td{padding:7px 9px}
}
@media print{
  body{background:#fff; font-size:10.5pt}
  .toc,nav.pagebar{break-inside:avoid}
  h2{break-after:avoid}
  table,blockquote{break-inside:avoid}
}
</style>
"""

MD_EXTENSIONS = ["tables", "fenced_code", "toc", "sane_lists"]


def _slugify(value: str, separator: str) -> str:
    """保留中文的锚点 id（默认 slugify 会把中文整段删掉，只剩 #1 #2 这种编号）。"""
    value = re.sub(r"[^\w\u4e00-\u9fff\- ]", "", value, flags=re.UNICODE)
    return re.sub(r"\s+", separator, value.strip()).lower()


MD_EXTENSION_CONFIGS = {
    "toc": {"toc_depth": "2-3", "title": "", "permalink": False, "slugify": _slugify}
}
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _build_toc(tokens: list[dict]) -> str:
    """按 toc_tokens 自己拼目录，避免 markdown 自带的 .toc 容器和页面样式打架。"""
    if not tokens:
        return ""
    items = []
    for t in tokens:
        link = f'<a href="#{t.get("id", "")}">{_html.escape(t.get("name", ""))}</a>'
        kids = _build_toc(t.get("children") or [])
        items.append(f"<li>{link}{kids}</li>")
    return "<ul>" + "".join(items) + "</ul>"


def _wrap_tables(html: str) -> str:
    """给宽表格加横向滚动容器，手机上不会撑破版面。"""
    return re.sub(r"<table>(.*?)</table>", r'<div class="tablewrap"><table>\1</table></div>', html, flags=re.S)


def _short_lede(text: str, limit: int = 150) -> str:
    """从正文开头连续的引文里抽一句导读（拼成整句、去掉 markdown 标记）。"""
    lines: list[str] = []
    for raw in text.splitlines():
        if raw.startswith(">"):
            piece = raw.lstrip(">").strip()
            if piece:
                lines.append(piece)
            continue
        if lines:
            break
    if not lines:
        return ""
    joined = re.sub(r"[*`]", "", "".join(lines))
    joined = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", joined)  # [文本](链接) → 文本
    joined = " ".join(joined.split())
    m = re.match(r"^(.*?。)", joined)  # 截到第一句号
    if m:
        joined = m.group(1)
    return joined[:limit] + ("…" if len(joined) > limit else "")


def render_doc(
    md_path: str | Path,
    *,
    kicker: str = "面试专题",
    home_href: str = "../index.html",
    quiz_href: str = "../kb.html",
    generated: str | None = None,
) -> str:
    """把一篇 docs/*.md 渲染成整页 HTML（返回字符串）。"""
    path = Path(md_path)
    src = path.read_text(encoding="utf-8")

    md = markdown.Markdown(
        extensions=MD_EXTENSIONS, extension_configs=MD_EXTENSION_CONFIGS
    )
    body = md.convert(src)
    body = _wrap_tables(body)

    # 标题：取第一个一级标题，正文里去掉它（页头已经渲染）
    title = path.stem
    m = re.search(r"^#\s+(.+)$", src, flags=re.M)
    if m:
        title = m.group(1).strip()
        body = re.sub(r"<h1[^>]*>.*?</h1>\s*", "", body, count=1, flags=re.S)

    toc = _build_toc(getattr(md, "toc_tokens", []) or [])
    lede = _short_lede(src)
    n_cjk = len(_CJK_RE.findall(src))
    now = generated or datetime.now().strftime("%Y-%m-%d %H:%M")
    md_href = path.with_suffix(".md").name

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{_html.escape(lede)}">
<title>{_html.escape(title)} · 秋招金融政策手册</title>
{DOC_CSS}
</head>
<body>
<div class="wrap">
  <header class="head">
    <div class="kicker">{_html.escape(kicker)}</div>
    <h1>{_html.escape(title)}</h1>
    <p class="lede">{_html.escape(lede)}</p>
    <div class="meta">
      <span>约 {n_cjk:,} 字</span><span>生成于 {now}</span>
      <span><a href="{md_href}">原始 Markdown</a></span>
    </div>
    <nav class="pagebar">
      <a href="{home_href}">← 返回首页（专题 / 名词档案）</a>
      <a href="{quiz_href}">去刷题（92 题）</a>
      <a href="使用说明.md">使用说明</a>
    </nav>
  </header>
  <div class="toc">
    <h2>目录</h2>
    {toc}
  </div>
  <main>
{body}
  </main>
  <footer class="foot">
    内容用于求职学习整理，不构成投资建议；面经与真题以当年官方通知为准。<br>
    本页由 <code>scripts/build_interview_doc.py</code> 从同名 Markdown 生成，改内容请改 .md。
  </footer>
</div>
</body>
</html>
"""
