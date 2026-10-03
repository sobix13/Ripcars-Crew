from __future__ import annotations

import hashlib
import time
from datetime import timedelta

import discord

from .detection import BurstTracker, Hit, inspect, redact
from .ui_common import can_target


ACTION_PERMS = {"ban": "ban_members", "kick": "kick_members", "timeout": "moderate_members", "untimeout": "moderate_members", "warn": "manage_messages"}


class Moderation:
    def __init__(self, bot):
        self.bot = bot
        self.burst = BurstTracker()

    async def manual(self, guild, actor, target, action, reason, seconds=600, delete_seconds=0):
        if action not in ACTION_PERMS:
            raise ValueError("Unknown moderation action.")
        can_target(guild, actor, target, ACTION_PERMS[action])
        if not 0 <= delete_seconds <= 604800 or not 1 <= seconds <= 2419200:
            raise ValueError("Ban cleanup max 7 days; timeout max 28 days.")
        if not reason.strip() or len(reason) > 1000:
            raise ValueError("Add a reason of 1–1000 characters.")
        case_id, _ = await self.bot.db.case(guild.id, target.id, actor.id, action, reason, "pending", seconds if action == "timeout" else 0)
        try:
            audit_reason = f"Ripcars Crew case {case_id}; actor {actor.id}: {reason}"[:500]
            if action == "ban":
                await guild.ban(target, reason=audit_reason, delete_message_seconds=delete_seconds)
            elif action == "kick":
                await target.kick(reason=audit_reason)
            elif action == "timeout":
                await target.timeout(timedelta(seconds=seconds), reason=audit_reason)
            elif action == "untimeout":
                await target.timeout(None, reason=audit_reason)
        except Exception as exc:
            await self.bot.db.execute("UPDATE cases SET status='failed',result=? WHERE id=?", (type(exc).__name__, case_id))
            raise
        await self.bot.db.execute("UPDATE cases SET status='done' WHERE id=?", (case_id,))
        cfg, _ = await self.bot.db.settings(guild.id)
        if action == "warn":
            await self.notice(target, f"Rip Cars warning · case {case_id}\nReason: {reason}\n{cfg['texts']['warning']}")
        await self.bot.reporter.log(guild, "Moderation case", f"#{case_id} · {action} · member {target.id}\nActor: {actor.id}\n{reason}")
        return case_id

    async def notice(self, user, text):
        try:
            await user.send(text[:1900], allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass

    async def process(self, message, *, edited=False):
        if not message.guild or message.author.id == self.bot.user.id:
            return []
        guild, user = message.guild, message.author
        cfg, _ = await self.bot.db.settings(guild.id)
        guard = cfg["guard"]
        if not guard["enabled"]:
            return []
        webhook = bool(message.webhook_id)
        if webhook and not guard["scan_webhooks"]:
            return []
        if not webhook and user.bot and (not guard["scan_bots"] or user.id in guard["trusted_bot_ids"]):
            return []
        role_ids = {r.id for r in getattr(user, "roles", [])}
        perms = getattr(user, "guild_permissions", discord.Permissions.none())
        staff = bool(user.id == guild.owner_id or perms.administrator or set(cfg["moderator_roles"]) & role_ids)
        if staff or role_ids & set(guard["exempt_roles"]):
            return []
        channel = message.channel
        channels = {channel.id, getattr(channel, "parent_id", 0), getattr(channel, "category_id", 0)}
        parent = getattr(channel, "parent", None)
        if parent:
            channels.add(getattr(parent, "category_id", 0))
        if channels & set(guard["exempt_channels"]) or (guard["include_channels"] and not channels & set(guard["include_channels"])):
            return []
        async with self.bot.member_lock(guild.id, user.id):
            hits = inspect(message.content, guard, mentions=len(message.mentions) + len(message.role_mentions), attachments=message.attachments, display_name=user.display_name, is_staff=staff)
            if not edited and not webhook and not user.bot:
                hits.extend(self.burst.record(guild.id, user.id, message.content, guard))
            rows = await self.bot.db.query("SELECT * FROM cooldowns WHERE guild=? AND user=? AND key=?", (guild.id, user.id, f"chat:{channel.id}"))
            if rows and rows[0]["at"] + rows[0]["delay"] > time.time() and guard["filters"]["member_cooldown"]["enabled"]:
                hits.append(Hit("member_cooldown", "Member-specific chat cooldown"))
            if not hits:
                return []
            event = f"message:{message.id}:" + hashlib.sha256((message.content + ":" + ",".join(str(a.id) for a in message.attachments)).encode()).hexdigest()[:24]
            actions = [guard["filters"][hit.rule]["action"] for hit in hits]
            action = "observe" if guard["mode"] == "observe" else max(actions, key={"alert": 0, "delete": 1, "warn": 2, "timeout": 3}.get)
            reason = "; ".join(f"{hit.rule}: {hit.reason}" for hit in hits)
            case_id, new = await self.bot.db.case(guild.id, user.id, self.bot.user.id, "violation" if action == "timeout" else action, reason, source="automod", event_key=event)
            if not new:
                return hits
            results = []
            if action in ("delete", "warn", "timeout"):
                try:
                    await message.delete()
                    results.append("deleted")
                except discord.NotFound:
                    results.append("already deleted")
                except Exception as exc:
                    results.append("delete failed")
                    await self.bot.report(guild, "automod deletion", exc)
            heat = 0
            if action in ("warn", "timeout"):
                heat = await self.bot.db.add_heat(guild.id, user.id, sum(guard["filters"][hit.rule]["weight"] for hit in hits), guard["heat_decay_per_minute"])
            warnings = await self.bot.db.warning_count(guild.id, user.id, guard["warn_expiry_days"])
            should_timeout = action == "timeout" or (action == "warn" and ((guard["timeout_after"] and warnings >= guard["timeout_after"]) or heat >= guard["heat_threshold"]))
            if should_timeout and not webhook and not user.bot:
                try:
                    can_target(guild, None, user, "moderate_members")
                    timeout_id, _ = await self.bot.db.case(guild.id, user.id, self.bot.user.id, "timeout", f"Escalation from case {case_id}", "pending", guard["timeout_seconds"], "automod", event + ":timeout")
                    try:
                        await user.timeout(timedelta(seconds=guard["timeout_seconds"]), reason=f"Ripcars Crew case {timeout_id}")
                    except Exception:
                        await self.bot.db.execute("UPDATE cases SET status='failed' WHERE id=?", (timeout_id,))
                        raise
                    await self.bot.db.execute("UPDATE cases SET status='done' WHERE id=?", (timeout_id,))
                    await self.bot.db.execute("UPDATE heat SET value=0 WHERE guild=? AND user=?", (guild.id, user.id))
                    results.append("timed out")
                except Exception as exc:
                    results.append("timeout failed/protected target")
                    await self.bot.report(guild, "automod timeout", exc)
            if action in ("warn", "timeout") and not webhook and not user.bot:
                try:
                    await self.bot.db.cooldown(guild.id, user.id, "notice", guard["notice_seconds"])
                except ValueError:
                    pass
                else:
                    await self.notice(user, f"Rip Cars moderation notice · case {case_id}\n{cfg['texts']['warning']}")
            await self.bot.db.execute("UPDATE cases SET result=? WHERE id=?", (", ".join(results), case_id))
            await self.bot.reporter.log(guild, "Chat moderation", f"Case #{case_id} · {action} · member {user.id} · channel {channel.id}\n{reason}\nResult: {', '.join(results) or 'review only'}")
            return hits

    async def activity(self, message):
        if not message.guild or message.author.bot:
            return
        cfg, _ = await self.bot.db.settings(message.guild.id)
        await self.bot.db.activity_message(message.guild.id, message.author.id, message.channel.id, message.id, redact(message.content), cfg["monitoring"])
