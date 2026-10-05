"""Optional Discord-side blocking; own-rule ID and baseline checks, no foreign edits."""
from __future__ import annotations

import json
import hashlib
import time

import discord


def snapshot(rule):
    return {"name": rule.name, "creator": rule.creator_id, "enabled": rule.enabled,
            "event": rule.event_type.value, "trigger": {"type": rule.trigger.type.value, "metadata": rule.trigger.to_metadata_dict()},
            "roles": sorted(rule.exempt_role_ids), "channels": sorted(rule.exempt_channel_ids),
            "actions": [action.to_dict() for action in rule.actions]}


def keywords(cfg):
    result = []
    for word in cfg["guard"]["words"]:
        text = word["text"].strip()
        if "*" in text or len(text) > 60:
            continue
        result.append("*" + text + "*" if word["mode"] == "contains" else text)
    return list(dict.fromkeys(result))


class NativeRules:
    def __init__(self, bot):
        self.bot = bot

    async def sync(self, guild, actor):
        if not guild.me.guild_permissions.manage_guild:
            raise ValueError("Native AutoMod sync requires Manage Server for the bot.")
        async with self.bot.db.lease(guild.id) as token:
            cfg, _ = await self.bot.db.settings(guild.id)
            rows = await self.bot.db.query("SELECT * FROM native WHERE guild=?", (guild.id,))
            row = rows[0] if rows else None
            rule = None
            if row:
                if row["state"] != "active":
                    raise ValueError("Native rule is protected after a manual edit/deletion. Inspect it in Discord; no automatic overwrite is allowed.")
                try:
                    rule = await guild.fetch_automod_rule(row["rule"])
                except discord.NotFound:
                    await self.bot.db.execute("UPDATE native SET state='missing' WHERE guild=?", (guild.id,))
                    raise ValueError("The registered native rule was deleted. It will not be silently recreated.")
                if rule.creator_id != self.bot.user.id or snapshot(rule) != json.loads(row["baseline"]):
                    await self.bot.db.execute("UPDATE native SET state='manual' WHERE guild=?", (guild.id,))
                    raise ValueError("External native-rule edit preserved. Review the rule in Discord.")
            words = keywords(cfg)
            enabled = bool(cfg["native"]["enabled"] and cfg["guard"]["enabled"] and cfg["guard"]["mode"] == "protect" and cfg["guard"]["filters"]["words"]["enabled"] and cfg["guard"]["filters"]["words"]["action"] != "alert" and words)
            if not rule and not enabled:
                return "Native rule is off; nothing created."
            roles = sorted(set(cfg["moderator_roles"] + cfg["guard"]["exempt_roles"]))
            channels = cfg["guard"]["exempt_channels"]
            if cfg["guard"]["include_channels"]:
                raise ValueError("Native word sync cannot represent include-only channel scope safely. Use bot-only rules or clear include_channels before enabling native blocking.")
            if len(roles) > 20 or len(channels) > 50:
                raise ValueError("Native limits: 20 exempt roles and 50 exempt channels.")
            options = dict(name="Rip Cars | Crew word filter", event_type=discord.AutoModRuleEventType.message_send,
                           trigger=discord.AutoModTrigger(keyword_filter=words or ["ripcars-unused-native-rule-placeholder"]),
                           actions=[discord.AutoModRuleAction(type=discord.AutoModRuleActionType.block_message, custom_message=cfg["texts"]["native_block"][:150])],
                           enabled=enabled, exempt_roles=[discord.Object(id=v) for v in roles],
                           exempt_channels=[discord.Object(id=v) for v in channels], reason="Ripcars Crew: confirmed native word sync")
            await self.bot.db.ensure_lease(guild.id,'server-setup',token)
            if rule:
                rule = await rule.edit(**options)
            else:
                rule = await guild.create_automod_rule(**options)
            await self.bot.db.execute("INSERT INTO native VALUES(?,?,?,'active') ON CONFLICT(guild) DO UPDATE SET rule=excluded.rule,baseline=excluded.baseline,state='active'", (guild.id, rule.id, json.dumps(snapshot(rule))))
            await self.bot.db.audit(guild.id, actor, "native_sync", f"rule={rule.id}; enabled={enabled}; words={len(words)}")
            return f"Native rule {rule.id}: {'enabled' if enabled else 'disabled'}; {len(words)} mirrored word rules."

    async def action(self, event):
        rows = await self.bot.db.query("SELECT * FROM native WHERE guild=? AND rule=?", (event.guild_id, event.rule_id))
        if not rows or event.action.type != discord.AutoModRuleActionType.block_message:
            return
        identity = str(event.message_id) if event.message_id else hashlib.sha256((event.matched_content or "").encode()).hexdigest()[:16] + f":{int(time.time() // 60)}"
        key = f"native:{event.rule_id}:{event.user_id}:{identity}"
        case_id, new = await self.bot.db.case(event.guild_id, event.user_id, self.bot.user.id, "native_block", "Discord native word rule blocked a message", source="native", event_key=key)
        if new:
            guild = self.bot.get_guild(event.guild_id)
            await self.bot.reporter.log(guild, "Native word block", f"Case #{case_id}; member {event.user_id}; channel {event.channel_id}. No automatic ban.")
