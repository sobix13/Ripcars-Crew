from __future__ import annotations

import io
import json
import re
import time
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from . import admin_ui, config, operations
from .detection import redact
from .ui_common import Confirm, can_target, embed, reply, require


def duration(value):
    match = re.fullmatch(r"(\d{1,8})([smhd])", value.strip().lower())
    if not match:
        raise ValueError("Duration examples: 30s, 10m, 6h, 3d.")
    seconds = int(match[1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match[2]]
    if not 1 <= seconds <= 2419200:
        raise ValueError("Duration must be between 1 second and 28 days.")
    return seconds


async def cleanup(guild, member_id, seconds, reason):
    results = {"deleted": 0, "checked": 0, "skipped": 0, "failed": 0, "capped": 0}
    if seconds == 0:
        return results
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    for channel in guild.text_channels[:50]:
        perms = channel.permissions_for(guild.me)
        if not (perms.view_channel and perms.read_message_history and perms.manage_messages):
            results["skipped"] += 1
            continue
        results["checked"] += 1
        try:
            deleted = await channel.purge(limit=100, after=cutoff, check=lambda m: m.author.id == member_id, reason=reason, bulk=True)
            results["deleted"] += len(deleted)
            results["capped"] += 1
        except discord.HTTPException:
            results["failed"] += 1
    results["skipped"] += max(0, len(guild.text_channels) - 50)
    return results


class CrewCommands(commands.Cog):
    crew = app_commands.Group(name="crew", description="Rip Cars moderation, support, and operations", guild_only=True)
    review = app_commands.Group(name="review", description="Resolve protected or ambiguous states after human review", parent=crew)

    def __init__(self, bot):
        self.bot = bot
        self.report_menu = app_commands.ContextMenu(name="Report to Rip Cars Crew", callback=self.report_context)
        self.bot.tree.add_command(self.report_menu)

    async def cog_unload(self):
        self.bot.tree.remove_command(self.report_menu.name, type=self.report_menu.type)

    async def report_context(self, i: discord.Interaction, message: discord.Message):
        if not i.guild or message.guild.id != i.guild_id or not message.channel.permissions_for(i.user).view_channel:
            raise ValueError("You can only report messages you can see in this server.")
        cfg, _ = await self.bot.db.settings(i.guild_id)
        if cfg["member_role"] not in {r.id for r in i.user.roles}:
            raise ValueError("Complete verification before reporting a message.")
        await self.bot.db.cooldown(i.guild_id, i.user.id, "member-report", 60)
        case_id, _ = await self.bot.db.case(i.guild_id, message.author.id, i.user.id, "member_report", f"channel={message.channel.id}; message={message.id}; human review requested", source="report")
        await self.bot.reporter.log(i.guild, "Member report", f"Case #{case_id}; reporter {i.user.id}; author {message.author.id}\nMessage: https://discord.com/channels/{i.guild_id}/{message.channel.id}/{message.id}\nNo automatic punishment.")
        await reply(i, f"Report #{case_id} saved for the crew. Reporting does not automatically punish the member.")

    @crew.command(name="panel", description="Open the private, branch-first admin center")
    async def panel(self, i: discord.Interaction):
        if await require(self.bot, i, True):
            cfg, _ = await self.bot.db.settings(i.guild_id)
            await reply(i, embed=embed(cfg, "Ripcars Crew | Admin center", "Choose a section. Changes stay separate from Gate's CAPTCHA and role claims."), view=admin_ui.Hub(self.bot, i.user.id))

    @crew.command(name="setup", description="Select staff, member role, logs, and ticket containers")
    async def setup_command(self, i: discord.Interaction):
        if await require(self.bot, i, True):
            cfg, revision = await self.bot.db.settings(i.guild_id)
            await reply(i, embed=admin_ui.section_embed(cfg, "setup"), view=admin_ui.SetupWizard(self.bot, i.user.id, cfg, revision))

    @crew.command(name="health", description="Check database, Gateway, tasks, backups, and errors")
    async def health_command(self, i: discord.Interaction):
        if await require(self.bot, i):
            await i.response.defer(ephemeral=True)
            await reply(i, embed=await operations.health(self.bot, i.guild))

    @crew.command(name="doctor", description="Report permissions, hierarchy, channel coverage, and setup blockers")
    async def doctor_command(self, i: discord.Interaction):
        if await require(self.bot, i):
            cfg, _ = await self.bot.db.settings(i.guild_id)
            blockers, warnings = await operations.doctor(self.bot, i.guild, cfg)
            url = f"https://discord.com/oauth2/authorize?client_id={self.bot.user.id}&scope=bot%20applications.commands&permissions={operations.permissions().value}&guild_id={i.guild_id}"
            await reply(i, "Blockers:\n" + ("\n".join(blockers) or "None") + "\n\nWarnings:\n" + ("\n".join(warnings) or "None") + f"\n\nPermission request (no Administrator): {url}\nNative AutoMod additionally requires Manage Server. Enable Members and Message Content intents in the Developer Portal.")

    @crew.command(name="guide", description="Read the short operating guide")
    async def guide(self, i: discord.Interaction):
        cfg, _ = await self.bot.db.settings(i.guild_id)
        # Public guide deliberately exposes no IDs, settings, or member information.
        from .ui_common import allowed
        staff = await allowed(self.bot, i)
        text = admin_ui.GUIDE if staff else cfg["texts"]["safety"] + "\nComplete verification, then open a ticket from the support panel if you need help."
        await reply(i, embed=embed(cfg, "Rip Cars | Guide", text))

    @crew.command(name="member", description="Privately review observed activity and moderation history")
    async def member(self, i: discord.Interaction, user: discord.Member):
        if not await require(self.bot, i):
            return
        cfg, _ = await self.bot.db.settings(i.guild_id)
        day = int(time.time() // 86400)
        rows = await self.bot.db.query("SELECT COALESCE(SUM(CASE WHEN day>=? THEN count ELSE 0 END),0) AS d7,COALESCE(SUM(count),0) AS d30,MAX(last) AS last FROM activity WHERE guild=? AND user=? AND day>=?", (day - 6, i.guild_id, user.id, day - 29))
        cases = await self.bot.db.query("SELECT * FROM cases WHERE guild=? AND user=? ORDER BY id DESC LIMIT 8", (i.guild_id, user.id))
        started = (await self.bot.db.query("SELECT started FROM crew_settings WHERE guild=?", (i.guild_id,)))[0]["started"]
        counts = rows[0]
        card = embed(cfg, "Member review", f"Member {user.id}\nAccount created: {user.created_at}\nJoined: {user.joined_at}\nObserved messages: 7d {counts['d7']}; 30d {counts['d30']}\nTracking began: <t:{int(started)}:f>\nNo backfill of full chat history.")
        card.add_field(name="Cases", value="\n".join(f"#{r['id']} · {r['action']} · {r['status']} · {'revoked' if r['revoked'] else 'active'}" for r in cases)[:1024] or "None", inline=False)
        if cfg["monitoring"]["store_previews"]:
            previews = await self.bot.db.query("SELECT * FROM previews WHERE guild=? AND user=? ORDER BY at DESC LIMIT ?", (i.guild_id, user.id, cfg["monitoring"]["preview_count"]))
            visible = [r for r in previews if i.guild.get_channel(r["channel"]) and i.guild.get_channel(r["channel"]).permissions_for(i.user).view_channel]
            card.add_field(name="Recent previews", value="\n".join(redact(r["text"]) for r in visible)[:1024] or "None visible", inline=False)
        await reply(i, embed=card)

    async def moderate(self, i, user, action, reason, seconds=600, delete_seconds=0):
        if not await require(self.bot, i):
            return
        can_target(i.guild, i.user, user, {"warn": "manage_messages", "kick": "kick_members", "ban": "ban_members", "timeout": "moderate_members", "untimeout": "moderate_members"}[action])
        async def execute(j):
            if not await require(self.bot, j):
                return
            target = j.guild.get_member(user.id)
            if not target:
                raise ValueError("Member is no longer available. Reopen the action.")
            can_target(j.guild, j.user, target, {"warn": "manage_messages", "kick": "kick_members", "ban": "ban_members", "timeout": "moderate_members", "untimeout": "moderate_members"}[action])
            extra = ""
            if action == "kick" and delete_seconds:
                if not j.user.guild_permissions.manage_messages or not j.guild.me.guild_permissions.manage_messages:
                    raise ValueError("Kick cleanup requires Manage Messages for both moderator and bot.")
                results = await cleanup(j.guild, target.id, delete_seconds, f"Ripcars Crew kick cleanup by {j.user.id}")
                extra = f"\nBounded cleanup: {results}. History may remain beyond scan limits."
            case_id = await self.bot.moderation.manual(j.guild, j.user, target, action, reason, seconds, delete_seconds if action == "ban" else 0)
            await reply(j, f"Case #{case_id}: {action} completed.{extra}")
        await reply(i, f"Confirm {action} for member {user.id}?\nReason: {reason}\nTimeout duration: {seconds if action == 'timeout' else 0}s\nCleanup window: {delete_seconds}s", view=Confirm(self.bot, i.user.id, execute))

    @crew.command(name="warn", description="Record and privately notify a warning after confirmation")
    async def warn(self, i: discord.Interaction, user: discord.Member, reason: str):
        await self.moderate(i, user, "warn", reason)

    @crew.command(name="revoke", description="Revoke an active warning by case ID; retain its audit trail")
    async def revoke(self, i: discord.Interaction, case_id: int):
        if not await require(self.bot, i, True):
            return
        async def action(j):
            await self.bot.db.revoke(j.guild_id, case_id, j.user.id)
            await reply(j, f"Warning case #{case_id} revoked. It no longer counts toward escalation.")
        await reply(i, f"Revoke warning #{case_id}?", view=Confirm(self.bot, i.user.id, action, admin=True))

    @crew.command(name="cases", description="Review recent cases, including failed/pending operations")
    async def cases(self, i: discord.Interaction, user: discord.Member | None = None):
        if not await require(self.bot, i):
            return
        rows = await self.bot.db.query("SELECT * FROM cases WHERE guild=?" + (" AND user=?" if user else "") + " ORDER BY id DESC LIMIT 12", (i.guild_id, user.id) if user else (i.guild_id,))
        await reply(i, "\n".join(f"#{r['id']} · {r['user']} · {r['action']} · {r['status']} · revoked={r['revoked']}\n{r['reason'][:75]} · {r['result'][:80]}" for r in rows)[:1900] or "No cases yet.")

    @crew.command(name="timeout", description="Timeout a member; maximum 28 days; confirmation required")
    async def timeout(self, i: discord.Interaction, user: discord.Member, length: str, reason: str):
        await self.moderate(i, user, "timeout", reason, duration(length))

    @crew.command(name="untimeout", description="Remove a timeout after confirmation")
    async def untimeout(self, i: discord.Interaction, user: discord.Member, reason: str):
        await self.moderate(i, user, "untimeout", reason)

    @crew.command(name="kick", description="Kick a member; optional bounded previous-message cleanup")
    async def kick(self, i: discord.Interaction, user: discord.Member, reason: str, cleanup_seconds: app_commands.Range[int, 0, 604800] = 0):
        await self.moderate(i, user, "kick", reason, delete_seconds=cleanup_seconds)

    @crew.command(name="ban", description="Ban a member; optional Discord-native previous-message deletion")
    async def ban(self, i: discord.Interaction, user: discord.Member, reason: str, cleanup_seconds: app_commands.Range[int, 0, 604800] = 0):
        await self.moderate(i, user, "ban", reason, delete_seconds=cleanup_seconds)

    @crew.command(name="purge", description="Delete at most 200 recent messages from this channel after confirmation")
    async def purge(self, i: discord.Interaction, count: app_commands.Range[int, 1, 200], user: discord.Member | None = None):
        if not await require(self.bot, i):
            return
        async def action(j):
            for member in (j.user, j.guild.me):
                if not j.channel.permissions_for(member).manage_messages:
                    raise ValueError("Manage Messages in this channel is required.")
            deleted = await j.channel.purge(limit=count, check=lambda msg: user is None or msg.author.id == user.id, reason=f"Ripcars Crew purge by {j.user.id}", bulk=True)
            await self.bot.db.audit(j.guild_id, j.user.id, "purge", f"channel={j.channel_id}; deleted={len(deleted)}")
            await reply(j, f"Deleted {len(deleted)} matching recent messages. Discord deletion cannot be undone.")
        await reply(i, f"Delete matching messages among the latest {count} in this channel?", view=Confirm(self.bot, i.user.id, action))

    @crew.command(name="cooldown", description="Temporarily restrict one member's posts in one channel while the bot is online")
    async def cooldown(self, i: discord.Interaction, user: discord.Member, channel: discord.TextChannel, seconds: app_commands.Range[int, 1, 86400]):
        if not await require(self.bot, i):
            return
        cfg, _ = await self.bot.db.settings(i.guild_id)
        guard = cfg["guard"]
        if not guard["enabled"] or guard["mode"] != "protect" or not guard["filters"]["member_cooldown"]["enabled"] or (guard["include_channels"] and not {channel.id, channel.category_id} & set(guard["include_channels"])) or {channel.id, channel.category_id} & set(guard["exempt_channels"]) or set(guard["exempt_roles"] + cfg["moderator_roles"]) & {r.id for r in user.roles}:
            raise ValueError("This member/channel is outside active cooldown enforcement. Check protection, rule, scope, and exemptions.")
        can_target(i.guild, i.user, user, "manage_messages")
        async def action(j):
            can_target(j.guild, j.user, user, "manage_messages")
            await self.bot.controls.member_cooldown(j.guild, user, channel, seconds, j.user.id)
            await self.bot.moderation.notice(user, cfg["texts"]["cooldown"] + f" Duration: {seconds}s in #{channel.name}.")
            await reply(j, f"Member {user.id} has a {seconds}s cooldown in <#{channel.id}>. This deletes posts after sending; it requires the bot online.")
        await reply(i, f"Apply a {seconds}s member-only cooldown in <#{channel.id}>? This is not native channel slowmode.", view=Confirm(self.bot, i.user.id, action))

    @crew.command(name="slowmode", description="Apply temporary native channel-wide slowmode, with safe expiry restoration")
    async def slowmode(self, i: discord.Interaction, channel: discord.TextChannel, seconds: app_commands.Range[int, 0, 21600], lifetime: app_commands.Range[int, 30, 86400]):
        if not await require(self.bot, i):
            return
        if not channel.permissions_for(i.user).manage_channels or not channel.permissions_for(i.guild.me).manage_channels:
            raise ValueError("Manage Channels is required for this control.")
        async def action(j):
            if not channel.permissions_for(j.user).manage_channels:
                raise ValueError("Channel permissions changed.")
            await self.bot.controls.slowmode(j.guild, channel, seconds, lifetime, j.user.id)
            await reply(j, f"Channel-wide slowmode {seconds}s for {lifetime}s. Manual changes during the window are preserved.")
        await reply(i, f"Apply {seconds}s channel-wide slowmode in <#{channel.id}> for {lifetime}s?", view=Confirm(self.bot, i.user.id, action))

    @crew.command(name="test-rule", description="Preview static rules without recording warnings or taking action")
    async def test_rule(self, i: discord.Interaction):
        if await require(self.bot, i, True):
            await i.response.send_modal(admin_ui.RuleTestModal(self.bot, i.user.id))

    @crew.command(name="export-config", description="Export settings without secrets or member data")
    async def export_config(self, i: discord.Interaction):
        if not await require(self.bot, i, True):
            return
        cfg, revision = await self.bot.db.settings(i.guild_id)
        await reply(i, file=discord.File(io.BytesIO(json.dumps({"revision": revision, "config": cfg}, ensure_ascii=False, indent=2).encode()), filename="ripcars-crew-config.json"))

    @crew.command(name="import-config", description="Validate and import a settings JSON attachment after confirmation")
    async def import_config(self, i: discord.Interaction, file: discord.Attachment):
        if not await require(self.bot, i, True):
            return
        if file.size > 256000 or not file.filename.lower().endswith(".json"):
            raise ValueError("Upload a JSON settings file smaller than 256 KB.")
        await i.response.defer(ephemeral=True)
        try:
            payload = json.loads(await file.read())
            cfg = payload.get("config", payload)
            config.validate(cfg)
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError("Invalid settings export: " + str(exc)[:200]) from exc
        _, revision = await self.bot.db.settings(i.guild_id)
        async def action(j):
            await admin_ui.checked_save(self.bot, j.guild, cfg, revision, j.user.id)
            await reply(j, "Settings imported. Republish public panels and explicitly sync native rules if required.")
        await reply(i, f"Import all validated settings over current revision {revision}? The previous settings remain in history.", view=Confirm(self.bot, i.user.id, action, admin=True))

    @crew.command(name="ticket-list", description="Review latest support tickets and incomplete operations")
    async def ticket_list(self, i: discord.Interaction):
        if await require(self.bot, i):
            rows = await self.bot.db.query("SELECT * FROM tickets WHERE guild=? ORDER BY id DESC LIMIT 20", (i.guild_id,))
            await reply(i, "\n".join(f"#{r['number']:04d} · {r['status']} · user {r['user']} · channel {r['channel']} · {r['last_error'][:60]}" for r in rows)[:1900] or "No tickets.")

    @crew.command(name="transcript", description="Export a bounded transcript from the current registered ticket")
    async def transcript(self, i: discord.Interaction):
        if not await require(self.bot, i) or not await self.bot.db.ticket(i.guild_id, i.channel_id):
            return
        await i.response.defer(ephemeral=True)
        cfg, _ = await self.bot.db.settings(i.guild_id)
        data = await self.bot.tickets.transcript(i.channel, cfg)
        await reply(i, file=discord.File(io.BytesIO(data), filename=f"ripcars-ticket-{i.channel_id}.txt"))

    @crew.command(name="forget-panel", description="Explicitly clear a missing/retired ticket panel registration")
    async def forget_panel(self, i: discord.Interaction):
        if not await require(self.bot, i, True):
            return
        async def action(j):
            await self.bot.db.execute("DELETE FROM panels WHERE guild=? AND key='tickets'", (j.guild_id,))
            await self.bot.db.audit(j.guild_id, j.user.id, "forget_panel", "Explicitly forgot registered ticket panel; old message not deleted")
            await reply(j, "Panel registration cleared. Remove any retired public message manually, then publish a replacement.")
        await reply(i, "Forget the panel registration? This does not delete its Discord message.", view=Confirm(self.bot, i.user.id, action, admin=True))

    @crew.command(name="history", description="Review previous settings revisions")
    async def history(self, i: discord.Interaction):
        if await require(self.bot, i, True):
            rows = await self.bot.db.query("SELECT id,actor,at FROM crew_history WHERE guild=? ORDER BY id DESC LIMIT 20", (i.guild_id,))
            await reply(i, "\n".join(f"History ID {r['id']} · actor {r['actor']} · <t:{int(r['at'])}:f>" for r in rows) or "No previous settings.")

    @crew.command(name="restore-config", description="Restore a reviewed settings snapshot with protection and tickets paused")
    async def restore_config(self, i: discord.Interaction, history_id: int):
        if not await require(self.bot, i, True):
            return
        rows = await self.bot.db.query("SELECT body FROM crew_history WHERE guild=? AND id=?", (i.guild_id, history_id))
        if not rows:
            raise ValueError("History ID not found for this server.")
        cfg = json.loads(rows[0]["body"])
        cfg["guard"]["enabled"] = cfg["tickets"]["enabled"] = cfg["native"]["enabled"] = False
        _, revision = await self.bot.db.settings(i.guild_id)
        async def action(j):
            await self.bot.db.save(j.guild_id, cfg, revision, j.user.id)
            await reply(j, "Snapshot restored with bot protection/tickets paused. Any previously synced native rule must still be disabled via Native Sync.")
        await reply(i, "Restore this snapshot with modules paused? Review bindings before enabling them. Native rule state in Discord is not changed by a settings restore.", view=Confirm(self.bot, i.user.id, action, admin=True))

    @review.command(name="control", description="Accept a channel's current slowmode and release a protected temporary control")
    async def review_control(self, i: discord.Interaction, channel: discord.TextChannel):
        if not await require(self.bot, i, True):
            return
        async def action(j):
            async with self.bot.db.lease(j.guild_id):
                rows = await self.bot.db.query("SELECT * FROM temporary WHERE guild=? AND channel=? AND kind='slowmode' AND state!='done'", (j.guild_id, channel.id))
                if not rows:
                    raise ValueError("No unresolved temporary control exists here.")
                for key, row in (await self.bot.db.resources(j.guild_id)).items():
                    if row["object_id"] == channel.id and row["state"] == "crew-temporary":
                        await self.bot.db.coord.execute("UPDATE resources SET state='manual' WHERE guild=? AND key=?", (j.guild_id, key))
                await self.bot.db.execute("UPDATE temporary SET state='done' WHERE guild=? AND channel=? AND kind='slowmode'", (j.guild_id, channel.id))
                await self.bot.db.audit(j.guild_id, j.user.id, "control_review", f"channel={channel.id}; current={channel.slowmode_delay}; manual value accepted")
            await reply(j, "Current slowmode accepted. Gate resource remains manual/protected; use Gate's Keep current if you want to pin it.")
        await reply(i, f"Accept current slowmode in <#{channel.id}> and stop restoration attempts? No channel permissions are changed.", view=Confirm(self.bot, i.user.id, action, admin=True))

    @review.command(name="case", description="Mark an unresolved moderation case reviewed without claiming API success")
    async def review_case(self, i: discord.Interaction, case_id: int, note: str):
        if not await require(self.bot, i, True):
            return
        if not 1 <= len(note) <= 500:
            raise ValueError("Add a review note of 1–500 characters.")
        async def action(j):
            changed = await self.bot.db.execute("UPDATE cases SET status='reviewed',result=? WHERE guild=? AND id=? AND status IN ('pending','failed')", (f"admin={j.user.id}; {note}", j.guild_id, case_id))
            if not changed:
                raise ValueError("No unresolved case matches that ID in this server.")
            await self.bot.db.audit(j.guild_id, j.user.id, "case_review", str(case_id))
            await reply(j, "Case marked reviewed, not executed/successful. Retry any intended action explicitly after checking Discord.")
        await reply(i, f"Record your review of unresolved case #{case_id}?\n{note}", view=Confirm(self.bot, i.user.id, action, admin=True))

    @review.command(name="native", description="Explicitly accept the current own-rule baseline or forget a deleted native rule")
    async def review_native(self, i: discord.Interaction, forget_missing: bool = False):
        if not await require(self.bot, i, True):
            return
        async def action(j):
            async with self.bot.db.lease(j.guild_id):
                rows = await self.bot.db.query("SELECT * FROM native WHERE guild=?", (j.guild_id,))
                if not rows:
                    raise ValueError("No native rule registration exists.")
                try:
                    rule = await j.guild.fetch_automod_rule(rows[0]["rule"])
                except discord.NotFound:
                    if not forget_missing:
                        raise ValueError("Rule is missing. Choose forget_missing true only after reviewing the deletion.")
                    await self.bot.db.execute("DELETE FROM native WHERE guild=?", (j.guild_id,))
                else:
                    if forget_missing:
                        raise ValueError("The rule still exists; it cannot be forgotten as missing.")
                    if rule.creator_id != self.bot.user.id:
                        raise ValueError("Foreign creator ID: adoption denied.")
                    from .native import snapshot
                    await self.bot.db.execute("UPDATE native SET baseline=?,state='active' WHERE guild=?", (json.dumps(snapshot(rule)), j.guild_id))
                await self.bot.db.audit(j.guild_id, j.user.id, "native_review", "explicit admin review")
            await reply(j, "Native registration reviewed. Sync is still explicit; no Discord rule was changed by this review.")
        await reply(i, "Accept the bot's current own-rule baseline, or explicitly forget a confirmed missing rule? No foreign rule can be adopted.", view=Confirm(self.bot, i.user.id, action, admin=True))


async def setup(bot):
    await bot.add_cog(CrewCommands(bot))
