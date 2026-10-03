from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
import weakref
from collections import deque
from pathlib import Path

import discord
from discord.ext import commands

from . import __version__, operations
from .coordination import TemporaryControls
from .moderation import Moderation
from .native import NativeRules
from .storage import Conflict, Database
from .tickets import Tickets
from .ui_common import reply


class CrewBot(commands.Bot):
    def __init__(self, db_path=None, coordination_path=None, *, sync=True):
        intents = discord.Intents.default()
        intents.members = intents.message_content = True
        intents.moderation = True
        intents.auto_moderation_configuration = intents.auto_moderation_execution = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.db = Database(db_path or os.getenv("DB_PATH", "data/crew.sqlite3"), coordination_path or os.getenv("COORDINATION_PATH", "data/coordination.sqlite3"))
        self.version = __version__
        self.logger = logging.getLogger("ripcarscrew")
        self.reporter = operations.Reporter(self)
        self.watchdog = operations.Watchdog()
        self.moderation = Moderation(self)
        self.tickets = Tickets(self)
        self.controls = TemporaryControls(self)
        self.native = NativeRules(self)
        self.background = {}
        self.last_monitor = None
        self.last_backup = None
        self.started = time.monotonic()
        self._member_locks = weakref.WeakValueDictionary()
        self._joins = {}
        self.sync_enabled = sync

    def member_lock(self, guild, user):
        key = (guild, user)
        lock = self._member_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._member_locks[key] = lock
        return lock

    async def setup_hook(self):
        await self.db.open()
        await self.load_extension("ripcars_crew.commands")
        from .ticket_ui import TicketControls, TicketPanel
        self.add_view(TicketPanel(self))
        self.add_view(TicketControls(self))
        self.tree.on_error = self.tree_error
        if self.sync_enabled:
            guild_id = os.getenv("GUILD_ID", "").strip()
            if guild_id:
                guild = discord.Object(id=int(guild_id))
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            else:
                await self.tree.sync()
        self.background = {
            "monitor": asyncio.create_task(self.monitor_loop(), name="crew-monitor"),
            "backups": asyncio.create_task(self.backup_loop(), name="crew-backups"),
            "watchdog": asyncio.create_task(self.watchdog.loop(self), name="crew-watchdog"),
        }

    async def report(self, guild, area, exc):
        return await self.reporter.error(guild, area, exc)

    async def on_ready(self):
        self.logger.info("online as %s; version %s; servers %s", self.user, self.version, len(self.guilds))

    async def on_message(self, message):
        if message.guild and not message.author.bot:
            await self.moderation.activity(message)
        await self.moderation.process(message)

    async def on_message_edit(self, before, after):
        if before.content != after.content or before.attachments != after.attachments:
            await self.moderation.process(after, edited=True)

    async def on_raw_message_edit(self, payload):
        if payload.cached_message or not payload.guild_id or "content" not in payload.data:
            return
        channel = self.get_channel(payload.channel_id)
        if channel and hasattr(channel, "fetch_message"):
            try:
                message = await channel.fetch_message(payload.message_id)
            except (discord.NotFound, discord.Forbidden):
                return
            await self.moderation.process(message, edited=True)

    async def on_raw_message_delete(self, payload):
        if payload.guild_id:
            await self.db.execute("DELETE FROM previews WHERE guild=? AND message=?", (payload.guild_id, payload.message_id))

    async def on_raw_bulk_message_delete(self, payload):
        if payload.guild_id:
            for message_id in payload.message_ids:
                await self.db.execute("DELETE FROM previews WHERE guild=? AND message=?", (payload.guild_id, message_id))

    async def on_automod_action(self, event):
        await self.native.action(event)

    async def on_member_join(self, member):
        cfg, _ = await self.db.settings(member.guild.id)
        monitor = cfg["monitoring"]
        now = time.time()
        queue = self._joins.setdefault(member.guild.id, deque(maxlen=101))
        queue.append(now)
        while queue and queue[0] < now - monitor["raid_window_seconds"]:
            queue.popleft()
        age = now - member.created_at.timestamp()
        if len(queue) >= monitor["raid_join_count"]:
            try:
                await self.db.cooldown(member.guild.id, 0, "raid-alert", 60)
            except ValueError:
                pass
            else:
                await self.reporter.log(member.guild, "Join burst detected", f"{len(queue)} recent joins in {monitor['raid_window_seconds']}s. Review Gate and chat protection. No automatic kick/ban/lockdown.")
        if age < monitor["new_account_hours"] * 3600:
            await self.db.audit(member.guild.id, member.id, "new_account", f"age_seconds={int(age)}; monitor only")

    async def audit_event(self, guild, area, actor, detail):
        cfg, _ = await self.db.settings(guild.id)
        if cfg["monitoring"]["audit_events"]:
            await self.db.audit(guild.id, actor, area, detail)

    async def on_audit_log_entry_create(self, entry):
        await self.audit_event(entry.guild, "discord_audit", getattr(entry, "user_id", None), f"action={entry.action}; target={getattr(entry.target, 'id', None)}")

    async def on_guild_channel_delete(self, channel):
        await self.audit_event(channel.guild, "channel_deleted", None, f"channel={channel.id}; no auto recreation")

    async def on_guild_channel_create(self, channel):
        await self.audit_event(channel.guild, "channel_created", None, f"channel={channel.id}; no auto adoption")

    async def on_guild_channel_update(self, before, after):
        await self.audit_event(after.guild, "channel_changed", None, f"channel={after.id}; not automatically reverted")

    async def on_guild_role_delete(self, role):
        await self.audit_event(role.guild, "role_deleted", None, f"role={role.id}; no auto recreation")

    async def on_guild_role_create(self, role):
        await self.audit_event(role.guild, "role_created", None, f"role={role.id}; no auto adoption")

    async def on_guild_role_update(self, before, after):
        await self.audit_event(after.guild, "role_changed", None, f"role={after.id}; not automatically reverted")

    async def on_webhooks_update(self, channel):
        await self.audit_event(channel.guild, "webhooks_changed", None, f"channel={channel.id}; webhook contents and tokens not fetched")

    async def monitor_cycle(self, guild):
        cfg, _ = await self.db.settings(guild.id)
        await self.db.prune(guild.id, cfg["monitoring"])
        await self.controls.restore_due(guild)
        await self.tickets.sweep(guild)
        native = await self.db.query("SELECT * FROM native WHERE guild=? AND state='active'", (guild.id,))
        if native:
            from .native import snapshot
            import json
            row = native[0]
            try:
                rule = await guild.fetch_automod_rule(row["rule"])
                if snapshot(rule) != json.loads(row["baseline"]):
                    await self.db.execute("UPDATE native SET state='manual' WHERE guild=?", (guild.id,))
                    await self.reporter.log(guild, "Native rule edit preserved", "An admin or another bot changed the registered rule. Automatic sync is paused; inspect it in Discord.")
            except discord.NotFound:
                await self.db.execute("UPDATE native SET state='missing' WHERE guild=?", (guild.id,))
                await self.reporter.log(guild, "Native rule is missing", "The rule was deleted; it was not recreated.")

    async def monitor_loop(self):
        await self.wait_until_ready()
        while not self.is_closed():
            for guild in self.guilds:
                try:
                    await self.monitor_cycle(guild)
                except asyncio.CancelledError:
                    raise
                except Conflict:
                    continue
                except Exception as exc:
                    await self.report(guild, "monitor", exc)
            self.last_monitor = int(time.time())
            await asyncio.sleep(60)

    async def take_backup(self):
        target = await asyncio.to_thread(operations.backup, self.db.path)
        self.last_backup = target.name
        self.logger.info("Backup integrity verified: %s", target)

    async def backup_loop(self):
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                await self.take_backup()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.report(None, "backup", exc)
            await asyncio.sleep(86400)

    async def tree_error(self, i, error):
        original = getattr(error, "original", error)
        if isinstance(original, ValueError):
            await reply(i, str(original)[:1800])
        elif isinstance(error, discord.app_commands.CheckFailure):
            await reply(i, "This command is unavailable to your role.")
        else:
            error_id = await self.report(i.guild, "command", original)
            await reply(i, f"Something went wrong. Error ID: {error_id}. Use /crew health.")

    async def on_error(self, event, *args, **kwargs):
        exc = sys.exc_info()[1]
        guild = next((getattr(arg, "guild", None) for arg in args if getattr(arg, "guild", None)), None)
        if exc:
            await self.report(guild, event, exc)

    async def close(self):
        self.watchdog.notify("STOPPING=1")
        for task in self.background.values():
            task.cancel()
        if self.background:
            await asyncio.gather(*self.background.values(), return_exceptions=True)
        await self.db.close()
        await super().close()


def load_env(path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


async def amain():
    load_env(Path(".env"))
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token or token.startswith("replace") or any(char.isspace() for char in token):
        raise SystemExit("Set a valid DISCORD_TOKEN in the dedicated environment file.")
    async with CrewBot() as bot:
        await bot.start(token)


def main():
    os.umask(0o007)  # shared coordination WAL/SHM remain group writable; private DB dir is 0700.
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
