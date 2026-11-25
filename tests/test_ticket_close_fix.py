import copy
import hashlib
import os
import pwd
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import discord

import ripcars_crew.tickets as baseline
from ripcars_crew.storage import Conflict
from tests.fakes import Channel, Category
import tests.test_tickets as existing_ticket_tests
from maintenance.ripcars_crew_ticket_fix import patched_source, method, BACKUP_CODE
import maintenance.ripcars_crew_ticket_fix as installer


def fixture_source():
    # The deployed 1.1.1 methods supplied in the incident retain the 1.1.0
    # structure and add per-ticket open_category and a fourth scope argument.
    source = Path(baseline.__file__).read_text()
    source = source.replace(
        '            pending = "closing" if close else "reopening"\n',
        '            category_id = cfg["tickets"]["closed_category"] if close else (ticket["open_category"] or cfg["tickets"]["open_category"])\n'
        '            await check_ticket_scope(self.bot, guild, cfg, (category_id,))\n'
        '            pending = "closing" if close else "reopening"\n')
    source = source.replace(
        '            category = guild.get_channel(cfg["tickets"]["closed_category" if close else "open_category"])\n',
        '            category = guild.get_channel(category_id)\n')
    return source


FIXTURE_SOURCE = fixture_source()
MODULE = types.ModuleType('ripcars_crew.ticket_hotfix_fixture')
MODULE.__package__ = 'ripcars_crew'
exec(compile(patched_source(FIXTURE_SOURCE), '<ticket-hotfix>', 'exec'), MODULE.__dict__)
NATIVE_SCOPE = MODULE.check_ticket_scope


async def scope(bot, guild, cfg, extra=()):
    await NATIVE_SCOPE(bot, guild, cfg)
    for category_id in extra:
        if category_id not in (cfg['tickets']['open_category'], cfg['tickets']['closed_category']):
            selected = copy.deepcopy(cfg)
            selected['tickets']['open_category'] = category_id
            await NATIVE_SCOPE(bot, guild, selected)


class PatchedTicketTests(existing_ticket_tests.TicketTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.bot.db.execute('ALTER TABLE tickets ADD COLUMN open_category INTEGER DEFAULT 0')
        self.bot.tickets = MODULE.Tickets(self.bot)
        MODULE.check_ticket_scope = scope
        self.guild.fetch_channels = AsyncMock(side_effect=lambda: list(self.guild.channels.values()))

        async def fetch(channel_id):
            result = self.guild.get_channel(channel_id)
            if result is None:
                raise discord.NotFound(types.SimpleNamespace(status=404, reason='Not Found'), 'missing')
            return result
        self.guild.fetch_channel = AsyncMock(side_effect=fetch)

    async def mark_pending(self, channel, state='closing', previous='open'):
        await self.bot.db.execute('UPDATE tickets SET status=?,operation=?,last_error=? WHERE channel=?',
            (state, previous, 'HTTPException: verify Discord state before retry', channel.id))

    def fill(self, category, count):
        for index in range(count):
            channel = Channel(200000 + index, self.guild, category=category)
            self.guild.channels[channel.id] = channel

    async def test_full_category_keeps_open_state_and_never_sends_patch(self):
        channel = await self.open_ticket()
        self.fill(self.guild.closed_category, 50)
        with self.assertRaisesRegex(ValueError, 'full'):
            await self.bot.tickets.transition(self.guild, channel, self.guild.staff)
        row = await self.bot.db.ticket(self.guild.id, channel.id)
        self.assertEqual((row['status'], row['operation']), ('open', ''))
        channel.edit.assert_not_awaited()

    async def test_capacity_race_invalid_form_restores_open_and_keeps_detail(self):
        channel = await self.open_ticket()
        channel.edit.side_effect = discord.HTTPException(
            types.SimpleNamespace(status=400, reason='Bad Request'),
            {'code': 50035, 'message': 'Invalid Form Body: Maximum number of channels in category reached (50)'})
        with self.assertRaises(discord.HTTPException):
            await self.bot.tickets.transition(self.guild, channel, self.guild.staff)
        row = await self.bot.db.ticket(self.guild.id, channel.id)
        self.assertEqual((row['status'], row['operation']), ('open', ''))
        self.assertIn('50035', row['last_error'])

    async def test_pending_open_channel_restored_then_closes_and_deletes(self):
        channel = await self.open_ticket()
        await self.mark_pending(channel)
        result = await self.bot.tickets.repair(self.guild, self.guild.administrator.id)
        self.assertIn('verified open', result[0])
        channel.edit.assert_not_awaited()
        self.assertEqual((await self.bot.db.ticket(self.guild.id, channel.id))['status'], 'open')
        await self.bot.tickets.transition(self.guild, channel, self.guild.staff)
        await self.bot.tickets.delete(self.guild, channel, self.guild.administrator.id)
        self.assertEqual((await self.bot.db.ticket(self.guild.id, channel.id))['status'], 'deleted')

    async def test_repair_completes_actual_close_and_disables_auto_delete(self):
        channel = await self.open_ticket()
        await self.bot.tickets.transition(self.guild, channel, self.guild.staff)
        await self.mark_pending(channel)
        await self.bot.db.execute('UPDATE tickets SET closed=0,delete_at=1 WHERE channel=?', (channel.id,))
        result = await self.bot.tickets.repair(self.guild, self.guild.administrator.id)
        row = await self.bot.db.ticket(self.guild.id, channel.id)
        self.assertIn('verified closed', result[0])
        self.assertEqual((row['status'], row['operation'], row['delete_at']), ('closed', '', 0))
        self.assertGreater(row['closed'], 0)

    async def test_repair_restores_closed_after_failed_reopen(self):
        channel = await self.open_ticket()
        await self.bot.tickets.transition(self.guild, channel, self.guild.staff)
        await self.mark_pending(channel, 'reopening', 'closed')
        result = await self.bot.tickets.repair(self.guild, self.guild.administrator.id)
        self.assertIn('verified closed', result[0])
        self.assertEqual((await self.bot.db.ticket(self.guild.id, channel.id))['status'], 'closed')

    async def test_custom_topic_open_category_is_preserved(self):
        channel = await self.open_ticket()
        custom = Category(777, self.guild, 'Claim a prize', dict(self.guild.open_category.overwrites))
        self.guild.channels[custom.id] = custom
        channel.category = custom
        channel.category_id = custom.id
        await self.bot.db.execute('UPDATE tickets SET open_category=? WHERE channel=?', (custom.id, channel.id))
        await self.mark_pending(channel)
        result = await self.bot.tickets.repair(self.guild, self.guild.administrator.id)
        self.assertIn('verified open', result[0])
        self.assertEqual(channel.category_id, custom.id)

    async def test_repair_uses_fresh_channel_and_preserves_ambiguous_record(self):
        channel = await self.open_ticket()
        await self.mark_pending(channel)
        fresh = Channel(channel.id, self.guild, overwrites=dict(channel.overwrites), category=self.guild.open_category)
        fresh.overwrites[self.guild.user] = discord.PermissionOverwrite(view_channel=True, send_messages=False)
        self.guild.fetch_channel = AsyncMock(return_value=fresh)
        result = await self.bot.tickets.repair(self.guild, self.guild.administrator.id)
        self.assertIn('unresolved', result[0])
        self.assertEqual((await self.bot.db.ticket(self.guild.id, channel.id))['status'], 'closing')
        fresh.edit.assert_not_awaited()

    async def test_fetch_failure_preserves_pending_record(self):
        channel = await self.open_ticket()
        await self.mark_pending(channel)
        self.guild.fetch_channel.side_effect = discord.Forbidden(
            types.SimpleNamespace(status=403, reason='Forbidden'), 'missing access')
        await self.bot.tickets.repair(self.guild, self.guild.administrator.id)
        self.assertEqual((await self.bot.db.ticket(self.guild.id, channel.id))['status'], 'closing')

    async def test_repair_shares_operation_mutex(self):
        channel = await self.open_ticket()
        await self.mark_pending(channel)
        async with self.bot.db.lease(self.guild.id):
            with self.assertRaises(Conflict):
                await self.bot.tickets.repair(self.guild, self.guild.administrator.id)
        self.guild.fetch_channel.assert_not_awaited()

    async def test_member_lookup_failure_happens_before_pending_write(self):
        channel = await self.open_ticket()
        self.guild.members.pop(self.guild.user.id)
        self.guild.fetch_member.side_effect = discord.Forbidden(
            types.SimpleNamespace(status=403, reason='Forbidden'), 'missing access')
        with self.assertRaises(discord.Forbidden):
            await self.bot.tickets.transition(self.guild, channel, self.guild.staff)
        self.assertEqual((await self.bot.db.ticket(self.guild.id, channel.id))['status'], 'open')

    async def test_missing_channel_not_recreated(self):
        channel = await self.open_ticket()
        await self.mark_pending(channel)
        self.guild.channels.pop(channel.id)
        await self.bot.tickets.repair(self.guild, self.guild.administrator.id)
        self.assertEqual((await self.bot.db.ticket(self.guild.id, channel.id))['status'], 'missing')
        self.guild.create_text_channel.assert_awaited_once()


class SourceTests(unittest.TestCase):
    def test_only_transition_and_repair_change(self):
        updated = patched_source(FIXTURE_SOURCE)
        for name in ('open', 'card', 'update_controls', 'claim', 'transcript', 'delete', 'sweep'):
            try:
                old = method(FIXTURE_SOURCE, name)[1]
            except ValueError:
                continue
            self.assertEqual(method(updated, name)[1], old)
        self.assertIn('ticket["open_category"]', updated)

    def test_unknown_source_pattern_is_rejected(self):
        with self.assertRaises(ValueError):
            patched_source(FIXTURE_SOURCE.replace('category = guild.get_channel(category_id)', 'category = None'))


class InstallerTests(unittest.TestCase):
    def run_installer(self, fail_preflight=False, wrong_hash=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = root / 'release'
            (release / 'ripcars_crew').mkdir(parents=True)
            (release / 'VERSION').write_text('1.1.1\n')
            target = release / 'ripcars_crew/tickets.py'
            target.write_text(FIXTURE_SOURCE)
            original = target.read_bytes()
            source_db = root / 'crew.sqlite3'
            with sqlite3.connect(source_db) as db:
                db.execute('CREATE TABLE proof(value)')
                db.execute('INSERT INTO proof VALUES(42)')
            account = pwd.getpwuid(os.geteuid())
            calls = []
            real_run = subprocess.run

            def command(args, **kwargs):
                calls.append(args)
                if args[:2] == ['systemctl', 'is-active']:
                    return subprocess.CompletedProcess(args, 0)
                if args[0] == 'runuser' and '-c' in args:
                    return real_run([sys.executable, '-c', BACKUP_CODE, *args[-2:]], check=True)
                if args[0] == 'runuser' and '--code-only' in args:
                    compile(target.read_text(), str(target), 'exec')
                    if fail_preflight:
                        raise subprocess.CalledProcessError(1, args)
                return subprocess.CompletedProcess(args, 0)

            with patch.multiple(installer, ROOT=release, CODE_BACKUPS=root / 'code-backups',
                DATA_BACKUPS=root / 'data-backups', DATABASE=source_db,
                EXPECTED_SHA256='wrong' if wrong_hash else hashlib.sha256(original).hexdigest()), \
                patch.object(installer.os, 'geteuid', return_value=0), \
                patch.object(installer.pwd, 'getpwnam', return_value=account), \
                patch.object(installer.subprocess, 'run', side_effect=command):
                if wrong_hash:
                    with self.assertRaises(SystemExit):installer.apply()
                    self.assertEqual(target.read_bytes(), original)
                    self.assertEqual(calls, [])
                    return
                if fail_preflight:
                    with self.assertRaises(subprocess.CalledProcessError):installer.apply()
                    self.assertEqual(target.read_bytes(), original)
                else:
                    installer.apply()
                    self.assertTrue(target.read_text().startswith(installer.MARKER))
                copies = list((root / 'code-backups').glob('*/tickets.py'))
                self.assertEqual(len(copies), 1)
                self.assertEqual(copies[0].read_bytes(), original)
                backups = list((root / 'data-backups').glob('*.sqlite3'))
                self.assertEqual(len(backups), 1)
                with sqlite3.connect(backups[0]) as db:
                    self.assertEqual(db.execute('PRAGMA quick_check').fetchone(), ('ok',))
                    self.assertEqual(db.execute('SELECT * FROM proof').fetchone(), (42,))
                with sqlite3.connect(source_db) as db:
                    self.assertEqual(db.execute('SELECT * FROM proof').fetchone(), (42,))
                self.assertIn(['systemctl', 'start', 'ripcars-crew.service'], calls)
                self.assertFalse(any('maestro' in str(call) or 'ripper-zone' in str(call) for call in calls))

    def test_successful_apply_backs_up_and_preserves_database(self):
        self.run_installer()

    def test_preflight_failure_restores_original_code_and_service(self):
        self.run_installer(fail_preflight=True)

    def test_unrecognized_live_file_is_not_modified(self):
        self.run_installer(wrong_hash=True)


if __name__ == '__main__':
    unittest.main()
