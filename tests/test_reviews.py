import unittest
from unittest.mock import MagicMock

from ripcars_crew.commands import CrewCommands
from tests.fakes import AsyncFixture, interaction, message


class ReviewTests(AsyncFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.bot.tree = MagicMock()
        self.cog = CrewCommands(self.bot)

    async def confirm_reply(self, i):
        view = i.response.send_message.await_args.kwargs["view"]
        button = next(c for c in view.children if c.label == "Confirm")
        j = interaction(self.bot)
        self.assertTrue(await view.interaction_check(j))
        await button.callback(j)

    async def test_member_report_no_punishment(self):
        i = interaction(self.bot, self.guild.user)
        await self.cog.report_context(i, message(self.bot, "please review", user=self.guild.staff))
        rows = await self.bot.db.query("SELECT * FROM cases")
        self.assertEqual(rows[0]["action"], "member_report")
        self.guild.staff.timeout.assert_not_awaited()
        self.guild.ban.assert_not_awaited()

    async def test_member_report_rate_limit(self):
        i = interaction(self.bot, self.guild.user)
        msg = message(self.bot, "review")
        await self.cog.report_context(i, msg)
        with self.assertRaises(ValueError):
            await self.cog.report_context(i, msg)

    async def test_context_menu_registered(self):
        self.bot.tree.add_command.assert_called_once()
        self.assertEqual(self.cog.report_menu.name, "Report to Rip Cars Crew")
        self.assertEqual(self.cog.report_menu.type.value, 3)

    async def test_unresolved_case_marked_reviewed_not_success(self):
        case, _ = await self.bot.db.case(101, 601, 602, "timeout", "unknown", "pending")
        i = interaction(self.bot)
        await self.cog.review_case.callback(self.cog, i, case, "Checked member")
        await self.confirm_reply(i)
        row = (await self.bot.db.query("SELECT status FROM cases WHERE id=?", (case,)))[0]
        self.assertEqual(row["status"], "reviewed")

    async def test_moderator_cannot_review_or_revoke_admin_case(self):
        case, _ = await self.bot.db.case(101, 601, 603, "warn", "test")
        i = interaction(self.bot, self.guild.staff)
        await self.cog.revoke.callback(self.cog, i, case)
        self.assertEqual(await self.bot.db.warning_count(101, 601, 30), 1)
        self.assertNotIn("view", i.response.send_message.await_args.kwargs)

    async def test_review_control_accepts_manual_without_writing_channel(self):
        await self.bot.controls.slowmode(self.guild, self.guild.general, 10, 60, 603)
        self.guild.general.slowmode_delay = 99
        self.guild.general.edit.reset_mock()
        i = interaction(self.bot)
        await self.cog.review_control.callback(self.cog, i, self.guild.general)
        await self.confirm_reply(i)
        self.guild.general.edit.assert_not_awaited()
        self.assertEqual((await self.bot.db.query("SELECT state FROM temporary"))[0]["state"], "done")

    async def test_member_report_hidden_channel_denied(self):
        i = interaction(self.bot, self.guild.user)
        msg = message(self.bot, "staff only", channel=self.guild.log)
        with self.assertRaises(ValueError):
            await self.cog.report_context(i, msg)

    async def test_filter_modal_updates_runtime(self):
        from ripcars_crew.admin_ui import FilterModal
        from ripcars_crew.ticket_ui import publish
        await publish(self.bot, self.guild, 603)
        cfg, rev = await self.bot.db.settings(101)
        modal = FilterModal(self.bot, 603, cfg, rev, "gift_scam")
        modal.enabled._value, modal.action._value, modal.weight._value = "true", "warn", "4"
        await modal.on_submit(interaction(self.bot))
        rule = (await self.bot.db.settings(101))[0]["guard"]["filters"]["gift_scam"]
        self.assertEqual(rule, {"enabled": True, "action": "warn", "weight": 4})
