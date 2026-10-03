"""SQLite stores and shared Gate leases; no member answers enter coordination."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from . import config


COORD_SCHEMA = """
CREATE TABLE IF NOT EXISTS resources(guild INTEGER,key TEXT,object_id INTEGER,kind TEXT,owner TEXT,baseline TEXT,desired TEXT,state TEXT DEFAULT 'active',PRIMARY KEY(guild,key),UNIQUE(guild,object_id));
CREATE TABLE IF NOT EXISTS integrations(guild INTEGER,bot INTEGER,scope TEXT,grants TEXT DEFAULT '{}',active INTEGER DEFAULT 0,PRIMARY KEY(guild,bot));
CREATE TABLE IF NOT EXISTS leases(guild INTEGER,key TEXT,token TEXT,expires REAL,PRIMARY KEY(guild,key));
"""
SCHEMA = """
CREATE TABLE IF NOT EXISTS crew_settings(guild INTEGER PRIMARY KEY,revision INTEGER,body TEXT,started REAL);
CREATE TABLE IF NOT EXISTS crew_history(id INTEGER PRIMARY KEY,guild INTEGER,actor INTEGER,at REAL,body TEXT);
CREATE TABLE IF NOT EXISTS cases(id INTEGER PRIMARY KEY,guild INTEGER,user INTEGER,actor INTEGER,action TEXT,reason TEXT,at REAL,status TEXT,duration INTEGER DEFAULT 0,source TEXT,event_key TEXT,revoked INTEGER DEFAULT 0,result TEXT DEFAULT '',UNIQUE(guild,event_key));
CREATE INDEX IF NOT EXISTS cases_member ON cases(guild,user,at);
CREATE TABLE IF NOT EXISTS tickets(id INTEGER PRIMARY KEY,guild INTEGER,user INTEGER,channel INTEGER UNIQUE,topic TEXT,number INTEGER,subject TEXT,details TEXT,status TEXT,claimed INTEGER DEFAULT 0,created REAL,closed REAL DEFAULT 0,delete_at REAL DEFAULT 0,control_message INTEGER DEFAULT 0,operation TEXT DEFAULT '',last_error TEXT DEFAULT '');
CREATE UNIQUE INDEX IF NOT EXISTS one_active_ticket ON tickets(guild,user) WHERE status IN ('creating','open','closing','reopening');
CREATE TABLE IF NOT EXISTS counters(guild INTEGER,key TEXT,value INTEGER,PRIMARY KEY(guild,key));
CREATE TABLE IF NOT EXISTS panels(guild INTEGER,key TEXT,channel INTEGER,message INTEGER,PRIMARY KEY(guild,key));
CREATE TABLE IF NOT EXISTS cooldowns(guild INTEGER,user INTEGER,key TEXT,at REAL,delay REAL,PRIMARY KEY(guild,user,key));
CREATE TABLE IF NOT EXISTS heat(guild INTEGER,user INTEGER,value REAL,at REAL,PRIMARY KEY(guild,user));
CREATE TABLE IF NOT EXISTS activity(guild INTEGER,user INTEGER,day INTEGER,count INTEGER,last REAL,PRIMARY KEY(guild,user,day));
CREATE TABLE IF NOT EXISTS seen(guild INTEGER,message INTEGER,at REAL,PRIMARY KEY(guild,message));
CREATE TABLE IF NOT EXISTS previews(guild INTEGER,user INTEGER,message INTEGER,channel INTEGER,at REAL,text TEXT,PRIMARY KEY(guild,message));
CREATE TABLE IF NOT EXISTS errors(id TEXT PRIMARY KEY,guild INTEGER,area TEXT,type TEXT,detail TEXT,at REAL);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,guild INTEGER,actor INTEGER,area TEXT,detail TEXT,at REAL);
CREATE TABLE IF NOT EXISTS temporary(guild INTEGER,channel INTEGER,kind TEXT,original TEXT,applied TEXT,expires REAL,state TEXT,PRIMARY KEY(guild,channel,kind));
CREATE TABLE IF NOT EXISTS native(guild INTEGER PRIMARY KEY,rule INTEGER,baseline TEXT,state TEXT);
"""


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path, schema):
        self.path = str(path)
        self.schema = schema
        self.conn = None
        self.lock = asyncio.Lock()

    async def open(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        def init():
            self.conn = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.execute("PRAGMA busy_timeout=15000")
            self.conn.executescript(self.schema)
            self.conn.commit()
        await asyncio.to_thread(init)

    async def run(self, fn):
        async with self.lock:
            if self.conn is None:
                raise RuntimeError("Database is closed.")
            def transaction():
                try:
                    self.conn.execute("BEGIN IMMEDIATE")
                    result = fn(self.conn)
                    self.conn.commit()
                    return result
                except BaseException:
                    self.conn.rollback()
                    raise
            task = asyncio.create_task(asyncio.to_thread(transaction))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                # Do not release the lock while a worker still owns a transaction.
                try:
                    await task
                finally:
                    raise

    async def query(self, sql, args=()):
        return await self.run(lambda c: [dict(row) for row in c.execute(sql, args).fetchall()])

    async def execute(self, sql, args=()):
        return await self.run(lambda c: c.execute(sql, args).rowcount)

    async def close(self):
        async with self.lock:
            if self.conn:
                await asyncio.to_thread(self.conn.close)
                self.conn = None


class Database(Store):
    def __init__(self, path, coordination_path):
        super().__init__(path, SCHEMA)
        if str(Path(path).resolve()) == str(Path(coordination_path).resolve()):
            raise ValueError("Operational and coordination databases must be separate.")
        self.coord = Store(coordination_path, COORD_SCHEMA)

    async def open(self):
        await super().open()
        await self.coord.open()

    async def close(self):
        await self.coord.close()
        await super().close()

    async def settings(self, guild):
        def get(c):
            row = c.execute("SELECT * FROM crew_settings WHERE guild=?", (guild,)).fetchone()
            if not row:
                cfg = config.defaults()
                c.execute("INSERT INTO crew_settings VALUES(?,1,?,?)", (guild, json.dumps(cfg), time.time()))
                return cfg, 1
            return json.loads(row["body"]), row["revision"]
        return await self.run(get)

    async def save(self, guild, cfg, revision, actor):
        config.validate(cfg)
        def write(c):
            row = c.execute("SELECT * FROM crew_settings WHERE guild=?", (guild,)).fetchone()
            if not row or row["revision"] != revision:
                raise Conflict("Settings changed. Reopen this section and try again.")
            c.execute("INSERT INTO crew_history(guild,actor,at,body) VALUES(?,?,?,?)", (guild, actor, time.time(), row["body"]))
            c.execute("UPDATE crew_settings SET body=?,revision=revision+1 WHERE guild=?", (json.dumps(cfg), guild))
            c.execute("DELETE FROM crew_history WHERE guild=? AND id NOT IN (SELECT id FROM crew_history WHERE guild=? ORDER BY id DESC LIMIT 30)", (guild, guild))
        await self.run(write)
        await self.audit(guild, actor, "settings", f"revision {revision} -> {revision + 1}")

    async def audit(self, guild, actor, area, detail):
        await self.execute("INSERT INTO audit(guild,actor,area,detail,at) VALUES(?,?,?,?,?)", (guild, actor, area, str(detail)[:2000], time.time()))

    async def resources(self, guild):
        rows = await self.coord.query("SELECT * FROM resources WHERE guild=?", (guild,))
        return {row["key"]: {**row, "baseline": json.loads(row["baseline"]), "desired": json.loads(row["desired"])} for row in rows}

    async def acquire(self, guild, key="server-setup", ttl=180):
        now, token = time.time(), uuid.uuid4().hex
        def claim(c):
            row = c.execute("SELECT * FROM leases WHERE guild=? AND key=?", (guild, key)).fetchone()
            if row and row["expires"] > now:
                raise Conflict("Another bot or task holds this setup lock. Try again shortly.")
            c.execute("INSERT INTO leases VALUES(?,?,?,?) ON CONFLICT(guild,key) DO UPDATE SET token=excluded.token,expires=excluded.expires", (guild, key, token, now + ttl))
            return token
        return await self.coord.run(claim)

    async def renew(self, guild, key, token, ttl=180):
        now = time.time()
        changed = await self.coord.execute("UPDATE leases SET expires=? WHERE guild=? AND key=? AND token=? AND expires>?", (now + ttl, guild, key, token, now))
        if not changed:
            raise Conflict("Setup lease expired. Retry after checking Health.")

    @asynccontextmanager
    async def lease(self, guild, key="server-setup", ttl=180):
        token = await self.acquire(guild, key, ttl)
        try:
            yield token
        finally:
            await self.coord.execute("DELETE FROM leases WHERE guild=? AND key=? AND token=?", (guild, key, token))

    async def case(self, guild, user, actor, action, reason, status="done", duration=0, source="manual", event_key=None):
        event_key = event_key or "manual:" + uuid.uuid4().hex
        def add(c):
            existing = c.execute("SELECT id FROM cases WHERE guild=? AND event_key=?", (guild, event_key)).fetchone()
            if existing:
                return existing["id"], False
            cur = c.execute("INSERT INTO cases(guild,user,actor,action,reason,at,status,duration,source,event_key) VALUES(?,?,?,?,?,?,?,?,?,?)", (guild, user, actor, action, reason[:1000], time.time(), status, duration, source, event_key))
            return cur.lastrowid, True
        return await self.run(add)

    async def warning_count(self, guild, user, days):
        rows = await self.query("SELECT COUNT(*) AS n FROM cases WHERE guild=? AND user=? AND action='warn' AND status='done' AND revoked=0 AND at>=?", (guild, user, time.time() - days * 86400))
        return rows[0]["n"]

    async def revoke(self, guild, case_id, actor):
        changed = await self.execute("UPDATE cases SET revoked=1 WHERE guild=? AND id=? AND action='warn' AND revoked=0", (guild, case_id))
        if not changed:
            raise ValueError("No active warning matches that case ID.")
        await self.audit(guild, actor, "warning_revoked", str(case_id))

    async def add_heat(self, guild, user, amount, decay):
        now = time.time()
        def add(c):
            row = c.execute("SELECT * FROM heat WHERE guild=? AND user=?", (guild, user)).fetchone()
            value = max(0, row["value"] - max(0, now - row["at"]) * decay / 60) if row else 0
            value = min(10000, value + amount)
            c.execute("INSERT INTO heat VALUES(?,?,?,?) ON CONFLICT(guild,user) DO UPDATE SET value=excluded.value,at=excluded.at", (guild, user, value, now))
            return value
        return await self.run(add)

    async def cooldown(self, guild, user, key, delay):
        now = time.time()
        def reserve(c):
            row = c.execute("SELECT * FROM cooldowns WHERE guild=? AND user=? AND key=?", (guild, user, key)).fetchone()
            if row and row["at"] + row["delay"] > now:
                raise ValueError(f"Try again in {max(1, int(row['at'] + row['delay'] - now))} second(s).")
            c.execute("INSERT INTO cooldowns VALUES(?,?,?,?,?) ON CONFLICT(guild,user,key) DO UPDATE SET at=excluded.at,delay=excluded.delay", (guild, user, key, now, delay))
            return now
        return await self.run(reserve)

    async def reserve_ticket(self, guild, user, topic, subject, details):
        def reserve(c):
            row = c.execute("SELECT channel FROM tickets WHERE guild=? AND user=? AND status IN ('creating','open','closing','reopening')", (guild, user)).fetchone()
            if row:
                raise ValueError(f"You already have an active ticket: {row['channel'] or 'being created'}.")
            c.execute("INSERT INTO counters VALUES(?,'ticket',1) ON CONFLICT(guild,key) DO UPDATE SET value=value+1", (guild,))
            number = c.execute("SELECT value FROM counters WHERE guild=? AND key='ticket'", (guild,)).fetchone()[0]
            cur = c.execute("INSERT INTO tickets(guild,user,topic,number,subject,details,status,created) VALUES(?,?,?,?,?,?,'creating',?)", (guild, user, topic, number, subject[:200], details[:1800], time.time()))
            return cur.lastrowid, number
        return await self.run(reserve)

    async def ticket(self, guild, channel):
        rows = await self.query("SELECT * FROM tickets WHERE guild=? AND channel=?", (guild, channel))
        return rows[0] if rows else None

    async def activity_message(self, guild, user, channel, message, text, cfg, now=None):
        now = time.time() if now is None else now
        day = int(now // 86400)
        def record(c):
            new = c.execute("INSERT OR IGNORE INTO seen VALUES(?,?,?)", (guild, message, now)).rowcount
            if new:
                c.execute("INSERT INTO activity VALUES(?,?,?,1,?) ON CONFLICT(guild,user,day) DO UPDATE SET count=count+1,last=MAX(last,excluded.last)", (guild, user, day, now))
            if cfg["store_previews"]:
                c.execute("INSERT OR REPLACE INTO previews VALUES(?,?,?,?,?,?)", (guild, user, message, channel, now, text[:200]))
                c.execute("DELETE FROM previews WHERE guild=? AND user=? AND message NOT IN (SELECT message FROM previews WHERE guild=? AND user=? ORDER BY at DESC LIMIT ?)", (guild, user, guild, user, cfg["preview_count"]))
        await self.run(record)

    async def prune(self, guild, cfg):
        now = time.time()
        await self.execute("DELETE FROM activity WHERE guild=? AND day<?", (guild, int(now // 86400) - cfg["activity_days"]))
        await self.execute("DELETE FROM seen WHERE guild=? AND at<?", (guild, now - cfg["activity_days"] * 86400))
        await self.execute("DELETE FROM previews WHERE guild=? AND (at<? OR ?=0)", (guild, now - 30 * 86400, int(cfg["store_previews"])))
        await self.execute("DELETE FROM cases WHERE guild=? AND at<? AND status!='pending'", (guild, now - cfg["case_retention_days"] * 86400))
        await self.execute("DELETE FROM errors WHERE guild=? AND at<?", (guild, now - cfg["error_retention_days"] * 86400))
        await self.execute("DELETE FROM audit WHERE guild=? AND at<?", (guild, now - cfg["case_retention_days"] * 86400))
        await self.execute("DELETE FROM cooldowns WHERE guild=? AND at+delay<?", (guild, now - 86400))
        await self.execute("DELETE FROM heat WHERE guild=? AND at<?", (guild, now - 7 * 86400))
