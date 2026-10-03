import unittest
from types import SimpleNamespace

from ripcars_crew import admin_ui, ticket_ui
from ripcars_crew.ui_common import Confirm, allowed
from tests.fakes import AsyncFixture, interaction


class UITests(AsyncFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await ticket_ui.publish(self.bot, self.guild, 603)

    async def test_all_sections_serialize_within_discord_limits(self):
        cfg, rev = await self.bot.db.settings(101)
        views = [admin_ui.Hub(self.bot, 603), admin_ui.SetupWizard(self.bot, 603, cfg, rev),
                 admin_ui.SetupWizard(self.bot, 603, cfg, rev, 1), ticket_ui.TicketControls(self.bot), ticket_ui.TicketPanel(self.bot, cfg)]
        views += [admin_ui.Section(self.bot, 603, section) for section in admin_ui.SECTIONS]
        for view in views:
            components = view.to_components()
            self.assertLessEqual(len(components), 5)
            for row in components:
                self.assertLessEqual(len(row["components"]), 5)
                for component in row["components"]:
                    self.assertLessEqual(len(component.get("options", [])), 25)

    async def test_wizard_selection_is_not_reset(self):
        cfg, rev = await self.bot.db.settings(101)
        wizard = admin_ui.SetupWizard(self.bot, 603, cfg, rev)
        select = next(c for c in wizard.children if getattr(c, "placeholder", None) == "Private staff log")
        select._values = [SimpleNamespace(id=303)]
        i = interaction(self.bot)
        await select.callback(i)
        self.assertEqual(wizard.cfg["log_channel"], 303)
        self.assertEqual(select.default_values[0].id, 303)
        self.assertIs(i.response.edit_message.await_args.kwargs["view"], wizard)

    async def test_modal_stale_revision_rejected(self):
        cfg, rev = await self.bot.db.settings(101)
        modal = admin_ui.SettingModal(self.bot, 603, cfg, rev, "brand")
        modal.value._value = "Rip Cars Club"
        await self.bot.db.save(101, cfg, rev, 603)
        with self.assertRaises(ValueError):
            await modal.on_submit(interaction(self.bot))

    async def test_word_add_remove_inside_discord(self):
        cfg, rev = await self.bot.db.settings(101)
        modal = admin_ui.ListEntryModal(self.bot, 603, cfg, rev, "guard.words")
        modal.text._value, modal.mode._value = "badword", "exact"
        await modal.on_submit(interaction(self.bot))
        cfg, rev = await self.bot.db.settings(101)
        self.assertEqual(cfg["guard"]["words"], [{"text": "badword", "mode": "exact"}])
        modal = admin_ui.ListEntryModal(self.bot, 603, cfg, rev, "guard.words", True)
        modal.text._value, modal.mode._value = "badword", "exact"
        await modal.on_submit(interaction(self.bot))
        self.assertEqual((await self.bot.db.settings(101))[0]["guard"]["words"], [])

    async def test_nonadmin_settings_submit_denied(self):
        cfg, rev = await self.bot.db.settings(101)
        modal = admin_ui.SettingModal(self.bot, 601, cfg, rev, "brand")
        modal.value._value = "Hacked"
        await modal.on_submit(interaction(self.bot, self.guild.user))
        self.assertNotEqual((await self.bot.db.settings(101))[0]["brand"], "Hacked")

    async def test_foreign_panel_owner_denied(self):
        hub = admin_ui.Hub(self.bot, 603)
        self.assertFalse(await hub.interaction_check(interaction(self.bot, self.guild.user)))

    async def test_persistent_ticket_controls(self):
        self.assertTrue(ticket_ui.TicketControls(self.bot).is_persistent())
        self.assertTrue(ticket_ui.TicketPanel(self.bot).is_persistent())
        ids = [c.custom_id for c in ticket_ui.TicketControls(self.bot).children]
        self.assertTrue(all(v.startswith("ripcars:crew:") for v in ids))

    async def test_confirmation_only_once(self):
        invoked = []
        async def action(i):
            invoked.append(i.user.id)
        view = Confirm(self.bot, 603, action, admin=True)
        button = next(c for c in view.children if c.label == "Confirm")
        await button.callback(interaction(self.bot))
        await button.callback(interaction(self.bot))
        self.assertEqual(invoked, [603])

    async def test_activation_blockers_details(self):
        cfg, rev = await self.bot.db.settings(101)
        cfg["log_channel"] = 305
        with self.assertRaisesRegex(ValueError, "private staff-only"):
            await admin_ui.checked_save(self.bot, self.guild, cfg, rev, 603)

    async def test_ticket_modal_end_to_end(self):
        cfg, _ = await self.bot.db.settings(101)
        modal = ticket_ui.TicketModal(self.bot, 601, cfg, "packs")
        modal.subject._value, modal.details._value = "Pack issue", "Please check"
        await modal.on_submit(interaction(self.bot, self.guild.user))
        rows = await self.bot.db.query("SELECT * FROM tickets")
        self.assertEqual(rows[0]["status"], "open")
        self.guild.create_text_channel.assert_awaited_once()

    async def test_claim_controls_require_staff(self):
        view = ticket_ui.TicketControls(self.bot)
        await view.claim.callback(interaction(self.bot, self.guild.user))
        self.assertFalse(await self.bot.db.query("SELECT * FROM audit WHERE area='ticket_claim'"))

    async def test_admin_separate_from_moderator(self):
        i = interaction(self.bot, self.guild.staff)
        self.assertTrue(await allowed(self.bot, i))
        self.assertFalse(await allowed(self.bot, i, True))
