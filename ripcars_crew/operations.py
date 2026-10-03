"""Private audit/error delivery, diagnostics, watchdog and consistent backups."""
from __future__ import annotations

import asyncio
import json
import math
import os
import secrets
import socket
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import discord

from .ui_common import embed


REQUIRED = ("view_channel", "read_message_history", "send_messages", "embed_links",
            "attach_files", "manage_messages", "manage_channels", "manage_roles",
            "moderate_members", "kick_members", "ban_members", "view_audit_log")


def permissions():
    return discord.Permissions(**dict.fromkeys(REQUIRED, True))


def private_channel(guild, channel, cfg):
    if channel is None or not hasattr(channel, "permissions_for"):
        return False
    if channel.permissions_for(guild.default_role).view_channel:
        return False
    staff = set(cfg["moderator_roles"])
    for role in guild.roles:
        if role.id == guild.default_role.id or role.id in staff or role.managed or role.permissions.administrator:
            continue
        if channel.permissions_for(role).view_channel:
            return False
    return True


class Reporter:
    def __init__(self, bot):
        self.bot = bot
        self.last_alert = {}

    async def log(self, guild, title, description):
        if not guild:
            return False
        cfg, _ = await self.bot.db.settings(guild.id)
        channel = guild.get_channel(cfg["log_channel"])
        if not private_channel(guild, channel, cfg):
            return False
        try:
            await channel.send(embed=embed(cfg, title, description), allowed_mentions=discord.AllowedMentions.none())
            return True
        except discord.HTTPException:
            return False

    async def error(self, guild, area, exc):
        now = time.time()
        error_id = "RCC-" + datetime.now(timezone.utc).strftime("%y%m%d-") + secrets.token_hex(4).upper()
        detail = str(exc)[:800]
        token = os.getenv("DISCORD_TOKEN", "")
        if token:
            detail = detail.replace(token, "[redacted]")
        self.bot.logger.error("%s %s %s", error_id, area, detail, exc_info=(type(exc), exc, exc.__traceback__))
        try:
            await self.bot.db.execute("INSERT INTO errors VALUES(?,?,?,?,?,?)", (error_id, guild.id if guild else 0, area[:100], type(exc).__name__, detail, now))
            key = (guild.id if guild else 0, area)
            if now - self.last_alert.get(key, 0) >= 60:
                self.last_alert[key] = now
                await self.log(guild, "Ripcars Crew error", f"ID: {error_id}\nArea: {area}\nType: {type(exc).__name__}\nUse /crew health and journalctl -u ripcars-crew for details.")
        except Exception:
            self.bot.logger.exception("Error reporting failed: %s", error_id)
        return error_id


class Watchdog:
    def __init__(self):
        raw = os.getenv("NOTIFY_SOCKET", "")
        self.address = "\0" + raw[1:] if raw.startswith("@") else raw
        self.enabled = bool(self.address)

    def notify(self, value):
        if self.enabled:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
                    sock.sendto(value.encode(), self.address)
            except OSError:
                pass

    async def loop(self, bot):
        await bot.wait_until_ready()
        self.notify("READY=1")
        while not bot.is_closed():
            self.notify("WATCHDOG=1")
            # Deliberately only heartbeat event-loop liveness; health separately checks Gateway/tasks.
            await asyncio.sleep(20)


def backup(path, keep=14):
    source = Path(path)
    if not source.exists():
        raise ValueError("Database not created yet.")
    folder = source.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (source.stem + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S-%f") + ".sqlite3")
    with sqlite3.connect(str(source)) as src, sqlite3.connect(str(target)) as dst:
        src.backup(dst)
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Backup integrity check failed.")
    for old in sorted(folder.glob(source.stem + "-*.sqlite3"))[:-keep]:
        old.unlink()
    return target


async def doctor(bot, guild, cfg):
    blockers, warnings = [], []
    me = guild.me
    if me is None:
        return ["Bot membership is unavailable."], []
    missing = [name for name in REQUIRED if not getattr(me.guild_permissions, name)]
    if missing:
        warnings.append("Missing server permissions: " + ", ".join(missing))
    for flag in ("view_channel", "manage_messages", "moderate_members"):
        if not getattr(me.guild_permissions, flag):
            blockers.append("Guard needs " + flag)
    if not bot.intents.members or not bot.intents.message_content:
        blockers.append("Members and Message Content intents are required.")
    if not cfg["moderator_roles"] or any(not guild.get_role(rid) for rid in cfg["moderator_roles"]):
        blockers.append("Configure valid moderator role IDs.")
    log = guild.get_channel(cfg["log_channel"])
    if not private_channel(guild, log, cfg):
        blockers.append("Select a private staff-only log channel.")
    elif not all(getattr(log.permissions_for(me), flag) for flag in ("view_channel", "send_messages", "embed_links")):
        blockers.append("Bot cannot send embeds to the staff log.")
    if not cfg["member_role"] or not guild.get_role(cfg["member_role"]):
        blockers.append("Select the member role granted by Gate.")
    elif guild.get_role(cfg["member_role"]) >= me.top_role:
        blockers.append("Place the bot role above the member role so moderation can work.")
    text_channels = [ch for ch in guild.text_channels if not cfg["guard"]["include_channels"] or ch.id in cfg["guard"]["include_channels"] or getattr(ch, "category_id", 0) in cfg["guard"]["include_channels"]]
    coverage = sum(all(getattr(ch.permissions_for(me), flag) for flag in ("view_channel", "read_message_history", "manage_messages")) for ch in text_channels)
    if coverage < len(text_channels):
        warnings.append(f"Chat coverage: {coverage}/{len(text_channels)} text channels. Threads also need parent visibility.")
    if cfg["native"]["enabled"] and not me.guild_permissions.manage_guild:
        blockers.append("Native AutoMod sync needs Manage Server.")
    native = await bot.db.query("SELECT * FROM native WHERE guild=?", (guild.id,))
    if native:
        row = native[0]
        if row["state"] != "active":
            warnings.append("Native rule has a protected/missing state; use /crew review native after checking Discord.")
        elif json.loads(row["baseline"]).get("enabled") and (not cfg["native"]["enabled"] or not cfg["guard"]["enabled"] or cfg["guard"]["mode"] != "protect"):
            warnings.append("A native rule is still enabled in Discord. Disable it via Native Sync; settings alone do not turn it off.")
    if await bot.db.query("SELECT id FROM tickets WHERE guild=? AND status IN ('creating','closing','reopening')", (guild.id,)):
        warnings.append("Incomplete ticket operations need review in Tickets > Repair.")
    if await bot.db.query("SELECT id FROM cases WHERE guild=? AND status='pending'", (guild.id,)):
        warnings.append("Some moderation cases have an unresolved API outcome; review Cases.")
    return blockers, warnings


async def health(bot, guild):
    cfg, revision = await bot.db.settings(guild.id)
    blockers, warnings = await doctor(bot, guild, cfg)
    db_ok = coord_ok = False
    try:
        db_ok = (await bot.db.query("PRAGMA quick_check"))[0]["quick_check"] == "ok"
        coord_ok = (await bot.db.coord.query("PRAGMA quick_check"))[0]["quick_check"] == "ok"
    except Exception:
        pass
    latency = bot.latency
    lag = f"{int(latency * 1000)} ms" if math.isfinite(latency) else "not available"
    errors = await bot.db.query("SELECT * FROM errors WHERE guild=? ORDER BY at DESC LIMIT 5", (guild.id,))
    active = await bot.db.query("SELECT status,COUNT(*) AS n FROM tickets WHERE guild=? GROUP BY status", (guild.id,))
    tasks = ", ".join(f"{name}: {'FAILED' if task.done() else 'running'}" for name, task in bot.background.items()) or "not started"
    result = embed(cfg, "Ripcars Crew | Health", f"Version {bot.version} · revision {revision}\nReady: {bot.is_ready()} · Gateway: {lag}\nDatabase: {db_ok} · Coordination: {coord_ok}\nWatchdog: {bot.watchdog.enabled}\nTasks: {tasks}\nLast monitor cycle: {bot.last_monitor or 'not run'}")
    result.add_field(name="Tickets", value="\n".join(f"{r['status']}: {r['n']}" for r in active)[:1024] or "None", inline=False)
    result.add_field(name="Blockers", value="\n".join(blockers)[:1024] or "None", inline=False)
    result.add_field(name="Warnings", value="\n".join(warnings)[:1024] or "None", inline=False)
    result.add_field(name="Recent error IDs", value="\n".join(f"{r['id']} · {r['area']} · {r['type']}" for r in errors)[:1024] or "None", inline=False)
    result.add_field(name="Backups", value=str(bot.last_backup or "No backup yet")[:1024], inline=False)
    return result
