"""SQLite 存储层 —— 新闻去重入库、查询、错题本."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

from .models import NewsItem
from .utils import workdir

SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
    uid           TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    source_name   TEXT,
    title         TEXT NOT NULL,
    url           TEXT,
    published_at  TEXT,
    pub_date      TEXT,
    summary       TEXT,
    content       TEXT,
    channel       TEXT,
    doc_no        TEXT,
    tags          TEXT,
    hotwords      TEXT,
    policy_score  REAL DEFAULT 0,
    impact        TEXT,
    fetched_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_news_date   ON news(pub_date DESC);
CREATE INDEX IF NOT EXISTS idx_news_source ON news(source);
CREATE INDEX IF NOT EXISTS idx_news_score  ON news(policy_score DESC);

CREATE TABLE IF NOT EXISTS quiz_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    qid         TEXT NOT NULL,
    board       TEXT,
    correct     INTEGER,
    answered_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_quiz_qid ON quiz_log(qid);

CREATE TABLE IF NOT EXISTS run_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    source    TEXT,
    ok        INTEGER,
    n_items   INTEGER,
    message   TEXT,
    ran_at    TEXT
);
"""


class Store:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else workdir() / "finradar.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------ 写入
    def save_news(self, items: Iterable[NewsItem]) -> int:
        """插入新条目, 返回新增数量 (uid 已存在则跳过)."""
        rows = []
        for it in items:
            rows.append(
                (
                    it.uid, it.source, it.source_name, it.title, it.url,
                    it.published_at, it.date, it.summary, it.content, it.channel,
                    it.doc_no, json.dumps(it.tags, ensure_ascii=False),
                    json.dumps(it.hotwords, ensure_ascii=False),
                    it.policy_score, it.impact, it.fetched_at,
                )
            )
        if not rows:
            return 0
        with self._conn() as c:
            before = c.execute("SELECT COUNT(*) FROM news").fetchone()[0]
            c.executemany(
                "INSERT OR IGNORE INTO news (uid,source,source_name,title,url,published_at,"
                "pub_date,summary,content,channel,doc_no,tags,hotwords,policy_score,impact,"
                "fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            after = c.execute("SELECT COUNT(*) FROM news").fetchone()[0]
        return after - before

    def log_run(self, source: str, ok: bool, n: int, message: str = "") -> None:
        from datetime import datetime

        with self._conn() as c:
            c.execute(
                "INSERT INTO run_log (source,ok,n_items,message,ran_at) VALUES (?,?,?,?,?)",
                (source, int(ok), n, message[:500], datetime.now().isoformat(timespec="seconds")),
            )

    def update_analysis(self, items: Iterable[NewsItem]) -> int:
        """按 uid 就地更新打分/标签/热词/传导逻辑 (改完 keywords.yaml 后重算用)."""
        rows = [
            (
                json.dumps(it.tags, ensure_ascii=False),
                json.dumps(it.hotwords, ensure_ascii=False),
                it.policy_score,
                it.impact,
                it.uid,
            )
            for it in items
        ]
        if not rows:
            return 0
        with self._conn() as c:
            c.executemany(
                "UPDATE news SET tags=?, hotwords=?, policy_score=?, impact=? WHERE uid=?",
                rows,
            )
        return len(rows)

    # ------------------------------------------------------------ 查询
    def query(
        self,
        since: str | None = None,
        source: str | None = None,
        min_score: float = 0.0,
        keyword: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        sql = "SELECT * FROM news WHERE policy_score >= ?"
        args: list = [min_score]
        if since:
            sql += " AND pub_date >= ?"
            args.append(since)
        if source:
            sql += " AND source = ?"
            args.append(source)
        if keyword:
            sql += " AND (title LIKE ? OR summary LIKE ?)"
            args += [f"%{keyword}%", f"%{keyword}%"]
        sql += " ORDER BY pub_date DESC, policy_score DESC LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            rows = [dict(r) for r in c.execute(sql, args).fetchall()]
        for r in rows:
            r["tags"] = json.loads(r.get("tags") or "[]")
            r["hotwords"] = json.loads(r.get("hotwords") or "[]")
        return rows

    def stats(self) -> dict:
        with self._conn() as c:
            total = c.execute("SELECT COUNT(*) FROM news").fetchone()[0]
            by_src = {
                r["source_name"] or r["source"]: r["n"]
                for r in c.execute(
                    "SELECT source, source_name, COUNT(*) n FROM news "
                    "GROUP BY source ORDER BY n DESC"
                ).fetchall()
            }
            # NULLIF: 少数列表页没给日期(pub_date 存成空串), 不该污染"最早日期"
            row = c.execute(
                "SELECT MAX(NULLIF(pub_date,'')), MIN(NULLIF(pub_date,'')) FROM news"
            ).fetchone()
            latest, earliest = row[0], row[1]
        return {"total": total, "by_source": by_src, "latest": latest, "earliest": earliest}

    # ------------------------------------------------------------ 错题本
    def log_quiz(self, qid: str, board: str, correct: bool) -> None:
        from datetime import datetime

        with self._conn() as c:
            c.execute(
                "INSERT INTO quiz_log (qid,board,correct,answered_at) VALUES (?,?,?,?)",
                (qid, board, int(correct), datetime.now().isoformat(timespec="seconds")),
            )

    def wrong_qids(self, min_wrong: int = 1) -> list[str]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT qid, SUM(1-correct) w FROM quiz_log GROUP BY qid "
                "HAVING w >= ? ORDER BY w DESC",
                (min_wrong,),
            ).fetchall()
        return [r["qid"] for r in rows]

    def quiz_stats(self) -> dict:
        with self._conn() as c:
            row = c.execute(
                "SELECT COUNT(*) n, SUM(correct) c FROM quiz_log"
            ).fetchone()
            by_board = [
                dict(r)
                for r in c.execute(
                    "SELECT board, COUNT(*) n, SUM(correct) c FROM quiz_log GROUP BY board"
                ).fetchall()
            ]
        n, c_ = row["n"] or 0, row["c"] or 0
        return {
            "answered": n,
            "correct": c_,
            "accuracy": round(c_ / n * 100, 1) if n else 0.0,
            "by_board": by_board,
        }
