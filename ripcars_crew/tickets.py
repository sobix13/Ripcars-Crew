"""Private support lifecycle: one non-name PATCH on close/reopen, then DB state."""
from __future__ import annotations

import io
import time

import discord

from .coordination import check_ticket_scope, staff_overwrites
from .detection import redact
from .operations import private_channel
from .ui_common import embed


def ticket_overwrites(guild, cfg, user, closed=False, existing=None):
    overwrites = dict(existing or {})
    for target, desired in staff_overwrites(guild, cfg).items():
        old = overwrites.get(target, discord.PermissionOverwrite())
        for field, value in desired:
            if value is not None:
                setattr(old, field, value)
        overwrites[target] = old
    target = user or discord.Object(id=0)
    if user is not None:
        old = overwrites.get(target, discord.PermissionOverwrite())
        old.update(view_channel=not closed, read_message_history=not closed, send_messages=not closed,
                   attach_files=not closed, embed_links=not closed, add_reactions=not closed,
                   mention_everyone=False, create_public_threads=False, create_private_threads=False,
                   send_messages_in_threads=False, manage_messages=False, manage_channels=False)
        overwrites[target] = old
    return overwrites


class Tickets:
    def __init__(self, bot):
        self.bot = bot

    async def update_controls(self, guild, channel):
        ticket = await self.bot.db.ticket(guild.id, channel.id)
        if not ticket or not ticket["control_message"]:
            return
        cfg, _ = await self.bot.db.settings(guild.id)
        card = embed(cfg, f"Ticket #{ticket['number']:04d} | {ticket['subject'][:150]}",
                     f"{cfg['texts']['ticket_welcome']}\n\n{ticket['details']}\n\nMember ID: {ticket['user']}")
        card.add_field(name="Status", value=ticket["status"], inline=True)
        card.add_field(name="Assigned staff ID", value=str(ticket["claimed"]) if ticket["claimed"] else "Not claimed", inline=True)
        try:
            control = await channel.fetch_message(ticket["control_message"])
            await control.edit(embed=card, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as exc:
            await self.bot.report(guild, "ticket control update", exc)

    async def open(self, guild, user, topic, subject, details):
        cfg, _ = await self.bot.db.settings(guild.id)
        if not cfg["tickets"]["enabled"]:
            raise ValueError("Tickets are paused. Please check back shortly.")
        if cfg["member_role"] not in {role.id for role in user.roles}:
            raise ValueError("Complete server verification first.")
        if not any(cat["key"] == topic and cat["enabled"] for cat in cfg["tickets"]["categories"]):
            raise ValueError("This ticket topic is not accepting tickets.")
        if not 1 <= len(subject.strip()) <= 200 or not 1 <= len(details.strip()) <= 1800:
            raise ValueError("A short subject and details are required.")
        async with self.bot.db.lease(guild.id, ttl=300):
            await check_ticket_scope(self.bot, guild, cfg)
            await self.bot.db.cooldown(guild.id, user.id, "ticket-create", cfg["tickets"]["create_cooldown_seconds"])
            ticket_id, number = await self.bot.db.reserve_ticket(guild.id, user.id, topic, redact(subject), redact(details))
            channel = None
            try:
                channel = await guild.create_text_channel(f"ticket-{number:04d}", category=guild.get_channel(cfg["tickets"]["open_category"]),
                    overwrites=ticket_overwrites(guild, cfg, user), reason=f"Ripcars Crew ticket {ticket_id}")
                await self.bot.db.execute("UPDATE tickets SET channel=? WHERE id=?", (channel.id, ticket_id))
                from .ticket_ui import TicketControls
                message = await channel.send(embed=embed(cfg, f"Ticket #{number:04d} | {subject[:150]}", f"{cfg['texts']['ticket_welcome']}\n\n{redact(details)}\n\nMember ID: {user.id}"),
                                             view=TicketControls(self.bot), allowed_mentions=discord.AllowedMentions.none())
                await self.bot.db.execute("UPDATE tickets SET status='open',control_message=? WHERE id=?", (message.id, ticket_id))
            except Exception as exc:
                if channel:
                    # Keep a recoverable record if rollback itself fails. Never hide an orphan.
                    try:
                        await channel.delete(reason="Ripcars Crew: incomplete ticket rollback")
                    except Exception as cleanup_error:
                        await self.bot.db.execute("UPDATE tickets SET last_error=? WHERE id=?", ("Channel rollback failed; admin repair required", ticket_id))
                        await self.bot.report(guild, "ticket creation rollback", cleanup_error)
                        raise exc
                await self.bot.db.execute("UPDATE tickets SET status='failed',last_error=? WHERE id=?", (type(exc).__name__, ticket_id))
                await self.bot.db.execute("DELETE FROM cooldowns WHERE guild=? AND user=? AND key='ticket-create'", (guild.id, user.id))
                raise
            await self.bot.reporter.log(guild, "Ticket opened", f"Ticket #{number:04d}; member {user.id}; channel {channel.id}; topic {topic}")
            return channel

    async def claim(self, guild, channel, actor):
        ticket = await self.bot.db.ticket(guild.id, channel.id)
        if not ticket or ticket["status"] != "open":
            raise ValueError("Only open tickets can be claimed.")
        await self.bot.db.execute("UPDATE tickets SET claimed=? WHERE id=? AND status='open'", (actor.id, ticket["id"]))
        await self.bot.db.audit(guild.id, actor.id, "ticket_claim", str(ticket["id"]))
        await self.update_controls(guild, channel)
        return ticket["number"]

    async def transition(self, guild, channel, actor, close=True):
        async with self.bot.db.lease(guild.id, ttl=300):
            cfg, _ = await self.bot.db.settings(guild.id)
            await check_ticket_scope(self.bot, guild, cfg)
            ticket = await self.bot.db.ticket(guild.id, channel.id)
            expected = "open" if close else "closed"
            if not ticket or ticket["status"] != expected:
                raise ValueError(f"Ticket must be {expected} before this action.")
            if not close and await self.bot.db.query("SELECT id FROM tickets WHERE guild=? AND user=? AND status IN ('creating','open','closing','reopening')", (guild.id, ticket["user"])):
                raise ValueError("This member already has another active ticket.")
            pending = "closing" if close else "reopening"
            await self.bot.db.execute("UPDATE tickets SET status=?,operation=? WHERE id=?", (pending, expected, ticket["id"]))
            user = guild.get_member(ticket["user"])
            if not user:
                # Fetch just the opener; never use a Role object for a member overwrite.
                try:
                    user = await guild.fetch_member(ticket["user"])
                except discord.NotFound:
                    if not close:
                        await self.bot.db.execute("UPDATE tickets SET status=? WHERE id=?", (expected, ticket["id"]))
                        raise ValueError("The ticket opener is no longer in the server.")
                    user = next((target for target in channel.overwrites if target.id == ticket["user"]), None)
            category = guild.get_channel(cfg["tickets"]["closed_category" if close else "open_category"])
            try:
                # Never rename on close/reopen, and never sync category permissions onto the ticket.
                await channel.edit(category=category, overwrites=ticket_overwrites(guild, cfg, user, close, channel.overwrites),
                                   sync_permissions=False, reason=f"Ripcars Crew ticket {ticket['id']} {'close' if close else 'reopen'}")
            except Exception as exc:
                await self.bot.db.execute("UPDATE tickets SET last_error=? WHERE id=?", (type(exc).__name__ + ": verify Discord state before retry", ticket["id"]))
                raise
            now = time.time()
            delete_at = now + cfg["tickets"]["auto_delete_days"] * 86400 if close and cfg["tickets"]["auto_delete_days"] else 0
            await self.bot.db.execute("UPDATE tickets SET status=?,closed=?,delete_at=?,operation='',last_error='' WHERE id=?", ("closed" if close else "open", now if close else 0, delete_at, ticket["id"]))
            await self.update_controls(guild, channel)
            await self.bot.db.audit(guild.id, actor.id, "ticket_close" if close else "ticket_reopen", str(ticket["id"]))
            await self.bot.reporter.log(guild, "Ticket closed" if close else "Ticket reopened", f"Ticket #{ticket['number']:04d}; actor {actor.id}")
            if close:
                try:
                    transcript = await self.transcript(channel, cfg)
                    log = guild.get_channel(cfg["log_channel"])
                    if private_channel(guild, log, cfg):
                        await log.send(content=f"Ticket #{ticket['number']:04d} transcript", file=discord.File(io.BytesIO(transcript), filename=f"ticket-{ticket['number']:04d}.txt"), allowed_mentions=discord.AllowedMentions.none())
                except Exception as exc:
                    await self.bot.report(guild, "ticket transcript", exc)
            return ticket["number"]

    async def transcript(self, channel, cfg):
        rows, total = [], 0
        limit = cfg["tickets"]["transcript_messages"]
        cap = cfg["tickets"]["transcript_bytes"]
        # Fetch newest bounded history, reverse for chronological display.
        async for msg in channel.history(limit=limit, oldest_first=False):
            attachments = " ".join(str(getattr(a, "filename", "attachment")) for a in msg.attachments)
            row = f"{msg.created_at.isoformat()} | user {msg.author.id} | {redact(msg.content)} | files: {attachments}\n".encode()
            if total + len(row) > cap - 300:
                break
            rows.append(row)
            total += len(row)
        return b"Rip Cars support transcript\nBounded export; may omit older messages. Attachments are not downloaded.\n\n" + b"".join(reversed(rows))

    async def repair(self, guild, actor):
        """Report incomplete states and settle only unambiguous observed transitions."""
        cfg, _ = await self.bot.db.settings(guild.id)
        outcomes = []
        for ticket in await self.bot.db.query("SELECT * FROM tickets WHERE guild=? AND status IN ('creating','closing','reopening','open','closed')", (guild.id,)):
            channel = guild.get_channel(ticket["channel"] or 0)
            if not channel:
                await self.bot.db.execute("UPDATE tickets SET status='missing',last_error='Channel unavailable; no auto recreation' WHERE id=?", (ticket["id"],))
                outcomes.append(f"#{ticket['number']}: missing; preserved record")
                continue
            if ticket["status"] in ("closing", "reopening"):
                closed = ticket["status"] == "closing"
                category = cfg["tickets"]["closed_category" if closed else "open_category"]
                target = next((t for t in channel.overwrites if t.id == ticket["user"]), None)
                overwrite = channel.overwrites.get(target) if target else None
                if channel.category_id == category and overwrite and overwrite.view_channel is (not closed) and overwrite.send_messages is (not closed):
                    await self.bot.db.execute("UPDATE tickets SET status=?,operation='',last_error='',delete_at=0 WHERE id=?", ("closed" if closed else "open", ticket["id"]))
                    outcomes.append(f"#{ticket['number']}: observed transition settled; auto-delete cleared")
                else:
                    outcomes.append(f"#{ticket['number']}: unresolved; no permission overwrite attempted")
            elif ticket["status"] == "creating":
                outcomes.append(f"#{ticket['number']}: incomplete starter; inspect channel before explicit deletion")
        await self.bot.db.audit(guild.id, actor, "ticket_repair", "; ".join(outcomes))
        return outcomes or ["No incomplete tickets."]

    async def delete(self, guild, channel, actor):
        async with self.bot.db.lease(guild.id, "ticket:" + str(channel.id)):
            ticket = await self.bot.db.ticket(guild.id, channel.id)
            if not ticket or ticket["status"] != "closed":
                raise ValueError("Only a registered closed ticket can be deleted.")
            await channel.delete(reason=f"Ripcars Crew: confirmed deletion of closed ticket {ticket['id']}")
            await self.bot.db.execute("UPDATE tickets SET status='deleted' WHERE id=?", (ticket["id"],))
            await self.bot.db.audit(guild.id, actor, "ticket_delete", str(ticket["id"]))

    async def sweep(self, guild):
        cfg, _ = await self.bot.db.settings(guild.id)
        if not cfg["tickets"]["auto_delete_days"]:
            return
        for ticket in await self.bot.db.query("SELECT * FROM tickets WHERE guild=? AND status='closed' AND delete_at>0 AND delete_at<=?", (guild.id, time.time())):
            channel = guild.get_channel(ticket["channel"])
            if channel:
                await self.delete(guild, channel, self.bot.user.id)
