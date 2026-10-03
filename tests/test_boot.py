import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import discord

from ripcars_crew.bot import CrewBot


class BootTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_bot_setup_hook_and_graceful_shutdown(self):
        with tempfile.TemporaryDirectory() as td:
            bot = CrewBot(Path(td) / "crew.db", Path(td) / "coord.db", sync=False)
            async with bot:
                await bot.setup_hook()
                group = next(g for g in bot.tree.get_commands() if g.name == "crew")
                self.assertEqual(group.name, "crew")
                self.assertLessEqual(len(group.commands), 25)
                self.assertEqual(len(bot.persistent_views), 2)
                self.assertTrue(bot.intents.members)
                self.assertTrue(bot.intents.message_content)
                cfg, _ = await bot.db.settings(101)
                self.assertFalse(cfg["guard"]["enabled"])
                self.assertFalse(cfg["tickets"]["enabled"])
                self.assertEqual(set(bot.background), {"monitor", "backups", "watchdog"})
            self.assertTrue(all(task.done() for task in bot.background.values()))
            self.assertIsNone(bot.db.conn)

    async def test_global_sync_mocked_real_command_tree(self):
        with tempfile.TemporaryDirectory() as td:
            bot = CrewBot(Path(td) / "crew.db", Path(td) / "coord.db")
            async with bot:
                bot._connection.application_id = 501
                bot.http.bulk_upsert_global_commands = AsyncMock(return_value=[])
                with patch.dict("os.environ", {"GUILD_ID": ""}):
                    await bot.setup_hook()
                self.assertEqual(bot.http.bulk_upsert_global_commands.await_count, 1)
                payload = bot.http.bulk_upsert_global_commands.await_args.kwargs["payload"]
                group = next(g for g in payload if g["name"] == "crew")
                self.assertLessEqual(len(group["options"]), 25)

    async def test_real_member_lock_reuses_live_lock(self):
        with tempfile.TemporaryDirectory() as td:
            bot = CrewBot(Path(td) / "crew.db", Path(td) / "coord.db", sync=False)
            async with bot:
                lock = bot.member_lock(1, 2)
                self.assertIs(bot.member_lock(1, 2), lock)
                self.assertIsNot(bot.member_lock(1, 3), lock)
                self.assertFalse(bot.intents.presences)
                self.assertFalse(discord.Permissions.none().administrator)
