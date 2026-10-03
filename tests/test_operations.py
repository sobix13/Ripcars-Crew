import sqlite3
import tempfile
import unittest
from pathlib import Path

import discord

from ripcars_crew import operations
from tests.fakes import AsyncFixture


class OperationsTests(AsyncFixture, unittest.IsolatedAsyncioTestCase):
    async def test_private_log_never_leaks_to_public(self):
        cfg, rev = await self.bot.db.settings(101)
        cfg["log_channel"] = 305
        await self.bot.db.save(101, cfg, rev, 603)
        self.assertFalse(await self.bot.reporter.log(self.guild, "Private case", "member details"))
        self.guild.general.send.assert_not_awaited()

    async def test_error_id_persisted_and_private_alert(self):
        error_id = await self.bot.report(self.guild, "injected", RuntimeError("test error"))
        self.assertTrue(error_id.startswith("RCC-"))
        rows = await self.bot.db.query("SELECT * FROM errors")
        self.assertEqual(rows[0]["id"], error_id)
        self.guild.log.send.assert_awaited_once()

    async def test_error_alert_rate_limited(self):
        for _ in range(3):
            await self.bot.report(self.guild, "same failure", RuntimeError("injected"))
        self.guild.log.send.assert_awaited_once()
        self.assertEqual(len(await self.bot.db.query("SELECT * FROM errors")), 3)

    async def test_doctor_blockers_actionable(self):
        cfg, _ = await self.bot.db.settings(101)
        cfg["moderator_roles"] = []
        blockers, _ = await operations.doctor(self.bot, self.guild, cfg)
        self.assertIn("Configure valid moderator role IDs.", blockers)

    async def test_permissions_no_administrator(self):
        self.assertFalse(operations.permissions().administrator)
        self.assertTrue(operations.permissions().manage_messages)
        self.assertTrue(operations.permissions().read_message_history)

    async def test_health_real_db_and_nan_latency(self):
        card = await operations.health(self.bot, self.guild)
        self.assertIn("Database: True", card.description)
        self.assertIn("not available", card.description)

    async def test_backup_consistent_retained_unique(self):
        targets = [operations.backup(self.bot.db.path, keep=3) for _ in range(5)]
        self.assertEqual(len(set(targets)), 5)
        self.assertEqual(len(list((Path(self.bot.db.path).parent / "backups").glob("*.sqlite3"))), 3)
        with sqlite3.connect(targets[-1]) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    async def test_no_backup_for_missing_database(self):
        with tempfile.TemporaryDirectory() as path:
            with self.assertRaises(ValueError):
                operations.backup(Path(path) / "missing.sqlite3")

    async def test_privacy_check_claimable_role_cannot_read_log(self):
        cfg, _ = await self.bot.db.settings(101)
        self.guild.log.overwrites[self.guild.rippers] = discord.PermissionOverwrite(view_channel=True)
        self.assertFalse(operations.private_channel(self.guild, self.guild.log, cfg))
