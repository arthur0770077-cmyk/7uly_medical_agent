# -*- coding: utf-8 -*-
"""Relational persistence for the lab-report agent.

The demo uses SQLite by default so it can run on a laptop and on Render free
instances without external services. The schema mirrors the MySQL DDL in
``schema_mysql.sql`` so the same product model can move to MySQL later.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB_PATH = Path(os.environ.get("DB_PATH", DATA / "agent_store.sqlite3"))


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def short_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


class RelationalStore:
    def __init__(self, path: Path = DB_PATH) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                  user_id TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  sex TEXT,
                  age INTEGER,
                  phone TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  UNIQUE(name, phone)
                );

                CREATE TABLE IF NOT EXISTS user_profiles (
                  user_id TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                  chronic_diseases TEXT,
                  medications TEXT,
                  allergies TEXT,
                  family_history TEXT,
                  pregnancy_status TEXT,
                  lifestyle TEXT,
                  risk_preferences TEXT,
                  json_profile TEXT NOT NULL DEFAULT '{}',
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS report_cases (
                  task_id TEXT PRIMARY KEY,
                  user_id TEXT REFERENCES users(user_id) ON DELETE SET NULL,
                  created_at TEXT NOT NULL,
                  raw_text TEXT,
                  source_type TEXT,
                  risk_level TEXT,
                  risk_score INTEGER,
                  requires_review INTEGER,
                  safety_notice TEXT,
                  json_result TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS observations (
                  obs_id TEXT PRIMARY KEY,
                  task_id TEXT NOT NULL REFERENCES report_cases(task_id) ON DELETE CASCADE,
                  code TEXT,
                  name TEXT,
                  value REAL,
                  unit TEXT,
                  status TEXT,
                  severity INTEGER,
                  reference_range TEXT,
                  standard_id TEXT,
                  source TEXT
                );

                CREATE TABLE IF NOT EXISTS review_tasks (
                  review_id TEXT PRIMARY KEY,
                  task_id TEXT NOT NULL REFERENCES report_cases(task_id) ON DELETE CASCADE,
                  status TEXT,
                  risk_level TEXT,
                  reason TEXT,
                  doctor_review_json TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS followups (
                  follow_id TEXT PRIMARY KEY,
                  task_id TEXT NOT NULL REFERENCES report_cases(task_id) ON DELETE CASCADE,
                  owner TEXT,
                  title TEXT,
                  priority TEXT,
                  due_date TEXT,
                  status TEXT,
                  questions_json TEXT,
                  feedback_json TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS audit_events (
                  event_id TEXT PRIMARY KEY,
                  time TEXT NOT NULL,
                  actor TEXT,
                  action TEXT,
                  target TEXT,
                  detail TEXT
                );

                CREATE TABLE IF NOT EXISTS conversation_turns (
                  turn_id TEXT PRIMARY KEY,
                  user_id TEXT REFERENCES users(user_id) ON DELETE SET NULL,
                  role TEXT NOT NULL,
                  content TEXT NOT NULL,
                  metadata_json TEXT,
                  created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS knowledge_documents (
                  doc_id TEXT PRIMARY KEY,
                  title TEXT NOT NULL,
                  source_id TEXT,
                  organization TEXT,
                  category TEXT,
                  evidence_level TEXT,
                  codes_json TEXT,
                  text TEXT NOT NULL,
                  url TEXT,
                  metadata_json TEXT,
                  updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_cases_user_time ON report_cases(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_observations_code ON observations(code);
                CREATE INDEX IF NOT EXISTS idx_followups_due ON followups(due_date, status);
                CREATE INDEX IF NOT EXISTS idx_knowledge_category ON knowledge_documents(category);
                """
            )

    def upsert_user(self, patient: dict[str, Any], profile: dict[str, Any] | None = None) -> dict[str, Any]:
        name = str(patient.get("name") or "模拟用户").strip()
        phone = str(patient.get("phone") or "").strip()
        now = now_iso()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE name = ? AND COALESCE(phone, '') = ?",
                (name, phone),
            ).fetchone()
            if row:
                user_id = row["user_id"]
                conn.execute(
                    "UPDATE users SET sex=?, age=?, updated_at=? WHERE user_id=?",
                    (patient.get("sex"), int(patient.get("age") or 0) or None, now, user_id),
                )
            else:
                user_id = patient.get("user_id") or short_id("USR")
                conn.execute(
                    """
                    INSERT INTO users(user_id, name, sex, age, phone, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, name, patient.get("sex"), int(patient.get("age") or 0) or None, phone, now, now),
                )
            if profile is not None:
                self._upsert_profile(conn, user_id, profile, now)
            return self.get_user(user_id) or {"user_id": user_id, "name": name}

    def _upsert_profile(self, conn: sqlite3.Connection, user_id: str, profile: dict[str, Any], now: str) -> None:
        fields = {
            "chronic_diseases": profile.get("chronic_diseases") or profile.get("chronicDiseases") or "",
            "medications": profile.get("medications") or "",
            "allergies": profile.get("allergies") or "",
            "family_history": profile.get("family_history") or profile.get("familyHistory") or "",
            "pregnancy_status": profile.get("pregnancy_status") or profile.get("pregnancyStatus") or "",
            "lifestyle": profile.get("lifestyle") or "",
            "risk_preferences": profile.get("risk_preferences") or profile.get("riskPreferences") or "",
        }
        conn.execute(
            """
            INSERT INTO user_profiles(
              user_id, chronic_diseases, medications, allergies, family_history,
              pregnancy_status, lifestyle, risk_preferences, json_profile, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              chronic_diseases=excluded.chronic_diseases,
              medications=excluded.medications,
              allergies=excluded.allergies,
              family_history=excluded.family_history,
              pregnancy_status=excluded.pregnancy_status,
              lifestyle=excluded.lifestyle,
              risk_preferences=excluded.risk_preferences,
              json_profile=excluded.json_profile,
              updated_at=excluded.updated_at
            """,
            (
                user_id,
                fields["chronic_diseases"],
                fields["medications"],
                fields["allergies"],
                fields["family_history"],
                fields["pregnancy_status"],
                fields["lifestyle"],
                fields["risk_preferences"],
                dumps(profile),
                now,
            ),
        )

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            if not row:
                return None
            profile = conn.execute("SELECT * FROM user_profiles WHERE user_id=?", (user_id,)).fetchone()
        user = dict(row)
        if profile:
            profile_dict = dict(profile)
            profile_dict["json_profile"] = loads(profile_dict.get("json_profile"), {})
            user["profile"] = profile_dict
        else:
            user["profile"] = {}
        return user

    def list_users(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY updated_at DESC LIMIT 100").fetchall()
        return [dict(row) for row in rows]

    def record_analysis(self, result: dict[str, Any], user_id: str | None = None, source_type: str = "manual") -> None:
        now = now_iso()
        parsed = result.get("parsed") or {}
        risk = result.get("risk") or {}
        followup = result.get("followup") or {}
        review = result.get("review_task") or {}
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO report_cases(
                  task_id, user_id, created_at, raw_text, source_type, risk_level,
                  risk_score, requires_review, safety_notice, json_result
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result["task_id"],
                    user_id,
                    result.get("created_at") or now,
                    parsed.get("raw_text", ""),
                    source_type,
                    risk.get("risk_level"),
                    int(risk.get("risk_score") or 0),
                    1 if risk.get("requires_review") else 0,
                    result.get("safety_notice", ""),
                    dumps(result),
                ),
            )
            conn.execute("DELETE FROM observations WHERE task_id=?", (result["task_id"],))
            for item in result.get("explanations", []):
                standard = item.get("standard") if isinstance(item.get("standard"), dict) else {}
                standard_id = item.get("standard_id") or standard.get("id")
                conn.execute(
                    """
                    INSERT INTO observations(
                      obs_id, task_id, code, name, value, unit, status, severity,
                      reference_range, standard_id, source
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        short_id("OBS"),
                        result["task_id"],
                        item.get("code"),
                        item.get("name"),
                        float(item.get("value") or 0),
                        item.get("unit"),
                        item.get("status"),
                        int(item.get("severity") or 0),
                        item.get("reference"),
                        standard_id,
                        item.get("source"),
                    ),
                )
            if review.get("review_id"):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO review_tasks(
                      review_id, task_id, status, risk_level, reason, doctor_review_json, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        review["review_id"],
                        result["task_id"],
                        review.get("status"),
                        risk.get("risk_level"),
                        review.get("reason"),
                        dumps(review),
                        result.get("created_at") or now,
                        now,
                    ),
                )
            if followup.get("follow_id"):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO followups(
                      follow_id, task_id, owner, title, priority, due_date, status,
                      questions_json, feedback_json, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        followup["follow_id"],
                        result["task_id"],
                        followup.get("owner"),
                        followup.get("title"),
                        followup.get("priority"),
                        followup.get("due_date"),
                        followup.get("status"),
                        dumps(followup.get("questions", [])),
                        dumps(followup.get("feedback", {})),
                        result.get("created_at") or now,
                        now,
                    ),
                )

    def history(self, user_id: str | None = None, patient_name: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT c.* FROM report_cases c LEFT JOIN users u ON c.user_id = u.user_id"
        params: list[Any] = []
        filters = []
        if user_id:
            filters.append("c.user_id = ?")
            params.append(user_id)
        if patient_name:
            filters.append("u.name = ?")
            params.append(patient_name)
        if filters:
            sql += " WHERE " + " AND ".join(filters)
        sql += " ORDER BY c.created_at DESC LIMIT 50"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [loads(row["json_result"], {}) for row in rows]

    def add_audit(self, actor: str, action: str, target: str, detail: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO audit_events(event_id, time, actor, action, target, detail) VALUES (?, ?, ?, ?, ?, ?)",
                (short_id("AUD"), now_iso(), actor, action, target, detail),
            )

    def audit_events(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM audit_events ORDER BY time DESC LIMIT 100").fetchall()
        return [dict(row) for row in rows]

    def clear_runtime_data(self) -> None:
        with self.connect() as conn:
            for table in [
                "conversation_turns",
                "audit_events",
                "followups",
                "review_tasks",
                "observations",
                "report_cases",
            ]:
                conn.execute(f"DELETE FROM {table}")

    def add_conversation_turn(
        self, user_id: str | None, role: str, content: str, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        item = {
            "turn_id": short_id("TURN"),
            "user_id": user_id,
            "role": role,
            "content": content,
            "metadata_json": dumps(metadata or {}),
            "created_at": now_iso(),
        }
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO conversation_turns(turn_id, user_id, role, content, metadata_json, created_at)
                VALUES (:turn_id, :user_id, :role, :content, :metadata_json, :created_at)
                """,
                item,
            )
        item["metadata"] = metadata or {}
        return item

    def conversation(self, user_id: str | None, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM conversation_turns
                WHERE (? IS NULL OR user_id = ?)
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, user_id, limit),
            ).fetchall()
        turns = []
        for row in reversed(rows):
            item = dict(row)
            item["metadata"] = loads(item.pop("metadata_json", None), {})
            turns.append(item)
        return turns

    def seed_knowledge_documents(self, docs: list[dict[str, Any]]) -> None:
        now = now_iso()
        with self.connect() as conn:
            for doc in docs:
                doc_id = str(doc.get("doc_id") or doc.get("id") or short_id("KB"))
                conn.execute(
                    """
                    INSERT INTO knowledge_documents(
                      doc_id, title, source_id, organization, category, evidence_level,
                      codes_json, text, url, metadata_json, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(doc_id) DO UPDATE SET
                      title=excluded.title,
                      source_id=excluded.source_id,
                      organization=excluded.organization,
                      category=excluded.category,
                      evidence_level=excluded.evidence_level,
                      codes_json=excluded.codes_json,
                      text=excluded.text,
                      url=excluded.url,
                      metadata_json=excluded.metadata_json,
                      updated_at=excluded.updated_at
                    """,
                    (
                        doc_id,
                        doc.get("title") or doc_id,
                        doc.get("source_id", ""),
                        doc.get("organization", ""),
                        doc.get("category", ""),
                        doc.get("evidence_level", ""),
                        dumps(doc.get("codes", [])),
                        doc.get("text", ""),
                        doc.get("url", ""),
                        dumps(doc.get("metadata", {})),
                        doc.get("updated_at") or now,
                    ),
                )

    def add_knowledge_document(self, doc: dict[str, Any]) -> dict[str, Any]:
        doc = dict(doc)
        doc["doc_id"] = str(doc.get("doc_id") or doc.get("id") or short_id("KB"))
        self.seed_knowledge_documents([doc])
        return doc

    def knowledge_documents(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_documents ORDER BY category, title LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._knowledge_row(row) for row in rows]

    def search_knowledge(
        self,
        query: str,
        codes: list[str] | None = None,
        categories: list[str] | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        query = query or ""
        code_set = {code.upper() for code in (codes or []) if code}
        category_set = {category for category in (categories or []) if category}
        tokens = self._query_tokens(query)
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM knowledge_documents").fetchall()
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            doc = self._knowledge_row(row)
            if category_set and doc.get("category") not in category_set:
                continue
            doc_codes = {code.upper() for code in doc.get("codes", [])}
            haystack = f"{doc.get('title', '')} {doc.get('text', '')} {doc.get('source_id', '')}".lower()
            score = 0.0
            for token in tokens:
                if token and token in haystack:
                    score += 1.5 if len(token) > 1 else 0.5
            overlap = code_set & doc_codes
            if overlap:
                breadth_penalty = max(len(doc_codes), 1) ** 0.5
                score += 12.0 * len(overlap) / breadth_penalty
            if code_set and doc.get("source_id", "").upper() in code_set:
                score += 4.0
            if not tokens and not code_set:
                score = 1.0
            if score > 0:
                doc["score"] = round(score, 2)
                scored.append((score, doc))
        return [doc for _, doc in sorted(scored, key=lambda pair: pair[0], reverse=True)[:limit]]

    @staticmethod
    def _query_tokens(query: str) -> list[str]:
        raw = query.replace("/", " ").replace("、", " ").replace("，", " ").replace("。", " ")
        tokens = [part.strip().lower() for part in raw.split() if part.strip()]
        tokens.extend(part.lower() for part in re_split_medical_terms(query) if part)
        return list(dict.fromkeys(tokens))

    @staticmethod
    def _knowledge_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["codes"] = loads(item.pop("codes_json", None), [])
        item["metadata"] = loads(item.pop("metadata_json", None), {})
        return item


def re_split_medical_terms(text: str) -> list[str]:
    separators = " \t\r\n,;:|/()[]{}+-=，。；：、（）【】"
    tokens: list[str] = []
    buff = []
    for char in text or "":
        if char in separators:
            if buff:
                tokens.append("".join(buff))
                buff = []
        else:
            buff.append(char)
    if buff:
        tokens.append("".join(buff))
    return tokens


STORE = RelationalStore()
