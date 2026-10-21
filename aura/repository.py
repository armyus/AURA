"""SQLite persistence for local AURA workspaces."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .engine import DecisionValidationError, validate_model


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Repository:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.environ.get("AURA_DB_PATH", "data/aura.sqlite3"))
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False) if str(self.path) == ":memory:" else None
        self.initialize()

    def connect(self):
        db = self._memory_conn or sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def initialize(self) -> None:
        db = self.connect()
        db.executescript("""
        CREATE TABLE IF NOT EXISTS decisions(
          id TEXT PRIMARY KEY, title TEXT NOT NULL, question TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, current_revision_id TEXT
        );
        CREATE TABLE IF NOT EXISTS revisions(
          id TEXT PRIMARY KEY, decision_id TEXT NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
          revision_no INTEGER NOT NULL, model_json TEXT NOT NULL, input_hash TEXT NOT NULL,
          created_at TEXT NOT NULL, UNIQUE(decision_id, revision_no)
        );
        CREATE TABLE IF NOT EXISTS evaluations(
          id TEXT PRIMARY KEY, decision_id TEXT NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
          revision_id TEXT NOT NULL REFERENCES revisions(id), scenario_id TEXT,
          algorithm_version TEXT NOT NULL, input_hash TEXT NOT NULL, result_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS scenarios(
          id TEXT PRIMARY KEY, decision_id TEXT NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
          baseline_evaluation_id TEXT NOT NULL REFERENCES evaluations(id), name TEXT NOT NULL,
          overrides_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS documents(
          id TEXT PRIMARY KEY, decision_id TEXT NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
          filename TEXT NOT NULL, media_type TEXT NOT NULL, sha256 TEXT NOT NULL,
          created_at TEXT NOT NULL, parse_status TEXT NOT NULL DEFAULT 'ready', parse_error TEXT,
          UNIQUE(decision_id, sha256)
        );
        CREATE TABLE IF NOT EXISTS chunks(
          id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
          chunk_no INTEGER NOT NULL, page_no INTEGER, start_offset INTEGER, end_offset INTEGER,
          text TEXT NOT NULL, verified_status TEXT NOT NULL DEFAULT 'unverified',
          verified_at TEXT, UNIQUE(document_id, chunk_no)
        );
        CREATE INDEX IF NOT EXISTS idx_revisions_decision ON revisions(decision_id, revision_no);
        CREATE INDEX IF NOT EXISTS idx_eval_decision ON evaluations(decision_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_docs_decision ON documents(decision_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id, chunk_no);
        CREATE TABLE IF NOT EXISTS schema_migrations(
          version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL
        );
        """)
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            with db:
                db.execute("INSERT OR IGNORE INTO schema_migrations(version,name,applied_at) VALUES(1,?,?)",
                           ("baseline-local-workspace", now()))
                db.execute("PRAGMA user_version=1")
            version = 1
        if version < 2:
            with db:
                db.executescript("""
                CREATE TABLE IF NOT EXISTS auth_users(
                  id INTEGER PRIMARY KEY CHECK(id=1), salt BLOB NOT NULL,
                  password_hash BLOB NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auth_sessions(
                  token_hash TEXT PRIMARY KEY, expires_at TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_auth_sessions_expiry ON auth_sessions(expires_at);
                """)
                db.execute("INSERT OR IGNORE INTO schema_migrations(version,name,applied_at) VALUES(2,?,?)",
                           ("single-user-local-auth", now()))
                db.execute("PRAGMA user_version=2")
            version = 2
        if version < 3:
            with db:
                document_columns = {row[1] for row in db.execute("PRAGMA table_info(documents)").fetchall()}
                if "parse_status" not in document_columns:
                    db.execute("ALTER TABLE documents ADD COLUMN parse_status TEXT NOT NULL DEFAULT 'ready'")
                if "parse_error" not in document_columns:
                    db.execute("ALTER TABLE documents ADD COLUMN parse_error TEXT")
                db.executescript("""
                CREATE TABLE IF NOT EXISTS ingestion_jobs(
                  id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                  payload BLOB, status TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_status ON ingestion_jobs(status, created_at);
                """)
                db.execute("INSERT OR IGNORE INTO schema_migrations(version,name,applied_at) VALUES(3,?,?)",
                           ("asynchronous-document-ingestion", now()))
                db.execute("PRAGMA user_version=3")
            version = 3
        if version < 4:
            with db:
                db.executescript("""
                CREATE TABLE IF NOT EXISTS robustness_runs(
                  id TEXT PRIMARY KEY, decision_id TEXT NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
                  revision_id TEXT NOT NULL REFERENCES revisions(id), input_hash TEXT NOT NULL,
                  settings_json TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_robustness_decision ON robustness_runs(decision_id, created_at);
                """)
                db.execute("INSERT OR IGNORE INTO schema_migrations(version,name,applied_at) VALUES(4,?,?)",
                           ("saved-robustness-simulations", now()))
                db.execute("PRAGMA user_version=4")
        db.commit()
        if not self._memory_conn:
            db.close()

    @staticmethod
    def _hash(model: dict[str, Any]) -> str:
        raw = json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def auth_configured(self) -> bool:
        db = self.connect()
        try:
            return bool(db.execute("SELECT 1 FROM auth_users WHERE id=1").fetchone())
        finally:
            if not self._memory_conn: db.close()

    def set_password(self, password: str) -> None:
        if not isinstance(password, str) or len(password) < 12 or len(password) > 1024:
            raise DecisionValidationError("Choose a password with 12–1024 characters.")
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
        db = self.connect()
        try:
            with db:
                if db.execute("SELECT 1 FROM auth_users WHERE id=1").fetchone():
                    raise FileExistsError("AURA sign-in is already configured.")
                db.execute("INSERT INTO auth_users(id,salt,password_hash,created_at) VALUES(1,?,?,?)",
                           (salt, digest, now()))
        finally:
            if not self._memory_conn: db.close()

    def verify_password(self, password: str) -> bool:
        if not isinstance(password, str): return False
        db = self.connect()
        try:
            row = db.execute("SELECT salt,password_hash FROM auth_users WHERE id=1").fetchone()
            if not row: return False
            candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), row["salt"], 310_000)
            return secrets.compare_digest(candidate, row["password_hash"])
        finally:
            if not self._memory_conn: db.close()

    def create_session(self, token: str, expires_at: str) -> None:
        db = self.connect()
        try:
            with db:
                db.execute("DELETE FROM auth_sessions WHERE expires_at<=?", (now(),))
                db.execute("INSERT INTO auth_sessions(token_hash,expires_at,created_at) VALUES(?,?,?)",
                           (self._token_hash(token), expires_at, now()))
        finally:
            if not self._memory_conn: db.close()

    def session_valid(self, token: str) -> bool:
        db = self.connect()
        try:
            return bool(db.execute("SELECT 1 FROM auth_sessions WHERE token_hash=? AND expires_at>?",
                                   (self._token_hash(token), now())).fetchone())
        finally:
            if not self._memory_conn: db.close()

    def revoke_session(self, token: str) -> None:
        db = self.connect()
        try:
            with db: db.execute("DELETE FROM auth_sessions WHERE token_hash=?", (self._token_hash(token),))
        finally:
            if not self._memory_conn: db.close()

    def migration_version(self) -> int:
        db = self.connect()
        try: return int(db.execute("PRAGMA user_version").fetchone()[0])
        finally:
            if not self._memory_conn: db.close()

    @staticmethod
    def _model(revision: sqlite3.Row) -> dict[str, Any]:
        return json.loads(revision["model_json"])

    @staticmethod
    def _row_json(row: sqlite3.Row, json_fields=()) -> dict[str, Any]:
        result = dict(row)
        for key in json_fields:
            if result.get(key) is not None:
                result[key] = json.loads(result[key])
        return result

    def create_decision(self, title: str, question: str, model: dict[str, Any]) -> dict[str, Any]:
        validate_model(model)
        decision_id, revision_id, stamp = str(uuid.uuid4()), str(uuid.uuid4()), now()
        digest = self._hash(model)
        db = self.connect()
        try:
            with db:
                db.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?)", (decision_id, title.strip(), question.strip(), stamp, stamp, revision_id))
                db.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?)", (revision_id, decision_id, 1,
                           json.dumps(model, ensure_ascii=False), digest, stamp))
        finally:
            if not self._memory_conn: db.close()
        return self.get_decision(decision_id)

    def list_decisions(self) -> list[dict[str, Any]]:
        db = self.connect()
        try:
            rows = db.execute("""SELECT d.*, r.revision_no FROM decisions d
                               LEFT JOIN revisions r ON r.id=d.current_revision_id
                               ORDER BY d.updated_at DESC""").fetchall()
            return [dict(row) for row in rows]
        finally:
            if not self._memory_conn: db.close()

    def get_decision(self, decision_id: str) -> dict[str, Any]:
        db = self.connect()
        try:
            decision = db.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
            if decision is None: raise KeyError("Decision not found.")
            revision = db.execute("SELECT * FROM revisions WHERE id=?", (decision["current_revision_id"],)).fetchone()
            return {**dict(decision), "currentRevision": {"id": revision["id"], "revisionNo": revision["revision_no"],
                    "inputHash": revision["input_hash"], "createdAt": revision["created_at"], "model": self._model(revision)}}
        finally:
            if not self._memory_conn: db.close()

    def get_revision(self, decision_id: str, revision_id: str | None = None) -> dict[str, Any]:
        db = self.connect()
        try:
            row = db.execute("SELECT r.* FROM revisions r JOIN decisions d ON d.id=r.decision_id WHERE d.id=? AND r.id=COALESCE(?,d.current_revision_id)",
                             (decision_id, revision_id)).fetchone()
            if row is None: raise KeyError("Revision not found.")
            return {**dict(row), "model": self._model(row)}
        finally:
            if not self._memory_conn: db.close()

    def save_revision(self, decision_id: str, model: dict[str, Any]) -> dict[str, Any]:
        validate_model(model)
        db = self.connect()
        revision_id, stamp = str(uuid.uuid4()), now()
        try:
            with db:
                row = db.execute("SELECT COALESCE(MAX(revision_no),0)+1 AS n FROM revisions WHERE decision_id=?", (decision_id,)).fetchone()
                if row is None: raise KeyError("Decision not found.")
                revno = row["n"]
                existing = db.execute("SELECT id FROM decisions WHERE id=?", (decision_id,)).fetchone()
                if not existing: raise KeyError("Decision not found.")
                db.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?)", (revision_id, decision_id, revno,
                           json.dumps(model, ensure_ascii=False), self._hash(model), stamp))
                db.execute("UPDATE decisions SET current_revision_id=?,updated_at=? WHERE id=?", (revision_id, stamp, decision_id))
            return self.get_decision(decision_id)
        finally:
            if not self._memory_conn: db.close()

    def save_evaluation(self, decision_id: str, revision_id: str, result: dict[str, Any], scenario_id: str | None = None,
                        model_for_hash: dict[str, Any] | None = None) -> dict[str, Any]:
        evaluation_id, stamp = str(uuid.uuid4()), now()
        revision = self.get_revision(decision_id, revision_id)
        digest = self._hash(model_for_hash) if model_for_hash is not None else revision["input_hash"]
        db = self.connect()
        try:
            with db:
                if scenario_id:
                    found = db.execute("SELECT id FROM scenarios WHERE id=? AND decision_id=?", (scenario_id, decision_id)).fetchone()
                    if not found: raise KeyError("Scenario not found.")
                db.execute("INSERT INTO evaluations VALUES(?,?,?,?,?,?,?,?)", (evaluation_id, decision_id, revision_id,
                           scenario_id, result["algorithmVersion"], digest, json.dumps(result), stamp))
            return self.get_evaluation(evaluation_id)
        finally:
            if not self._memory_conn: db.close()

    def get_evaluation(self, evaluation_id: str) -> dict[str, Any]:
        db = self.connect()
        try:
            row = db.execute("SELECT * FROM evaluations WHERE id=?", (evaluation_id,)).fetchone()
            if not row: raise KeyError("Evaluation not found.")
            result = dict(row); result["result"] = json.loads(result.pop("result_json")); return result
        finally:
            if not self._memory_conn: db.close()

    def list_evaluations(self, decision_id: str) -> list[dict[str, Any]]:
        db = self.connect()
        try:
            rows = db.execute("SELECT * FROM evaluations WHERE decision_id=? ORDER BY created_at DESC", (decision_id,)).fetchall()
            out = []
            for row in rows:
                item = dict(row); item["result"] = json.loads(item.pop("result_json")); out.append(item)
            return out
        finally:
            if not self._memory_conn: db.close()

    def create_scenario(self, decision_id: str, baseline_evaluation_id: str, name: str, overrides: dict[str, Any]) -> dict[str, Any]:
        self.get_evaluation(baseline_evaluation_id)
        scenario_id, stamp = str(uuid.uuid4()), now()
        db = self.connect()
        try:
            with db:
                row = db.execute("SELECT id FROM evaluations WHERE id=? AND decision_id=? AND scenario_id IS NULL", (baseline_evaluation_id, decision_id)).fetchone()
                if not row: raise DecisionValidationError("Scenario must reference a baseline evaluation for this decision.")
                db.execute("INSERT INTO scenarios VALUES(?,?,?,?,?,?)", (scenario_id, decision_id, baseline_evaluation_id,
                           name.strip(), json.dumps(overrides), stamp))
            return self.get_scenario(scenario_id)
        finally:
            if not self._memory_conn: db.close()

    def get_scenario(self, scenario_id: str) -> dict[str, Any]:
        db = self.connect()
        try:
            row = db.execute("SELECT * FROM scenarios WHERE id=?", (scenario_id,)).fetchone()
            if not row: raise KeyError("Scenario not found.")
            return self._row_json(row, ("overrides_json",)) | {"overrides": json.loads(row["overrides_json"])}
        finally:
            if not self._memory_conn: db.close()

    def list_scenarios(self, decision_id: str) -> list[dict[str, Any]]:
        db = self.connect()
        try:
            rows = db.execute("SELECT * FROM scenarios WHERE decision_id=? ORDER BY created_at DESC", (decision_id,)).fetchall()
            return [{**dict(row), "overrides": json.loads(row["overrides_json"])} for row in rows]
        finally:
            if not self._memory_conn: db.close()

    def save_robustness_run(self, decision_id: str, revision_id: str, input_hash: str,
                            settings: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        run_id, stamp = str(uuid.uuid4()), now(); db = self.connect()
        try:
            with db:
                row = db.execute("SELECT id FROM revisions WHERE id=? AND decision_id=?", (revision_id, decision_id)).fetchone()
                if not row: raise KeyError("Decision revision not found.")
                db.execute("INSERT INTO robustness_runs VALUES(?,?,?,?,?,?,?)",
                           (run_id, decision_id, revision_id, input_hash, json.dumps(settings), json.dumps(result), stamp))
            return {"id": run_id, "decision_id": decision_id, "revision_id": revision_id,
                    "input_hash": input_hash, "settings": settings, "result": result, "created_at": stamp}
        finally:
            if not self._memory_conn: db.close()

    def list_robustness_runs(self, decision_id: str) -> list[dict[str, Any]]:
        db = self.connect()
        try:
            rows = db.execute("SELECT * FROM robustness_runs WHERE decision_id=? ORDER BY created_at DESC LIMIT 50", (decision_id,)).fetchall()
            return [{**dict(row), "settings": json.loads(row["settings_json"]), "result": json.loads(row["result_json"])}
                    for row in rows]
        finally:
            if not self._memory_conn: db.close()

    def add_document(self, decision_id: str, filename: str, media_type: str, sha256: str, chunks: list[dict[str, Any]]) -> dict[str, Any]:
        document_id, stamp = str(uuid.uuid4()), now()
        db = self.connect()
        try:
            with db:
                exists = db.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
                if not exists: raise KeyError("Decision not found.")
                db.execute("INSERT INTO documents(id,decision_id,filename,media_type,sha256,created_at,parse_status) VALUES(?,?,?,?,?,?,?)",
                           (document_id, decision_id, filename, media_type, sha256, stamp, "ready"))
                db.executemany("INSERT INTO chunks(id,document_id,chunk_no,page_no,start_offset,end_offset,text) VALUES(?,?,?,?,?,?,?)",
                    [(str(uuid.uuid4()), document_id, c["chunkNo"], c.get("pageNo"), c["start"], c["end"], c["text"]) for c in chunks])
            return self.get_document(document_id)
        finally:
            if not self._memory_conn: db.close()

    def enqueue_document(self, decision_id: str, filename: str, media_type: str, digest: str, payload: bytes) -> dict[str, Any]:
        document_id, job_id, stamp = str(uuid.uuid4()), str(uuid.uuid4()), now()
        db = self.connect()
        try:
            with db:
                if not db.execute("SELECT 1 FROM decisions WHERE id=?", (decision_id,)).fetchone():
                    raise KeyError("Decision not found.")
                pending = db.execute("SELECT COUNT(*) AS n FROM ingestion_jobs WHERE status IN ('queued','processing')").fetchone()["n"]
                if pending >= 5:
                    raise DecisionValidationError("The local document queue is full. Wait for processing to finish, then try again.")
                db.execute("INSERT INTO documents(id,decision_id,filename,media_type,sha256,created_at,parse_status) VALUES(?,?,?,?,?,?,?)",
                           (document_id, decision_id, filename, media_type, digest, stamp, "queued"))
                db.execute("INSERT INTO ingestion_jobs(id,document_id,payload,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                           (job_id, document_id, sqlite3.Binary(payload), "queued", stamp, stamp))
            return self.get_document(document_id)
        finally:
            if not self._memory_conn: db.close()

    def claim_ingestion_job(self) -> dict[str, Any] | None:
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT j.*,d.filename FROM ingestion_jobs j JOIN documents d ON d.id=j.document_id WHERE j.status='queued' ORDER BY j.created_at LIMIT 1").fetchone()
            if not row:
                db.commit(); return None
            stamp = now()
            db.execute("UPDATE ingestion_jobs SET status='processing',updated_at=? WHERE id=?", (stamp, row["id"]))
            db.execute("UPDATE documents SET parse_status='processing',parse_error=NULL WHERE id=?", (row["document_id"],))
            db.commit()
            job = dict(row); job["payload"] = bytes(job["payload"] or b"")
            return job
        except Exception:
            db.rollback(); raise
        finally:
            if not self._memory_conn: db.close()

    def requeue_interrupted_ingestion_jobs(self) -> int:
        db = self.connect(); stamp = now()
        try:
            with db:
                rows = db.execute("SELECT document_id FROM ingestion_jobs WHERE status='processing'").fetchall()
                db.execute("UPDATE ingestion_jobs SET status='queued',error=NULL,updated_at=? WHERE status='processing'", (stamp,))
                db.execute("UPDATE documents SET parse_status='queued',parse_error=NULL WHERE id IN (SELECT document_id FROM ingestion_jobs WHERE status='queued')")
                return len(rows)
        finally:
            if not self._memory_conn: db.close()

    def complete_ingestion_job(self, job_id: str, document_id: str, chunks: list[dict[str, Any]]) -> None:
        db = self.connect(); stamp = now()
        try:
            with db:
                row = db.execute("SELECT status FROM ingestion_jobs WHERE id=? AND document_id=?", (job_id, document_id)).fetchone()
                if not row: return
                db.executemany("INSERT INTO chunks(id,document_id,chunk_no,page_no,start_offset,end_offset,text) VALUES(?,?,?,?,?,?,?)",
                    [(str(uuid.uuid4()), document_id, c["chunkNo"], c.get("pageNo"), c["start"], c["end"], c["text"]) for c in chunks])
                db.execute("UPDATE documents SET parse_status='ready',parse_error=NULL WHERE id=?", (document_id,))
                db.execute("UPDATE ingestion_jobs SET status='completed',payload=NULL,error=NULL,updated_at=? WHERE id=?", (stamp, job_id))
        finally:
            if not self._memory_conn: db.close()

    def fail_ingestion_job(self, job_id: str, document_id: str, safe_error: str) -> None:
        db = self.connect(); stamp = now()
        try:
            with db:
                db.execute("UPDATE documents SET parse_status='failed',parse_error=? WHERE id=?", (safe_error[:300], document_id))
                db.execute("UPDATE ingestion_jobs SET status='failed',payload=NULL,error=?,updated_at=? WHERE id=? AND document_id=?",
                           (safe_error[:300], stamp, job_id, document_id))
        finally:
            if not self._memory_conn: db.close()

    def get_document(self, document_id: str) -> dict[str, Any]:
        db = self.connect()
        try:
            row = db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            if not row: raise KeyError("Document not found.")
            count = db.execute("SELECT COUNT(*) AS n FROM chunks WHERE document_id=?", (document_id,)).fetchone()["n"]
            return {**dict(row), "chunkCount": count}
        finally:
            if not self._memory_conn: db.close()

    def list_documents(self, decision_id: str) -> list[dict[str, Any]]:
        db = self.connect()
        try:
            rows = db.execute("SELECT d.*,COUNT(c.id) AS chunkCount FROM documents d LEFT JOIN chunks c ON c.document_id=d.id WHERE d.decision_id=? GROUP BY d.id ORDER BY d.created_at DESC", (decision_id,)).fetchall()
            return [dict(row) for row in rows]
        finally:
            if not self._memory_conn: db.close()

    def list_chunks(self, decision_id: str) -> list[dict[str, Any]]:
        db = self.connect()
        try:
            rows = db.execute("SELECT c.*,d.filename,d.decision_id FROM chunks c JOIN documents d ON d.id=c.document_id WHERE d.decision_id=? ORDER BY d.created_at DESC,c.chunk_no", (decision_id,)).fetchall()
            return [dict(row) for row in rows]
        finally:
            if not self._memory_conn: db.close()

    def set_chunk_status(self, chunk_id: str, status: str) -> dict[str, Any]:
        if status not in {"verified", "rejected", "unverified"}: raise DecisionValidationError("Invalid evidence status.")
        db = self.connect()
        try:
            with db:
                cur = db.execute("UPDATE chunks SET verified_status=?,verified_at=? WHERE id=?", (status, now() if status == "verified" else None, chunk_id))
                if cur.rowcount == 0: raise KeyError("Evidence passage not found.")
                row = db.execute("SELECT c.*,d.filename,d.decision_id FROM chunks c JOIN documents d ON d.id=c.document_id WHERE c.id=?", (chunk_id,)).fetchone()
                return dict(row)
        finally:
            if not self._memory_conn: db.close()

    def get_chunk(self, chunk_id: str) -> dict[str, Any]:
        db = self.connect()
        try:
            row = db.execute("SELECT c.*,d.filename,d.decision_id FROM chunks c JOIN documents d ON d.id=c.document_id WHERE c.id=?", (chunk_id,)).fetchone()
            if not row: raise KeyError("Evidence passage not found.")
            return dict(row)
        finally:
            if not self._memory_conn: db.close()

    def delete_document(self, document_id: str) -> None:
        db = self.connect()
        try:
            with db:
                cur = db.execute("DELETE FROM documents WHERE id=?", (document_id,))
                if not cur.rowcount: raise KeyError("Document not found.")
        finally:
            if not self._memory_conn: db.close()

    def delete_decision(self, decision_id: str) -> None:
        db = self.connect()
        try:
            with db:
                if not db.execute("SELECT 1 FROM decisions WHERE id=?", (decision_id,)).fetchone():
                    raise KeyError("Decision not found.")
                db.execute("DELETE FROM scenarios WHERE decision_id=?", (decision_id,))
                db.execute("DELETE FROM evaluations WHERE decision_id=?", (decision_id,))
                db.execute("DELETE FROM documents WHERE decision_id=?", (decision_id,))
                db.execute("DELETE FROM revisions WHERE decision_id=?", (decision_id,))
                cur = db.execute("DELETE FROM decisions WHERE id=?", (decision_id,))
                if not cur.rowcount: raise KeyError("Decision not found.")
        finally:
            if not self._memory_conn: db.close()

