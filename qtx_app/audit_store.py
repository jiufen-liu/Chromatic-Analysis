from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .storage_v2 import audit_database_path


class AuditStore:
    """Dedicated DG4 audit database.

    Security identity/permissions live in security.sqlite3; append-only-ish audit
    history lives here so restoring colour data or user permissions does not
    accidentally roll the other domain backwards.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else audit_database_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._create_schema()

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def _create_schema(self) -> None:
        with self.connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    username TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL DEFAULT '',
                    detail TEXT NOT NULL DEFAULT ''
                )"""
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(created_at DESC)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(username)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action)")

    def migrate_from_security_database(self, security_db: str | Path) -> int:
        """Move legacy audit rows out of the copied auth DB into audit.sqlite3.

        The legacy source file itself is never changed. Only the new DG4
        security DB may have its old audit_log table removed after a verified
        copy, preserving physical separation without risking the rollback copy.
        """
        security_db = Path(security_db)
        if not security_db.exists():
            return 0
        try:
            src = sqlite3.connect(security_db)
            src.row_factory = sqlite3.Row
            try:
                table = src.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_log'"
                ).fetchone()
                if not table:
                    return 0
                rows = src.execute(
                    "SELECT id,created_at,username,action,target,detail FROM audit_log ORDER BY id"
                ).fetchall()
                if not rows:
                    src.execute("DROP TABLE IF EXISTS audit_log")
                    src.commit()
                    return 0
                with self.connect() as dst:
                    existing = int(dst.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] or 0)
                    if existing == 0:
                        dst.executemany(
                            "INSERT INTO audit_log(id,created_at,username,action,target,detail) VALUES(?,?,?,?,?,?)",
                            [(int(r['id']), float(r['created_at']), str(r['username'] or ''),
                              str(r['action'] or ''), str(r['target'] or ''), str(r['detail'] or '')) for r in rows],
                        )
                    copied = int(dst.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] or 0)
                if copied >= len(rows):
                    src.execute("DROP TABLE IF EXISTS audit_log")
                    src.commit()
                return len(rows) if existing == 0 else 0
            finally:
                src.close()
        except Exception:
            return 0

    def log(self, username: str, action: str, target: str = "", detail: str = "") -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO audit_log(created_at,username,action,target,detail) VALUES(?,?,?,?,?)",
                (time.time(), username or "system", action, target or "", detail or ""),
            )

    def query(
        self, *, since: float | None = None, username: str = "", action: str = "",
        keyword: str = "", limit: int | None = 5000,
    ) -> list[sqlite3.Row]:
        clauses = []
        params: list[object] = []
        if since is not None:
            clauses.append("created_at>=?")
            params.append(float(since))
        username = str(username or "").strip()
        if username:
            clauses.append("username=?")
            params.append(username)
        action = str(action or "").strip()
        if action:
            clauses.append("action=?")
            params.append(action)
        keyword = str(keyword or "").strip()
        if keyword:
            like = f"%{keyword}%"
            clauses.append("(username LIKE ? OR action LIKE ? OR target LIKE ? OR detail LIKE ?)")
            params.extend([like, like, like, like])
        sql = "SELECT id,created_at,username,action,target,detail FROM audit_log"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(1, int(limit)))
        with self.connect() as db:
            return db.execute(sql, params).fetchall()

    def filter_values(self) -> tuple[list[str], list[str]]:
        with self.connect() as db:
            users = [str(r[0]) for r in db.execute(
                "SELECT DISTINCT username FROM audit_log WHERE username<>'' ORDER BY username COLLATE NOCASE"
            ).fetchall()]
            actions = [str(r[0]) for r in db.execute(
                "SELECT DISTINCT action FROM audit_log WHERE action<>'' ORDER BY action COLLATE NOCASE"
            ).fetchall()]
        return users, actions
