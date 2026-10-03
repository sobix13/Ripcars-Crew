from __future__ import annotations

import asyncio
import logging
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from ripcars_crew.coordination import TemporaryControls
from ripcars_crew.moderation import Moderation
from ripcars_crew.native import NativeRules
from ripcars_crew.operations import Reporter
from ripcars_crew.storage import Database
from ripcars_crew.tickets import Tickets


class Role:
    def __init__(self, role_id, name, position=1, perms=None, managed=False):
        self.id, self.name, self.position, self.managed = role_id, name, position, managed
        self.permissions = perms or discord.Permissions.none()

    def __le__(self, other):
        return self.position <= other.position


class Member:
    def __init__(self, member_id, guild, roles, bot=False, perms=None, name="Member"):
        self.id, self.guild, self.roles, self.bot = member_id, guild, roles, bot
        self.guild_permissions = perms or discord.Permissions.none()
        self.top_role = max(roles, key=lambda r: r.position)
        self.display_name = name
        self.created_at = datetime.now(timezone.utc)
        self.joined_at = self.created_at
        self.send, self.timeout, self.kick = AsyncMock(), AsyncMock(), AsyncMock()


class Channel:
    @property
    def overwrites(self):
        return self._test_overwrites

    @overwrites.setter
    def overwrites(self, value):
        self._test_overwrites = value

    @property
    def category(self):
        return self._test_category

    @category.setter
    def category(self, value):
        self._test_category = value

    @property
    def category_id(self):
        return self._test_category_id

    @category_id.setter
    def category_id(self, value):
        self._test_category_id = value

    def __init__(self, channel_id, guild, name="channel", overwrites=None, category=None):
        self.id, self.guild, self.name = channel_id, guild, name
        self.overwrites = dict(overwrites or {})
        self.category_id = category.id if category else None
        self.category = category
        self.slowmode_delay = 0
        self.parent_id = None
        self.parent = None
        self.send = AsyncMock(side_effect=self._send)
        self.edit = AsyncMock(side_effect=self._edit)
        self.delete = AsyncMock(side_effect=self._delete)
        self.fetch_message = AsyncMock(side_effect=self._fetch)
        self.purge = AsyncMock(return_value=[])
        self.messages = {}
        self.history_rows = []

    async def _send(self, **kwargs):
        message = SimpleNamespace(id=10000 + len(self.messages), edit=AsyncMock())
        self.messages[message.id] = message
        return message

    async def _edit(self, **kwargs):
        if "overwrites" in kwargs:
            self.overwrites = kwargs["overwrites"]
        if "category" in kwargs:
            self.category = kwargs["category"]
            self.category_id = self.category.id
        if "slowmode_delay" in kwargs:
            self.slowmode_delay = kwargs["slowmode_delay"]
        return self

    async def _delete(self, **kwargs):
        self.guild.channels.pop(self.id, None)

    async def _fetch(self, message_id):
        if message_id not in self.messages:
            raise discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "missing")
        return self.messages[message_id]

    def permissions_for(self, target):
        perms = discord.Permissions.none()
        roles = [target] if isinstance(target, Role) else target.roles
        for role in [self.guild.default_role] + roles:
            perms.value |= role.permissions.value
        if perms.administrator:
            return discord.Permissions.all()
        default = self.overwrites.get(self.guild.default_role)
        if default:
            allow, deny = default.pair()
            perms.handle_overwrite(allow.value, deny.value)
        allows = denies = 0
        for role in roles:
            if role.id == self.guild.default_role.id:
                continue
            overwrite = self.overwrites.get(role)
            if overwrite:
                allow, deny = overwrite.pair()
                allows |= allow.value
                denies |= deny.value
        perms.handle_overwrite(allows, denies)
        if not isinstance(target, Role) and target in self.overwrites:
            allow, deny = self.overwrites[target].pair()
            perms.handle_overwrite(allow.value, deny.value)
        return perms

    async def history(self, **kwargs):
        for row in self.history_rows[:kwargs.get("limit", len(self.history_rows))]:
            yield row


class Category(Channel, discord.CategoryChannel):
    pass


class Guild:
    def __init__(self):
        self.id, self.owner_id = 101, 999
        self.default_role = Role(101, "@everyone", 0)
        self.rippers = Role(201, "Rippers", 1)
        self.mod = Role(202, "Moderator", 10, discord.Permissions(manage_messages=True, moderate_members=True, kick_members=True, ban_members=True, manage_channels=True))
        self.admin = Role(203, "Admin", 30, discord.Permissions(administrator=True))
        self.bot_role = Role(204, "Crew", 50, discord.Permissions.all(), managed=True)
        self.roles = [self.default_role, self.rippers, self.mod, self.admin, self.bot_role]
        self.me = Member(501, self, [self.bot_role], True, discord.Permissions.all())
        self.user = Member(601, self, [self.rippers])
        self.staff = Member(602, self, [self.mod], perms=self.mod.permissions)
        self.administrator = Member(603, self, [self.admin], perms=self.admin.permissions)
        self.members = {m.id: m for m in (self.me, self.user, self.staff, self.administrator)}
        private = {self.default_role: discord.PermissionOverwrite(view_channel=False), self.mod: discord.PermissionOverwrite(view_channel=True, read_message_history=True), self.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True)}
        self.open_category = Category(301, self, "Open", private)
        self.closed_category = Category(302, self, "Closed", private)
        self.log = Channel(303, self, "crew-log", private)
        self.panel = Channel(304, self, "ticket", {self.default_role: discord.PermissionOverwrite(view_channel=False), self.rippers: discord.PermissionOverwrite(view_channel=True)})
        self.general = Channel(305, self, "general", {self.rippers: discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=True)})
        self.channels = {c.id: c for c in (self.open_category, self.closed_category, self.log, self.panel, self.general)}
        self.create_text_channel = AsyncMock(side_effect=self._create)
        self.create_category = AsyncMock(side_effect=self._category)
        self.fetch_member = AsyncMock(side_effect=lambda uid: self.members[uid])
        self.ban = AsyncMock()
        self.fetch_automod_rule = AsyncMock()
        self.create_automod_rule = AsyncMock()

    @property
    def text_channels(self):
        return [c for c in self.channels.values() if not isinstance(c, Category)]

    @property
    def categories(self):
        return [c for c in self.channels.values() if isinstance(c, Category)]

    def get_role(self, role_id):
        return next((r for r in self.roles if r.id == role_id), None)

    def get_member(self, member_id):
        return self.members.get(member_id)

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)

    async def _create(self, name, **kwargs):
        channel = Channel(max(self.channels) + 1, self, name, kwargs.get("overwrites"), kwargs.get("category"))
        self.channels[channel.id] = channel
        return channel

    async def _category(self, name, **kwargs):
        channel = Category(max(self.channels) + 1, self, name, kwargs.get("overwrites"))
        self.channels[channel.id] = channel
        return channel


class Bot:
    def __init__(self, path):
        self.db = Database(Path(path) / "crew.sqlite3", Path(path) / "coordination.sqlite3")
        self.guild = Guild()
        self.user = self.guild.me
        self.logger = logging.getLogger("test")
        self.reporter = Reporter(self)
        self.report = AsyncMock(side_effect=self.reporter.error)
        self._locks = defaultdict(asyncio.Lock)
        self.moderation, self.tickets = Moderation(self), Tickets(self)
        self.controls, self.native = TemporaryControls(self), NativeRules(self)
        self.intents = discord.Intents.all()
        self.background = {}
        self.version, self.latency = "1.0.0", float("nan")
        self.watchdog = SimpleNamespace(enabled=False)
        self.last_monitor = self.last_backup = None
        self.take_backup = AsyncMock()

    async def open(self, enabled=True):
        await self.db.open()
        cfg, revision = await self.db.settings(self.guild.id)
        cfg.update(moderator_roles=[202], member_role=201, log_channel=303)
        cfg["tickets"].update(panel_channel=304, open_category=301, closed_category=302, enabled=enabled, create_cooldown_seconds=0)
        cfg["guard"]["enabled"] = enabled
        await self.db.save(self.guild.id, cfg, revision, 603)

    def member_lock(self, guild, user):
        return self._locks[(guild, user)]

    def get_guild(self, guild):
        return self.guild if self.guild.id == guild else None

    def is_ready(self):
        return True


def interaction(bot, user=None, channel=None):
    i = SimpleNamespace(client=bot, guild=bot.guild, guild_id=bot.guild.id, user=user or bot.guild.administrator, channel=channel or bot.guild.general)
    i.channel_id = i.channel.id
    i.response = SimpleNamespace(is_done=lambda: False, send_message=AsyncMock(), edit_message=AsyncMock(), send_modal=AsyncMock(), defer=AsyncMock())
    i.followup = SimpleNamespace(send=AsyncMock())
    return i


def message(bot, content="hello", message_id=701, user=None, channel=None):
    return SimpleNamespace(guild=bot.guild, author=user or bot.guild.user, channel=channel or bot.guild.general, id=message_id,
                           content=content, mentions=[], role_mentions=[], attachments=[], webhook_id=None,
                           delete=AsyncMock(), created_at=datetime.now(timezone.utc))


class AsyncFixture:
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bot = Bot(self.temp.name)
        await self.bot.open()
        self.guild = self.bot.guild

    async def asyncTearDown(self):
        await self.bot.db.close()
        self.temp.cleanup()
