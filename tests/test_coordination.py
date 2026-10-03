import json
import unittest

from ripcars_crew.coordination import import_gate, standalone_setup
from tests.fakes import AsyncFixture, Channel


class CoordinationTests(AsyncFixture, unittest.IsolatedAsyncioTestCase):
    async def register_general(self):
        snapshot = json.dumps({"slowmode": 0})
        await self.bot.db.coord.execute("INSERT INTO resources VALUES(101,'channel:general',305,'text','ripcars-gate',?,?,'active')", (snapshot, snapshot))

    async def test_gate_ids_import_no_owner_mutation(self):
        await self.register_general()
        await self.bot.db.coord.execute("INSERT INTO resources VALUES(101,'role:rippers',201,'role','ripcars-gate','{}','{}','active')")
        before = await self.bot.db.resources(101)
        await import_gate(self.bot, self.guild, 603)
        self.assertEqual(await self.bot.db.resources(101), before)

    async def test_temporary_slowmode_pauses_gate_then_restores(self):
        await self.register_general()
        await self.bot.controls.slowmode(self.guild, self.guild.general, 30, 60, 603)
        self.assertEqual((await self.bot.db.resources(101))["channel:general"]["state"], "crew-temporary")
        await self.bot.db.execute("UPDATE temporary SET expires=0")
        await self.bot.controls.restore_due(self.guild)
        row = (await self.bot.db.resources(101))["channel:general"]
        self.assertEqual(row["state"], "active")
        self.assertEqual(row["owner"], "ripcars-gate")
        self.assertEqual(self.guild.general.slowmode_delay, 0)

    async def test_manual_slowmode_preserved(self):
        await self.register_general()
        await self.bot.controls.slowmode(self.guild, self.guild.general, 30, 60, 603)
        self.guild.general.slowmode_delay = 99
        await self.bot.db.execute("UPDATE temporary SET expires=0")
        await self.bot.controls.restore_due(self.guild)
        self.assertEqual(self.guild.general.slowmode_delay, 99)
        self.assertEqual((await self.bot.db.resources(101))["channel:general"]["state"], "manual")

    async def test_ownership_change_preserved(self):
        await self.register_general()
        await self.bot.controls.slowmode(self.guild, self.guild.general, 30, 60, 603)
        await self.bot.db.coord.execute("UPDATE resources SET owner='bot:900' WHERE guild=101")
        await self.bot.db.execute("UPDATE temporary SET expires=0")
        await self.bot.controls.restore_due(self.guild)
        self.assertEqual((await self.bot.db.resources(101))["channel:general"]["owner"], "bot:900")
        self.assertEqual(self.guild.general.slowmode_delay, 30)

    async def test_slowmode_limit(self):
        with self.assertRaises(ValueError):
            await self.bot.controls.slowmode(self.guild, self.guild.general, 86400, 60, 603)

    async def test_unknown_channel_not_touched(self):
        unknown = Channel(999, self.guild, "Other bot's channel")
        self.guild.channels[999] = unknown
        await self.bot.controls.restore_due(self.guild)
        unknown.edit.assert_not_awaited()

    async def test_duplicate_category_not_adopted_by_name(self):
        cfg, rev = await self.bot.db.settings(101)
        cfg["tickets"].update(enabled=False, open_category=0, closed_category=0)
        await self.bot.db.save(101, cfg, rev, 603)
        with self.assertRaises(ValueError):
            await standalone_setup(self.bot, self.guild, 603)
        self.guild.create_category.assert_not_awaited()

    async def test_temporary_survives_restart(self):
        await self.register_general()
        await self.bot.controls.slowmode(self.guild, self.guild.general, 30, 60, 603)
        await self.bot.db.close()
        await self.bot.db.open()
        await self.bot.db.execute("UPDATE temporary SET expires=0")
        await self.bot.controls.restore_due(self.guild)
        self.assertEqual(self.guild.general.slowmode_delay, 0)
