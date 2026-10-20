import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from ripcars_crew.bot import CrewBot


class AuditEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_role_audit_preserves_missing_and_uncached_target_ids(self):
        guild = SimpleNamespace(_state=None, get_member=lambda _: None,
                                get_role=lambda _: None)
        bot = SimpleNamespace(audit_event=AsyncMock())
        for target in (None, "90210"):
            with self.subTest(target=target):
                entry = discord.AuditLogEntry(
                    users={}, integrations={}, app_commands={},
                    automod_rules={}, webhooks={}, guild=guild,
                    data={"id": "12345", "action_type": 31,
                          "target_id": target, "user_id": "603"})
                await CrewBot.on_audit_log_entry_create(bot, entry)
                bot.audit_event.assert_awaited_with(
                    guild, "discord_audit", 603,
                    f"action={entry.action}; target={int(target) if target else None}")
