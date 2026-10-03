import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from tests.fakes import AsyncFixture, Member, Role, message


class ModerationTests(AsyncFixture, unittest.IsolatedAsyncioTestCase):
    async def word_config(self, mode="protect", action="warn"):
        cfg, revision = await self.bot.db.settings(101)
        cfg["guard"].update(words=[{"text": "blockedword", "mode": "exact"}], mode=mode)
        cfg["guard"]["filters"]["words"]["action"] = action
        await self.bot.db.save(101, cfg, revision, 603)

    async def test_warning_delete_and_case(self):
        await self.word_config()
        msg = message(self.bot, "blockedword")
        await self.bot.moderation.process(msg)
        msg.delete.assert_awaited_once()
        self.guild.user.send.assert_awaited_once()
        self.assertEqual(await self.bot.db.warning_count(101, 601, 30), 1)

    async def test_observe_never_sanctions(self):
        await self.word_config("observe")
        msg = message(self.bot, "blockedword")
        await self.bot.moderation.process(msg)
        msg.delete.assert_not_awaited()
        self.guild.user.timeout.assert_not_awaited()
        self.assertEqual(await self.bot.db.warning_count(101, 601, 30), 0)

    async def test_alert_only_never_warns(self):
        await self.word_config(action="alert")
        msg = message(self.bot, "blockedword")
        await self.bot.moderation.process(msg)
        msg.delete.assert_not_awaited()
        self.assertEqual(await self.bot.db.warning_count(101, 601, 30), 0)

    async def test_edit_after_safe_message_detected(self):
        await self.word_config()
        safe = message(self.bot, "hello")
        await self.bot.moderation.process(safe)
        safe.content = "blockedword"
        await self.bot.moderation.process(safe, edited=True)
        safe.delete.assert_awaited_once()

    async def test_repeated_event_dedup(self):
        await self.word_config()
        msg = message(self.bot, "blockedword")
        for _ in range(2):
            await self.bot.moderation.process(msg)
        msg.delete.assert_awaited_once()
        self.assertEqual(await self.bot.db.warning_count(101, 601, 30), 1)

    async def test_warning_escalation_no_ban(self):
        await self.word_config()
        for n in range(3):
            await self.bot.moderation.process(message(self.bot, "blockedword", 701 + n))
        self.guild.user.timeout.assert_awaited_once()
        self.guild.ban.assert_not_awaited()

    async def test_staff_exempt(self):
        await self.word_config()
        msg = message(self.bot, "blockedword", user=self.guild.staff)
        self.assertEqual(await self.bot.moderation.process(msg), [])
        msg.delete.assert_not_awaited()

    async def test_channel_exempt(self):
        await self.word_config()
        cfg, rev = await self.bot.db.settings(101)
        cfg["guard"]["exempt_channels"] = [305]
        await self.bot.db.save(101, cfg, rev, 603)
        self.assertEqual(await self.bot.moderation.process(message(self.bot, "blockedword")), [])

    async def test_disabled_guard(self):
        await self.word_config()
        cfg, rev = await self.bot.db.settings(101)
        cfg["guard"]["enabled"] = False
        await self.bot.db.save(101, cfg, rev, 603)
        self.assertEqual(await self.bot.moderation.process(message(self.bot, "blockedword")), [])

    async def test_missing_delete_permission_is_reported(self):
        await self.word_config()
        msg = message(self.bot, "blockedword")
        msg.delete.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions")
        await self.bot.moderation.process(msg)
        self.bot.report.assert_awaited()
        case = (await self.bot.db.query("SELECT * FROM cases"))[0]
        self.assertIn("delete failed", case["result"])

    async def test_timeout_failure_not_recorded_success(self):
        await self.word_config(action="timeout")
        self.guild.user.timeout.side_effect = RuntimeError("injected")
        await self.bot.moderation.process(message(self.bot, "blockedword"))
        rows = await self.bot.db.query("SELECT * FROM cases WHERE action='timeout'")
        self.assertEqual([r["status"] for r in rows], ["failed"])

    async def test_member_only_cooldown(self):
        await self.bot.controls.member_cooldown(self.guild, self.guild.user, self.guild.general, 60, 602)
        msg = message(self.bot, "hello")
        await self.bot.moderation.process(msg)
        msg.delete.assert_awaited_once()
        other = Member(610, self.guild, [self.guild.rippers])
        other_msg = message(self.bot, "hello", 999, other)
        await self.bot.moderation.process(other_msg)
        other_msg.delete.assert_not_awaited()

    async def test_manual_moderation_role_hierarchy(self):
        target = Member(611, self.guild, [Role(222, "Above staff", 20)])
        with self.assertRaises(ValueError):
            await self.bot.moderation.manual(self.guild, self.guild.staff, target, "kick", "reason")

    async def test_manual_api_failure(self):
        self.guild.user.kick.side_effect = RuntimeError("injected")
        with self.assertRaises(RuntimeError):
            await self.bot.moderation.manual(self.guild, self.guild.staff, self.guild.user, "kick", "reason")
        self.assertEqual((await self.bot.db.query("SELECT status FROM cases"))[0]["status"], "failed")

    async def test_ban_native_deletion_window(self):
        await self.bot.moderation.manual(self.guild, self.guild.staff, self.guild.user, "ban", "reason", delete_seconds=604800)
        self.assertEqual(self.guild.ban.await_args.kwargs["delete_message_seconds"], 604800)

    async def test_unknown_bot_not_auto_trusted_when_scan_enabled(self):
        await self.word_config()
        cfg, rev = await self.bot.db.settings(101)
        cfg["guard"]["scan_bots"] = True
        await self.bot.db.save(101, cfg, rev, 603)
        bot_user = Member(700, self.guild, [self.guild.rippers], True)
        msg = message(self.bot, "blockedword", user=bot_user)
        await self.bot.moderation.process(msg)
        msg.delete.assert_awaited_once()
        bot_user.timeout.assert_not_awaited()

    async def test_webhook_scan_does_not_try_member_timeout(self):
        await self.word_config(action="timeout")
        author = SimpleNamespace(id=888, bot=True, display_name="Webhook")
        msg = message(self.bot, "blockedword", user=author)
        msg.webhook_id = 333
        await self.bot.moderation.process(msg)
        msg.delete.assert_awaited_once()

    async def test_notice_rate_limit(self):
        await self.word_config()
        for n in range(2):
            await self.bot.moderation.process(message(self.bot, "blockedword", 701 + n))
        self.guild.user.send.assert_awaited_once()

    async def test_admin_target_protected(self):
        with self.assertRaises(ValueError):
            await self.bot.moderation.manual(self.guild, self.guild.staff, self.guild.administrator, "warn", "reason")

    async def test_actor_permission_not_just_role(self):
        no_ban = Member(612, self.guild, [self.guild.mod])
        with self.assertRaises(ValueError):
            await self.bot.moderation.manual(self.guild, no_ban, self.guild.user, "ban", "reason")

    async def test_no_dm_failure_interrupt(self):
        self.guild.user.send = AsyncMock(side_effect=discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "DM closed"))
        await self.word_config()
        await self.bot.moderation.process(message(self.bot, "blockedword"))
        self.assertEqual(await self.bot.db.warning_count(101, 601, 30), 1)
