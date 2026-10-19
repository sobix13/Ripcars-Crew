import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord.webhook.async_ import async_context

from ripcars_crew.bot import CrewBot
from tests.fakes import Guild


class CommandDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bot = CrewBot(Path(self.temp.name)/"crew.db", Path(self.temp.name)/"coord.db", sync=False)
        await self.bot.__aenter__()
        self.bot._connection.user = discord.ClientUser(state=self.bot._connection, data={"id":"501","username":"Crew","discriminator":"0","avatar":None,"bot":True})
        await self.bot.setup_hook()
        self.guild = Guild()
        cfg, revision = await self.bot.db.settings(101)
        cfg.update(moderator_roles=[202], member_role=201, log_channel=303)
        await self.bot.db.save(101, cfg, revision, 603)
        self.acknowledged = asyncio.Event()
        async def respond(*args, **kwargs):
            self.acknowledged.set()
            return {"interaction":{"id":str(args[0])}}
        self.adapter = SimpleNamespace(create_interaction_response=AsyncMock(side_effect=respond))
        self.context_token = async_context.set(self.adapter)
        self.webhook_patch = patch.object(discord.Webhook, "send", new_callable=AsyncMock)
        self.followup = self.webhook_patch.start()

    async def asyncTearDown(self):
        self.webhook_patch.stop()
        async_context.reset(self.context_token)
        await self.bot.__aexit__(None, None, None)
        self.temp.cleanup()

    def interaction(self, name, user=None, kind=2):
        i = discord.Interaction(state=self.bot._connection, data={
            "id":str(discord.utils.time_snowflake(discord.utils.utcnow())),
            "application_id":"501", "type":kind, "token":"test-only-interaction-token",
            "version":1, "attachment_size_limit":10000000,
            "data":{"name":"crew", "type":1, "options":[{"name":name,"type":1}]},
        })
        i.guild_id, i.user, i.channel = 101, user or self.guild.administrator, self.guild.general
        return i

    def missing(self, code=10062):
        return discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), {"code":code,"message":"Unknown interaction"})

    async def test_commands_acknowledge_before_waiting_for_real_sqlite_writer(self):
        for name in ("panel", "doctor", "health"):
            with self.subTest(command=name):
                self.acknowledged.clear()
                self.adapter.create_interaction_response.reset_mock()
                self.followup.reset_mock()
                writer = sqlite3.connect(self.bot.db.path)
                writer.execute("BEGIN IMMEDIATE")
                i = self.interaction(name, self.guild.staff if name == "health" else None)
                task = asyncio.create_task(self.bot.tree._call(i))
                try:
                    await asyncio.wait_for(self.acknowledged.wait(), timeout=0.5)
                    self.assertFalse(task.done(), "Database work should still be waiting for the writer")
                finally:
                    writer.rollback()
                    writer.close()
                    await asyncio.wait_for(task, timeout=3)
                self.assertTrue(i.response.is_done())
                self.adapter.create_interaction_response.assert_awaited_once()
                self.assertEqual(self.adapter.create_interaction_response.await_args.kwargs["params"].payload["data"]["flags"], 64)
                self.followup.assert_awaited_once()

    async def test_early_ack_does_not_authorize_nonadmin(self):
        i = self.interaction("panel", self.guild.user)
        with patch.object(self.bot.db, "settings", new_callable=AsyncMock) as settings:
            await self.bot.tree._call(i)
            settings.assert_not_awaited()
        self.followup.assert_awaited_once_with("This control is for server admins.", ephemeral=True, allowed_mentions=unittest.mock.ANY)

    async def test_modal_opener_is_not_deferred(self):
        await self.bot.tree._call(self.interaction("test-rule"))
        self.adapter.create_interaction_response.assert_awaited_once()
        self.assertEqual(self.adapter.create_interaction_response.await_args.kwargs["params"].payload["type"], 9)
        self.followup.assert_not_awaited()

    async def test_expired_ack_stops_command_without_executing_or_retrying(self):
        self.adapter.create_interaction_response.side_effect = self.missing()
        i = self.interaction("panel")
        with patch.object(i.command, "_callback", new_callable=AsyncMock) as action, patch.object(self.bot, "report", new_callable=AsyncMock) as report:
            with self.assertLogs("ripcarscrew", level="WARNING") as logs:
                await self.bot.tree._call(i)
            action.assert_not_awaited()
            report.assert_not_awaited()
        self.assertTrue(i.command_failed)
        self.assertIn("age_seconds=", " ".join(logs.output))
        self.assertNotIn(i.token, " ".join(logs.output))
        self.adapter.create_interaction_response.assert_awaited_once()
        self.followup.assert_not_awaited()

    async def test_expired_error_response_is_logged_without_second_failure(self):
        i = self.interaction("doctor")
        await i.response.defer(ephemeral=True)
        self.followup.side_effect = self.missing()
        with patch.object(self.bot, "report", new_callable=AsyncMock, return_value="TEST-ERROR") as report:
            with self.assertLogs("ripcarscrew", level="WARNING"):
                await self.bot.tree_error(i, RuntimeError("injected command failure"))
            report.assert_awaited_once()
        self.followup.assert_awaited_once()

    async def test_other_notfound_errors_still_reported(self):
        i = self.interaction("doctor")
        error = self.missing(10008)
        with patch.object(self.bot, "report", new_callable=AsyncMock, return_value="TEST-ERROR") as report:
            await self.bot.tree_error(i, error)
            report.assert_awaited_once_with(self.guild, "command", error)
        self.adapter.create_interaction_response.assert_awaited_once()

    async def test_autocomplete_is_not_deferred(self):
        i = self.interaction("doctor", kind=4)
        self.assertTrue(await self.bot.tree.interaction_check(i))
        self.adapter.create_interaction_response.assert_not_awaited()
