import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from ripcars_crew.native import keywords, snapshot
from tests.fakes import AsyncFixture


def native_rule(guild, bot_id=501, **kwargs):
    data = {"id": "9001", "guild_id": str(guild.id), "name": "Rip Cars | Crew word filter", "creator_id": str(bot_id),
            "event_type": 1, "trigger_type": 1, "trigger_metadata": {"keyword_filter": ["badword"]},
            "actions": [{"type": 1, "metadata": {"custom_message": "Please rephrase"}}],
            "enabled": True, "exempt_roles": ["202"], "exempt_channels": []}
    data.update(kwargs)
    return discord.AutoModRule(data=data, state=SimpleNamespace(), guild=guild)


class NativeTests(AsyncFixture, unittest.IsolatedAsyncioTestCase):
    async def configure(self):
        cfg, revision = await self.bot.db.settings(101)
        cfg["guard"]["words"] = [{"text": "badword", "mode": "exact"}]
        cfg["native"]["enabled"] = True
        await self.bot.db.save(101, cfg, revision, 603)

    async def test_real_sdk_payload_and_single_owned_rule(self):
        await self.configure()
        rule = native_rule(self.guild)
        self.guild.create_automod_rule.return_value = rule
        await self.bot.native.sync(self.guild, 603)
        options = self.guild.create_automod_rule.await_args.kwargs
        self.assertEqual(options["trigger"].to_metadata_dict()["keyword_filter"], ["badword"])
        self.assertEqual(options["actions"][0].to_dict()["type"], 1)
        self.assertEqual((await self.bot.db.query("SELECT rule FROM native"))[0]["rule"], 9001)

    async def test_manual_rule_edit_preserved(self):
        await self.configure()
        rule = native_rule(self.guild)
        self.guild.create_automod_rule.return_value = rule
        await self.bot.native.sync(self.guild, 603)
        rule.name = "Manually changed"
        self.guild.fetch_automod_rule.return_value = rule
        with self.assertRaises(ValueError):
            await self.bot.native.sync(self.guild, 603)
        self.assertEqual((await self.bot.db.query("SELECT state FROM native"))[0]["state"], "manual")

    async def test_disable_edits_only_own_rule(self):
        await self.configure()
        rule = native_rule(self.guild)
        self.guild.create_automod_rule.return_value = rule
        await self.bot.native.sync(self.guild, 603)
        self.guild.fetch_automod_rule.return_value = rule
        cfg, rev = await self.bot.db.settings(101)
        cfg["native"]["enabled"] = False
        await self.bot.db.save(101, cfg, rev, 603)
        disabled = native_rule(self.guild, enabled=False)
        with patch.object(discord.AutoModRule, "edit", AsyncMock(return_value=disabled)) as edit:
            await self.bot.native.sync(self.guild, 603)
            self.assertFalse(edit.await_args.kwargs["enabled"])
        self.guild.create_automod_rule.assert_awaited_once()

    async def test_foreign_creator_never_adopted(self):
        await self.configure()
        self.guild.create_automod_rule.return_value = native_rule(self.guild)
        await self.bot.native.sync(self.guild, 603)
        self.guild.fetch_automod_rule.return_value = native_rule(self.guild, bot_id=888)
        with self.assertRaises(ValueError):
            await self.bot.native.sync(self.guild, 603)

    async def test_contains_and_exact_mapping(self):
        cfg, _ = await self.bot.db.settings(101)
        cfg["guard"]["words"] = [{"text": "word", "mode": "exact"}, {"text": "phrase", "mode": "contains"}, {"text": "not*native", "mode": "exact"}]
        self.assertEqual(keywords(cfg), ["word", "*phrase*"])

    async def test_disabled_default_creates_nothing(self):
        await self.bot.native.sync(self.guild, 603)
        self.guild.create_automod_rule.assert_not_awaited()

    async def test_snapshot_uses_real_sdk(self):
        state = snapshot(native_rule(self.guild))
        self.assertEqual(state["trigger"]["type"], 1)
        self.assertTrue(state["enabled"])

    async def test_include_only_scope_not_silently_widened(self):
        await self.configure()
        cfg, rev = await self.bot.db.settings(101)
        cfg["guard"]["include_channels"] = [305]
        await self.bot.db.save(101, cfg, rev, 603)
        with self.assertRaises(ValueError):
            await self.bot.native.sync(self.guild, 603)
        self.guild.create_automod_rule.assert_not_awaited()
