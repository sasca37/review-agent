"""업무별 리뷰 요청 기록 (SQLite). 입력한 diff 와 점검 결과를 서버에 저장한다. API Key 는 저장하지 않는다."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from typing import Dict, List, Optional

KEEP_PER_TASK = 300


class HistoryStore:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS history (
                     id INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT NOT NULL, created_at TEXT NOT NULL,
                     engine TEXT NOT NULL, diff TEXT NOT NULL, git_status TEXT NOT NULL, result TEXT NOT NULL)""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_task ON history(task, id DESC)")

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def add(self, task: str, engine: str, diff: str, git_status: str, result: Dict) -> int:
        with closing(self._conn()) as c, c:
            cur = c.execute(
                "INSERT INTO history(task, created_at, engine, diff, git_status, result) VALUES (?,?,?,?,?,?)",
                (task, datetime.now().isoformat(timespec="seconds"), engine, diff, git_status,
                 json.dumps(result, ensure_ascii=False)))
            c.execute("DELETE FROM history WHERE task=? AND id NOT IN "
                      "(SELECT id FROM history WHERE task=? ORDER BY id DESC LIMIT ?)",
                      (task, task, KEEP_PER_TASK))
            return cur.lastrowid

    def list(self, task: Optional[str] = None, limit: int = 30) -> List[Dict]:
        sql, args = "SELECT id, task, created_at, engine, result FROM history", []
        if task:
            sql += " WHERE task=?"
            args.append(task)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with closing(self._conn()) as c:
            rows = c.execute(sql, args).fetchall()
        out = []
        for r in rows:
            res = json.loads(r["result"])
            fs = res.get("findings", [])
            found = sum(1 for f in fs if f.get("status") == "지적")
            check = sum(1 for f in fs if f.get("status") == "확인 필요")
            opinion = sum(1 for f in fs if f.get("status") == "의견")
            files = [f["path"] for f in res.get("files", [])]
            out.append({"id": r["id"], "task": r["task"], "created_at": r["created_at"], "engine": r["engine"],
                        "found": found, "check": check, "opinion": opinion, "files": files})
        return out

    def get(self, id_: int) -> Optional[Dict]:
        with closing(self._conn()) as c:
            r = c.execute("SELECT * FROM history WHERE id=?", (id_,)).fetchone()
        if not r:
            return None
        return {"id": r["id"], "task": r["task"], "created_at": r["created_at"], "engine": r["engine"],
                "diff": r["diff"], "git_status": r["git_status"], "result": json.loads(r["result"])}

    def delete(self, id_: int) -> bool:
        with closing(self._conn()) as c, c:
            return c.execute("DELETE FROM history WHERE id=?", (id_,)).rowcount > 0
