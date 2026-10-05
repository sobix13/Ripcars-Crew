"""Explicit ownership and temporary chat controls; never rename unknown objects."""
from __future__ import annotations

import json
import time

import discord

from . import config
from .operations import private_channel
from .storage import Conflict


def owner(bot):
    return f"bot:{bot.user.id}"


async def import_gate(bot, guild, actor):
    async with bot.db.lease(guild.id):
        resources = await bot.db.resources(guild.id)
        cfg, revision = await bot.db.settings(guild.id)
        for key, path in (("role:rippers", "member_role"), ("channel:gate_log", "log_channel"),
                          ("channel:ticket", "tickets.panel_channel"), ("channel:open_tickets", "tickets.open_category"),
                          ("channel:closed_tickets", "tickets.closed_category")):
            row = resources.get(key)
            if row:
                if row['state'] not in ('active','pinned','external'):raise ValueError('Gate resource needs review before import: '+key)
                cfg = config.set_path(cfg, path, row["object_id"])
        mod = resources.get("role:moderator")
        if mod:
            cfg["moderator_roles"] = [mod["object_id"]]
        await bot.db.save(guild.id, cfg, revision, actor)
    return cfg


async def check_ticket_scope(bot, guild, cfg):
    resources = await bot.db.resources(guild.id)
    for path in ("panel_channel", "open_category", "closed_category"):
        channel_id = cfg["tickets"][path]
        row = next((r for r in resources.values() if r["object_id"] == channel_id), None)
        if row and row["owner"] != owner(bot):
            raise ValueError(f"{path} belongs to another controller. In Gate, activate this bot's support integration and confirm ticket responsibility handoff first.")
        if row and row["state"] not in ("active", "external", "pinned"):
            raise ValueError(f"{path} has a protected/manual state. Review it before proceeding.")
    for path in ("open_category", "closed_category"):
        category = guild.get_channel(cfg["tickets"][path])
        if not isinstance(category, discord.CategoryChannel):
            raise ValueError(f"Select a valid {path} category.")
        if not private_channel(guild, category, cfg):
            raise ValueError(f"{path} must be staff-only. Members get access only to their own open ticket.")
    panel = guild.get_channel(cfg["tickets"]["panel_channel"])
    if panel is None or not hasattr(panel, "send"):
        raise ValueError("Select an available ticket panel text channel.")
    if not guild.me.guild_permissions.manage_channels or not guild.me.guild_permissions.manage_roles:
        raise ValueError("Tickets need Manage Channels and Manage Roles for permission overwrites.")


def staff_overwrites(guild, cfg):
    overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False, read_message_history=False),
                  guild.me: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True, embed_links=True, attach_files=True, manage_messages=True)}
    for role_id in cfg["moderator_roles"]:
        role = guild.get_role(role_id)
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True, attach_files=True, embed_links=True, add_reactions=True, manage_messages=True)
    return overwrites


async def standalone_setup(bot, guild, actor):
    """Create private ticket containers only, after an explicit admin confirmation."""
    async with bot.db.lease(guild.id) as token:
        cfg, revision = await bot.db.settings(guild.id)
        if not cfg["moderator_roles"]:
            raise ValueError("Configure moderator role IDs before creating ticket containers.")
        resources = await bot.db.resources(guild.id)
        overwrites = staff_overwrites(guild, cfg)
        for key, name, field in (("crew:open", "Open", "open_category"), ("crew:closed", "Closed", "closed_category")):
            row = resources.get(key)
            if row:
                existing = guild.get_channel(row["object_id"])
                if not existing:
                    raise ValueError("A registered category is missing. Bind a reviewed replacement; do not silently recreate it.")
                cfg["tickets"][field] = existing.id
                continue
            if cfg["tickets"][field]:
                raise ValueError("Categories are already bound. Use Gate handoff or explicit bindings, not standalone creation.")
            if any(ch.name == name for ch in guild.categories):
                raise ValueError(f"A category named {name} already exists. Bind its ID explicitly instead of creating a duplicate.")
            await bot.db.renew(guild.id, "server-setup", token)
            channel = await guild.create_category(name, overwrites=overwrites, reason="Ripcars Crew: confirmed ticket container setup")
            await bot.db.coord.execute("INSERT INTO resources VALUES(?,?,?,?,?,?,?,'active')", (guild.id, key, channel.id, "category", owner(bot), "{}", "{}"))
            cfg["tickets"][field] = channel.id
        await bot.db.save(guild.id, cfg, revision, actor)
    return cfg


class TemporaryControls:
    def __init__(self, bot):
        self.bot = bot

    async def slowmode(self, guild, channel, seconds, lifetime, actor):
        if not 0 <= seconds <= 21600 or not 30 <= lifetime <= 86400:
            raise ValueError("Channel slowmode: 0–21600 seconds; duration: 30–86400 seconds.")
        async with self.bot.db.lease(guild.id) as token:
            if await self.bot.db.query("SELECT * FROM temporary WHERE guild=? AND channel=? AND kind='slowmode' AND state!='done'", (guild.id, channel.id)):
                raise ValueError("This channel already has a temporary control or unresolved restoration.")
            resources = await self.bot.db.resources(guild.id)
            row = next((r for r in resources.values() if r["object_id"] == channel.id), None)
            original = {"delay": channel.slowmode_delay, "resource": row}
            # Gate ignores non-active states. Preserve its ownership, baseline and desired exactly.
            if row:
                if row["state"] not in ("active", "pinned", "external"):
                    raise ValueError("This resource is protected by a manual-change review.")
                await self.bot.db.coord.execute("UPDATE resources SET state='crew-temporary' WHERE guild=? AND key=?", (guild.id, row["key"]))
            await self.bot.db.execute("INSERT OR REPLACE INTO temporary VALUES(?,?,'slowmode',?,?,?,'pending')", (guild.id, channel.id, json.dumps(original), json.dumps({"delay": seconds}), time.time() + lifetime))
            try:
                await self.bot.db.ensure_lease(guild.id,'server-setup',token)
                await channel.edit(slowmode_delay=seconds, reason="Ripcars Crew: confirmed temporary channel slowmode")
            except Exception:
                # Outcome may be ambiguous: never declare restored without checking Discord.
                await self.bot.db.execute("UPDATE temporary SET state='review' WHERE guild=? AND channel=? AND kind='slowmode'", (guild.id, channel.id))
                raise
            await self.bot.db.execute("UPDATE temporary SET state='active' WHERE guild=? AND channel=? AND kind='slowmode'", (guild.id, channel.id))
            await self.bot.db.audit(guild.id, actor, "temporary_slowmode", f"channel={channel.id}; seconds={seconds}; duration={lifetime}")

    async def restore_due(self, guild):
        due = await self.bot.db.query("SELECT * FROM temporary WHERE guild=? AND expires<=? AND state IN ('active','pending')", (guild.id, time.time()))
        for item in due:
            try:
                async with self.bot.db.lease(guild.id) as token:
                    channel = guild.get_channel(item["channel"])
                    original, applied = json.loads(item["original"]), json.loads(item["applied"])
                    row = original["resource"]
                    current = (await self.bot.db.resources(guild.id)).get(row["key"]) if row else None
                    safe_registry = row is None or (current and current["state"] == "crew-temporary" and current["owner"] == row["owner"] and current["object_id"] == row["object_id"] and current["baseline"] == row["baseline"] and current["desired"] == row["desired"])
                    if not channel or channel.slowmode_delay not in (applied["delay"], original["delay"]) or not safe_registry:
                        await self.bot.db.execute("UPDATE temporary SET state='review' WHERE guild=? AND channel=? AND kind='slowmode'", (guild.id, item["channel"]))
                        if current and current["state"] == "crew-temporary":
                            await self.bot.db.coord.execute("UPDATE resources SET state='manual' WHERE guild=? AND key=?", (guild.id, row["key"]))
                        await self.bot.reporter.log(guild, "Temporary control needs review", f"Channel {item['channel']}: an external edit or ownership change was preserved.")
                        continue
                    if channel.slowmode_delay == applied["delay"]:
                        await self.bot.db.ensure_lease(guild.id,'server-setup',token)
                        await channel.edit(slowmode_delay=original["delay"], reason="Ripcars Crew: temporary slowmode expired")
                    if current:
                        await self.bot.db.coord.execute("UPDATE resources SET state=? WHERE guild=? AND key=?", (row["state"], guild.id, row["key"]))
                    await self.bot.db.execute("UPDATE temporary SET state='done' WHERE guild=? AND channel=? AND kind='slowmode'", (guild.id, item["channel"]))
            except Conflict:
                continue

    async def member_cooldown(self, guild, user, channel, seconds, actor):
        if not 1 <= seconds <= 86400:
            raise ValueError("Member channel cooldown: 1–86400 seconds.")
        await self.bot.db.execute("INSERT INTO cooldowns VALUES(?,?,?,?,?) ON CONFLICT(guild,user,key) DO UPDATE SET at=excluded.at,delay=excluded.delay", (guild.id, user.id, f"chat:{channel.id}", time.time(), seconds))
        await self.bot.db.audit(guild.id, actor, "member_chat_cooldown", f"user={user.id}; channel={channel.id}; seconds={seconds}")
