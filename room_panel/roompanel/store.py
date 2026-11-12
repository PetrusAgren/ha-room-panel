"""SQLite-backed persistent store for room profiles, panels and credentials."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS records (kind TEXT NOT NULL, id TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS pairings (panel_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (panel_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, last_seen REAL NOT NULL);
            """)
            self._db.execute("INSERT OR IGNORE INTO kv(key,value) VALUES('secret',?)", (secrets.token_hex(32),))

    @staticmethod
    def _kind(kind: str) -> str:
        if kind not in ("profiles", "panels"):
            raise ValueError("kind must be 'profiles' or 'panels'")
        return kind

    def settings(self) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute("SELECT value FROM kv WHERE key='settings'").fetchone()
            return json.loads(row[0]) if row else {}

    def save_settings(self, value: dict[str, Any]) -> None:
        if not isinstance(value, dict):
            raise TypeError("settings must be a dict")
        with self._lock, self._db:
            self._db.execute("INSERT INTO kv(key,value) VALUES('settings',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(value),))

    def list(self, kind: str) -> list[dict[str, Any]]:
        kind = self._kind(kind)
        with self._lock:
            return [json.loads(row[0]) for row in self._db.execute("SELECT value FROM records WHERE kind=? ORDER BY rowid", (kind,))]

    def get(self, kind: str, record_id: str) -> dict[str, Any] | None:
        kind = self._kind(kind)
        with self._lock:
            row = self._db.execute("SELECT value FROM records WHERE kind=? AND id=?", (kind, str(record_id))).fetchone()
            return json.loads(row[0]) if row else None

    def put(self, kind: str, record: dict[str, Any]) -> dict[str, Any]:
        kind = self._kind(kind)
        if not isinstance(record, dict):
            raise TypeError("record must be a dict")
        detached = dict(record)
        ident = detached.get("id")
        if not isinstance(ident, str) or not ident:
            ident = secrets.token_urlsafe(12)
            detached["id"] = ident
        with self._lock, self._db:
            self._db.execute("INSERT INTO records(kind,id,value) VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET value=excluded.value", (kind, ident, json.dumps(detached)))
        return dict(detached)

    def delete(self, kind: str, record_id: str) -> bool:
        kind = self._kind(kind)
        with self._lock, self._db:
            cur = self._db.execute("DELETE FROM records WHERE kind=? AND id=?", (kind, str(record_id)))
            return cur.rowcount > 0

    def _digest(self, purpose: str, panel_id: str, token: str) -> str:
        row = self._db.execute("SELECT value FROM kv WHERE key='secret'").fetchone()
        key = bytes.fromhex(row[0])
        return hmac.new(key, f"{purpose}\0{panel_id}\0{token}".encode(), hashlib.sha256).hexdigest()

    def create_pairing(self, panel_id: str) -> str:
        panel_id = str(panel_id)
        raw = secrets.token_urlsafe(32)
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO pairings(panel_id,token_hash,created) VALUES(?,?,?)", (panel_id, self._digest("pair", panel_id, raw), time.time()))
        return raw

    def redeem_pairing(self, panel_id: str, token: str) -> str | None:
        panel_id, token = str(panel_id), str(token)
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute("SELECT token_hash FROM pairings WHERE panel_id=?", (panel_id,)).fetchone()
                actual = self._digest("pair", panel_id, token)
                if not row or not hmac.compare_digest(row[0], actual):
                    self._db.execute("ROLLBACK")
                    return None
                self._db.execute("DELETE FROM pairings WHERE panel_id=?", (panel_id,))
                raw = secrets.token_urlsafe(32)
                self._db.execute("INSERT OR REPLACE INTO sessions(panel_id,token_hash,last_seen) VALUES(?,?,?)", (panel_id, self._digest("session", panel_id, raw), time.time()))
                self._db.execute("COMMIT")
                return raw
            except BaseException:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise

    def session(self, panel_id: str, token: str) -> bool:
        panel_id, token = str(panel_id), str(token)
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute("SELECT token_hash FROM sessions WHERE panel_id=?", (panel_id,)).fetchone()
                if not row or not hmac.compare_digest(row[0], self._digest("session", panel_id, token)):
                    self._db.execute("ROLLBACK")
                    return False
                self._db.execute("UPDATE sessions SET last_seen=? WHERE panel_id=?", (time.time(), panel_id))
                self._db.execute("COMMIT")
                return True
            except BaseException:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise

    def session_status(self, panel_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute("SELECT last_seen FROM sessions WHERE panel_id=?", (str(panel_id),)).fetchone()
            return {"paired": row is not None, "last_seen": float(row[0]) if row else None}

    def revoke(self, panel_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM sessions WHERE panel_id=?", (str(panel_id),))
            self._db.execute("DELETE FROM pairings WHERE panel_id=?", (str(panel_id),))

    def close(self) -> None:
        with self._lock:
            self._db.close()


def hash_pin(pin: str) -> str:
    if not isinstance(pin, str) or not pin:
        raise ValueError("PIN must be a non-empty string")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(pin.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_pin(pin: str, encoded: str) -> bool:
    try:
        algorithm, salt, expected = encoded.split("$", 2)
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(pin.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (AttributeError, TypeError, ValueError, UnicodeError):
        return False
