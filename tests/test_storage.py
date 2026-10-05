import asyncio
import json
import time
import unittest
from pathlib import Path

from ripcars_crew.storage import Conflict, Database
from tests.fakes import AsyncFixture


class StorageTests(AsyncFixture, unittest.IsolatedAsyncioTestCase):
    async def test_revision_conflict(self):
        cfg, rev = await self.bot.db.settings(101)
        await self.bot.db.save(101, cfg, rev, 1)
        with self.assertRaises(Conflict):
            await self.bot.db.save(101, cfg, rev, 2)

    async def test_invalid_save_does_not_change_config(self):
        cfg, rev = await self.bot.db.settings(101)
        cfg["guard"]["timeout_seconds"] = -1
        with self.assertRaises(ValueError):
            await self.bot.db.save(101, cfg, rev, 1)
        self.assertEqual((await self.bot.db.settings(101))[1], rev)

    async def test_history_bounded(self):
        for n in range(35):
            cfg, rev = await self.bot.db.settings(101)
            await self.bot.db.save(101, cfg, rev, n)
        self.assertEqual(len(await self.bot.db.query("SELECT * FROM crew_history WHERE guild=101")), 30)

    async def test_shared_lease_contention(self):
        other = Database(Path(self.temp.name) / "other.sqlite3", self.bot.db.coord.path)
        await other.open()
        try:
            async with self.bot.db.lease(101):
                with self.assertRaises(Conflict):
                    await other.acquire(101)
            token = await other.acquire(101)
            self.assertTrue(token)
        finally:
            await other.close()

    async def test_expired_lease_and_token_ownership(self):
        token = await self.bot.db.acquire(101)
        await self.bot.db.coord.execute("UPDATE leases SET expires=0 WHERE guild=101")
        await self.bot.db.coord.execute("UPDATE locks SET expires=0 WHERE guild=101")
        other = await self.bot.db.acquire(101)
        await self.bot.db.coord.execute("DELETE FROM leases WHERE guild=101 AND token=?", (token,))
        self.assertEqual((await self.bot.db.coord.query("SELECT token FROM leases WHERE guild=101"))[0]["token"], other)

    async def test_operational_data_not_shared(self):
        await self.bot.db.case(101, 601, 602, "warn", "test")
        names = await self.bot.db.coord.query("SELECT name FROM sqlite_master WHERE type='table'")
        self.assertNotIn("cases", {r["name"] for r in names})
        self.assertNotIn("tickets", {r["name"] for r in names})

    async def test_cases_idempotent(self):
        first = await self.bot.db.case(101, 601, 602, "warn", "test", event_key="msg:7")
        second = await self.bot.db.case(101, 601, 602, "warn", "test", event_key="msg:7")
        self.assertEqual(first[0], second[0])
        self.assertFalse(second[1])

    async def test_warning_revocation_and_failed_exclusion(self):
        case, _ = await self.bot.db.case(101, 601, 602, "warn", "test")
        await self.bot.db.case(101, 601, 602, "warn", "test", status="failed")
        self.assertEqual(await self.bot.db.warning_count(101, 601, 30), 1)
        await self.bot.db.revoke(101, case, 603)
        self.assertEqual(await self.bot.db.warning_count(101, 601, 30), 0)

    async def test_warning_expiry(self):
        case, _ = await self.bot.db.case(101, 601, 602, "warn", "test")
        await self.bot.db.execute("UPDATE cases SET at=0 WHERE id=?", (case,))
        self.assertEqual(await self.bot.db.warning_count(101, 601, 30), 0)

    async def test_atomic_ticket_reservation(self):
        results = await asyncio.gather(*(self.bot.db.reserve_ticket(101, 601, "packs", "subject", "details") for _ in range(3)), return_exceptions=True)
        self.assertEqual(sum(not isinstance(v, Exception) for v in results), 1)

    async def test_cooldown_concurrent_and_restart(self):
        values = await asyncio.gather(*(self.bot.db.cooldown(101, 601, "chat:305", 100) for _ in range(3)), return_exceptions=True)
        self.assertEqual(sum(not isinstance(v, Exception) for v in values), 1)
        await self.bot.db.close()
        await self.bot.db.open()
        with self.assertRaises(ValueError):
            await self.bot.db.cooldown(101, 601, "chat:305", 100)

    async def test_heat_decay(self):
        self.assertEqual(await self.bot.db.add_heat(101, 601, 5, 1), 5)
        await self.bot.db.execute("UPDATE heat SET at=? WHERE guild=101", (time.time() - 600,))
        self.assertEqual(await self.bot.db.add_heat(101, 601, 2, 1), 2)

    async def test_activity_duplicate_and_opt_in_previews(self):
        cfg = (await self.bot.db.settings(101))[0]["monitoring"]
        for _ in range(2):
            await self.bot.db.activity_message(101, 601, 305, 999, "private", cfg)
        self.assertEqual((await self.bot.db.query("SELECT count FROM activity"))[0]["count"], 1)
        self.assertEqual(await self.bot.db.query("SELECT * FROM previews"), [])
        cfg["store_previews"] = True
        for n in range(8):
            await self.bot.db.activity_message(101, 601, 305, 1000 + n, "text", cfg)
        self.assertEqual(len(await self.bot.db.query("SELECT * FROM previews")), cfg["preview_count"])

    async def test_transaction_rolls_back(self):
        def bad(conn):
            conn.execute("INSERT INTO audit(guild,area) VALUES(101,'rollback')")
            raise RuntimeError("injected")
        with self.assertRaises(RuntimeError):
            await self.bot.db.run(bad)
        self.assertFalse(await self.bot.db.query("SELECT * FROM audit WHERE area='rollback'"))

    async def test_sql_injection_is_data(self):
        malicious = "'); DROP TABLE tickets; --"
        await self.bot.db.audit(101, 603, "test", malicious)
        self.assertEqual((await self.bot.db.query("SELECT detail FROM audit WHERE area='test'"))[0]["detail"], malicious)
        self.assertEqual(await self.bot.db.query("SELECT COUNT(*) AS n FROM tickets"), [{"n": 0}])

    async def test_separate_paths_required(self):
        with self.assertRaises(ValueError):
            Database(self.bot.db.path, self.bot.db.path)

    async def test_resource_identity_unique(self):
        await self.bot.db.coord.execute("INSERT INTO resources VALUES(101,'channel:ticket',304,'text','ripcars-gate','{}','{}','active')")
        rows = await self.bot.db.resources(101)
        self.assertEqual(rows["channel:ticket"]["object_id"], 304)
        self.assertEqual(rows["channel:ticket"]["baseline"], {})

    async def test_retention_preserves_pending(self):
        await self.bot.db.case(101, 601, 602, "timeout", "unknown", "pending")
        await self.bot.db.case(101, 601, 602, "warn", "old", "done")
        await self.bot.db.execute("UPDATE cases SET at=0")
        await self.bot.db.prune(101, (await self.bot.db.settings(101))[0]["monitoring"])
        self.assertEqual([r["status"] for r in await self.bot.db.query("SELECT * FROM cases")], ["pending"])

    async def test_cancelled_transaction_completes_before_next(self):
        def slow(conn):
            time.sleep(0.05)
            conn.execute("INSERT INTO audit(guild,area) VALUES(101,'cancelled')")
        task = asyncio.create_task(self.bot.db.run(slow))
        await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(await self.bot.db.query("SELECT * FROM audit WHERE area='cancelled'")), 1)

    async def test_settings_persist(self):
        cfg, revision = await self.bot.db.settings(101)
        cfg["texts"]["ticket_title"] = "Rip Cars | Garage help"
        await self.bot.db.save(101, cfg, revision, 603)
        await self.bot.db.close()
        await self.bot.db.open()
        self.assertEqual((await self.bot.db.settings(101))[0]["texts"]["ticket_title"], cfg["texts"]["ticket_title"])
        self.assertEqual(json.loads((await self.bot.db.query("SELECT body FROM crew_settings"))[0]["body"]), cfg)
