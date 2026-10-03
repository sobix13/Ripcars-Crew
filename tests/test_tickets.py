import asyncio
import unittest
from datetime import datetime, timezone

import discord

from ripcars_crew import ticket_ui
from tests.fakes import AsyncFixture, Member, Role, message


class TicketTests(AsyncFixture, unittest.IsolatedAsyncioTestCase):
    async def open_ticket(self):
        return await self.bot.tickets.open(self.guild, self.guild.user, "packs", "Pack issue", "A pack needs a check")

    async def test_open_private_for_author_and_staff(self):
        channel = await self.open_ticket()
        other = Member(701, self.guild, [self.guild.rippers])
        self.assertTrue(channel.permissions_for(self.guild.user).view_channel)
        self.assertTrue(channel.permissions_for(self.guild.user).read_message_history)
        self.assertTrue(channel.permissions_for(self.guild.staff).view_channel)
        self.assertFalse(channel.permissions_for(other).view_channel)
        self.assertFalse(channel.permissions_for(self.guild.user).mention_everyone)
        self.assertEqual((await self.bot.db.ticket(101, channel.id))["status"], "open")

    async def test_no_membership_no_ticket(self):
        guest = Member(701, self.guild, [self.guild.default_role])
        with self.assertRaises(ValueError):
            await self.bot.tickets.open(self.guild, guest, "packs", "subject", "details")
        self.guild.create_text_channel.assert_not_awaited()

    async def test_duplicate_ticket_prevented(self):
        await self.open_ticket()
        with self.assertRaises(ValueError):
            await self.open_ticket()
        self.guild.create_text_channel.assert_awaited_once()

    async def test_concurrent_open_prevented(self):
        results = await asyncio.gather(self.open_ticket(), self.open_ticket(), return_exceptions=True)
        self.assertEqual(sum(not isinstance(r, Exception) for r in results), 1)
        self.guild.create_text_channel.assert_awaited_once()

    async def test_close_one_patch_without_rename(self):
        channel = await self.open_ticket()
        await self.bot.tickets.transition(self.guild, channel, self.guild.staff)
        channel.edit.assert_awaited_once()
        self.assertNotIn("name", channel.edit.await_args.kwargs)
        self.assertFalse(channel.edit.await_args.kwargs["sync_permissions"])
        self.assertEqual(channel.category_id, self.guild.closed_category.id)
        self.assertFalse(channel.permissions_for(self.guild.user).view_channel)
        self.assertTrue(channel.permissions_for(self.guild.staff).view_channel)

    async def test_failed_patch_does_not_claim_closed(self):
        channel = await self.open_ticket()
        channel.edit.side_effect = RuntimeError("injected API failure")
        with self.assertRaises(RuntimeError):
            await self.bot.tickets.transition(self.guild, channel, self.guild.staff)
        ticket = await self.bot.db.ticket(101, channel.id)
        self.assertEqual(ticket["status"], "closing")
        self.assertEqual(ticket["operation"], "open")

    async def test_db_failure_after_discord_transition_recoverable(self):
        channel = await self.open_ticket()
        original = self.bot.db.execute
        async def fail(sql, args=()):
            if "closed=?,delete_at=?" in sql:
                raise RuntimeError("injected DB failure")
            return await original(sql, args)
        self.bot.db.execute = fail
        with self.assertRaises(RuntimeError):
            await self.bot.tickets.transition(self.guild, channel, self.guild.staff)
        self.bot.db.execute = original
        self.assertEqual((await self.bot.db.ticket(101, channel.id))["status"], "closing")
        report = await self.bot.tickets.repair(self.guild, 603)
        self.assertIn("observed transition settled", report[0])
        self.assertEqual((await self.bot.db.ticket(101, channel.id))["status"], "closed")

    async def test_reopen_one_patch_restore_author(self):
        channel = await self.open_ticket()
        await self.bot.tickets.transition(self.guild, channel, self.guild.staff)
        channel.edit.reset_mock()
        await self.bot.tickets.transition(self.guild, channel, self.guild.staff, False)
        channel.edit.assert_awaited_once()
        self.assertNotIn("name", channel.edit.await_args.kwargs)
        self.assertTrue(channel.permissions_for(self.guild.user).view_channel)
        self.assertEqual((await self.bot.db.ticket(101, channel.id))["status"], "open")

    async def test_reopen_denied_when_new_open_ticket(self):
        channel = await self.open_ticket()
        await self.bot.tickets.transition(self.guild, channel, self.guild.staff)
        await self.open_ticket()
        with self.assertRaises(ValueError):
            await self.bot.tickets.transition(self.guild, channel, self.guild.staff, False)

    async def test_foreign_overwrite_preserved(self):
        channel = await self.open_ticket()
        foreign = Role(777, "Other bot", 5, managed=True)
        overwrite = discord.PermissionOverwrite(view_channel=True, use_external_emojis=True)
        channel.overwrites[foreign] = overwrite
        await self.bot.tickets.transition(self.guild, channel, self.guild.staff)
        self.assertEqual(channel.overwrites[foreign].pair(), overwrite.pair())

    async def test_claim_does_not_rename(self):
        channel = await self.open_ticket()
        await self.bot.tickets.claim(self.guild, channel, self.guild.staff)
        channel.edit.assert_not_awaited()
        self.assertEqual((await self.bot.db.ticket(101, channel.id))["claimed"], 602)

    async def test_creation_api_failure_no_active_ticket(self):
        self.guild.create_text_channel.side_effect = RuntimeError("injected")
        with self.assertRaises(RuntimeError):
            await self.open_ticket()
        self.assertEqual((await self.bot.db.query("SELECT status FROM tickets"))[0]["status"], "failed")

    async def test_starter_failure_rolls_back_known_channel(self):
        creator = self.guild.create_text_channel.side_effect
        async def fail(*args, **kwargs):
            channel = await creator(*args, **kwargs)
            channel.send.side_effect = RuntimeError("injected")
            return channel
        self.guild.create_text_channel.side_effect = fail
        with self.assertRaises(RuntimeError):
            await self.open_ticket()
        self.assertEqual(len(self.guild.channels), 5)
        self.assertEqual((await self.bot.db.query("SELECT status FROM tickets"))[0]["status"], "failed")

    async def test_gate_owner_requires_explicit_handoff(self):
        await self.bot.db.coord.execute("INSERT INTO resources VALUES(101,'channel:open_tickets',301,'category','ripcars-gate','{}','{}','active')")
        with self.assertRaises(ValueError):
            await self.open_ticket()
        self.guild.create_text_channel.assert_not_awaited()
        await self.bot.db.coord.execute("UPDATE resources SET owner='bot:501',state='external' WHERE guild=101")
        channel = await self.open_ticket()
        self.assertTrue(channel)

    async def test_bad_private_category_blocks_ticket(self):
        self.guild.open_category.overwrites[self.guild.rippers] = discord.PermissionOverwrite(view_channel=True)
        with self.assertRaises(ValueError):
            await self.open_ticket()

    async def test_transcript_bounded_redacts_no_download(self):
        channel = await self.open_ticket()
        cfg = (await self.bot.db.settings(101))[0]
        cfg["tickets"]["transcript_bytes"] = 10000
        for n in range(25):
            msg = message(self.bot, "password: topsecret\n" + "x" * 1000, message_id=800 + n)
            msg.created_at = datetime.now(timezone.utc)
            channel.history_rows.append(msg)
        data = await self.bot.tickets.transcript(channel, cfg)
        self.assertLessEqual(len(data), 10000)
        self.assertNotIn(b"topsecret", data)
        self.assertIn(b"may omit older messages", data)

    async def test_auto_delete_off(self):
        channel = await self.open_ticket()
        await self.bot.tickets.transition(self.guild, channel, self.guild.staff)
        await self.bot.db.execute("UPDATE tickets SET delete_at=1")
        await self.bot.tickets.sweep(self.guild)
        channel.delete.assert_not_awaited()

    async def test_only_closed_ticket_deletion(self):
        channel = await self.open_ticket()
        with self.assertRaises(ValueError):
            await self.bot.tickets.delete(self.guild, channel, 603)
        channel.delete.assert_not_awaited()

    async def test_missing_channel_not_recreated(self):
        channel = await self.open_ticket()
        self.guild.channels.pop(channel.id)
        await self.bot.tickets.repair(self.guild, 603)
        self.guild.create_text_channel.assert_awaited_once()
        self.assertEqual((await self.bot.db.ticket(101, channel.id))["status"], "missing")

    async def test_publish_idempotent(self):
        first = await ticket_ui.publish(self.bot, self.guild, 603)
        second = await ticket_ui.publish(self.bot, self.guild, 603)
        self.assertEqual(first, second)
        self.guild.panel.send.assert_awaited_once()
        self.guild.panel.messages[first].edit.assert_awaited_once()

    async def test_missing_public_panel_requires_review(self):
        first = await ticket_ui.publish(self.bot, self.guild, 603)
        self.guild.panel.messages.pop(first)
        with self.assertRaises(ValueError):
            await ticket_ui.publish(self.bot, self.guild, 603)
        self.guild.panel.send.assert_awaited_once()
