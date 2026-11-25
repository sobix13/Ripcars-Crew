#!/usr/bin/env python3
"""Targeted close/recovery repair for the inspected Ripcars Crew 1.1.1 file."""
from __future__ import annotations

import argparse
import ast
import hashlib
import os
import pwd
import shutil
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

EXPECTED_SHA256 = '84bdbecb9b16c4cd01c6a57a8f6c64806f1cf8463d19881cdc619c1e3ceaf8b9'
MARKER = '# Crew 1.1.1 ticket recovery hotfix 2026-11-25'
ROOT = Path('/opt/ripcars-crew/current')
SERVICE = 'ripcars-crew.service'
CODE_BACKUPS = Path('/var/backups/ripcars-crew-ticket-fix')
DATA_BACKUPS = Path('/var/lib/ripcars-crew/ticket-fix-backups')
DATABASE = Path('/var/lib/ripcars-crew/crew.sqlite3')

REPAIR = '''    async def repair(self, guild, actor):
        """Reconcile interrupted operations against a fresh Discord response."""
        outcomes = []
        async with self.bot.db.lease(guild.id, ttl=300) as token:
            cfg, _ = await self.bot.db.settings(guild.id)
            await check_ticket_scope(self.bot, guild, cfg)
            rows = await self.bot.db.query(
                "SELECT * FROM tickets WHERE guild=? AND status IN ('creating','closing','reopening')",
                (guild.id,))
            for ticket in rows:
                if ticket['status'] == 'creating':
                    outcomes.append(f"#{ticket['number']}: incomplete starter; manual review required")
                    continue
                source = 'open' if ticket['status'] == 'closing' else 'closed'
                if ticket['operation'] != source:
                    outcomes.append(f"#{ticket['number']}: operation metadata needs manual review")
                    continue
                open_id = ticket['open_category'] or cfg['tickets']['open_category']
                closed_id = cfg['tickets']['closed_category']
                await check_ticket_scope(self.bot, guild, cfg, (open_id, closed_id))
                try:
                    channel = await guild.fetch_channel(ticket['channel'])
                except discord.NotFound:
                    await self.bot.db.ensure_lease(guild.id, 'server-setup', token)
                    await self.bot.db.execute(
                        "UPDATE tickets SET status='missing',last_error='Channel confirmed missing by Discord' WHERE id=? AND status=? AND operation=?",
                        (ticket['id'], ticket['status'], ticket['operation']))
                    outcomes.append(f"#{ticket['number']}: channel confirmed missing; record preserved")
                    continue
                except discord.HTTPException as exc:
                    outcomes.append(f"#{ticket['number']}: Discord verification failed ({exc.status}); unchanged")
                    continue
                if channel.guild.id != guild.id or not hasattr(channel, 'overwrites'):
                    outcomes.append(f"#{ticket['number']}: channel identity needs manual review")
                    continue
                target = next((t for t in channel.overwrites if t.id == ticket['user']), None)
                overwrite = channel.overwrites.get(target) if target else None
                observed = None
                if overwrite:
                    if channel.category_id == open_id and overwrite.view_channel is True and overwrite.send_messages is True:
                        observed = 'open'
                    elif channel.category_id == closed_id and overwrite.view_channel is False and overwrite.send_messages is False:
                        observed = 'closed'
                if observed is None:
                    outcomes.append(f"#{ticket['number']}: unresolved; channel permissions unchanged")
                    continue
                await self.bot.db.ensure_lease(guild.id, 'server-setup', token)
                changed = await self.bot.db.execute(
                    "UPDATE tickets SET status=?,closed=?,operation='',last_error='',delete_at=0 WHERE id=? AND status=? AND operation=?",
                    (observed, (ticket['closed'] or time.time()) if observed == 'closed' else 0,
                     ticket['id'], ticket['status'], ticket['operation']))
                if not changed:
                    outcomes.append(f"#{ticket['number']}: record changed; retry review")
                    continue
                await self.bot.db.audit(guild.id, actor, 'ticket_reconcile',
                    f"ticket={ticket['id']}; {ticket['status']} -> {observed}; previous_error={ticket['last_error']}")
                await self.update_controls(guild, channel)
                outcomes.append(f"#{ticket['number']}: observed transition settled; verified {observed}; auto-delete cleared")
            await self.bot.db.audit(guild.id, actor, 'ticket_repair', '; '.join(outcomes))
        return outcomes or ['No incomplete tickets.']
'''


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError('The inspected source pattern differs. No patch was applied.')
    return source.replace(old, new, 1)


def method(source, name):
    tree = ast.parse(source)
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Tickets']
    if len(classes) != 1:
        raise ValueError('Expected one Tickets class.')
    matches = [n for n in classes[0].body if isinstance(n, ast.AsyncFunctionDef) and n.name == name]
    if len(matches) != 1:
        raise ValueError('Expected one Tickets.' + name)
    node = matches[0]
    lines = source.splitlines(keepends=True)
    return node, ''.join(lines[node.lineno - 1:node.end_lineno])


def patched_source(source):
    node, transition = method(source, 'transition')
    transition = replace_once(transition,
        '            if not ticket or ticket["status"] != expected:\n',
        '            if ticket and ticket["status"] in ("closing", "reopening"):\n'
        '                raise ValueError("This ticket has an interrupted operation. Ask an administrator to run Tickets > Review incomplete tickets.")\n'
        '            if not ticket or ticket["status"] != expected:\n')
    pending = ('            pending = "closing" if close else "reopening"\n'
               '            await self.bot.db.execute("UPDATE tickets SET status=?,operation=? WHERE id=?", (pending, expected, ticket["id"]))\n')
    transition = replace_once(transition, pending, '')
    transition = replace_once(transition,
        '                        await self.bot.db.execute("UPDATE tickets SET status=? WHERE id=?", (expected, ticket["id"]))\n', '')
    transition = replace_once(transition,
        '            category = guild.get_channel(category_id)\n',
        '            live_channels = await guild.fetch_channels()\n'
        '            category = next((c for c in live_channels if c.id == category_id), None)\n'
        '            if not isinstance(category, discord.CategoryChannel) or not private_channel(guild, category, cfg):\n'
        '                raise ValueError("The target ticket category is unavailable or is not staff-only.")\n'
        '            occupants = sum(1 for c in live_channels if getattr(c, "category_id", None) == category_id and c.id != channel.id)\n'
        '            if occupants >= 50:\n'
        '                raise ValueError("The target ticket category is full (50 channels). Ask staff to free archive space or configure another private category, then retry.")\n'
        + pending)
    transition = replace_once(transition,
        '                await self.bot.db.execute("UPDATE tickets SET last_error=? WHERE id=?", (type(exc).__name__ + ": verify Discord state before retry", ticket["id"]))\n',
        '                detail = redact(type(exc).__name__ + ": " + str(exc))[:500]\n'
        '                if isinstance(exc, discord.HTTPException) and exc.status == 400 and exc.code == 50035:\n'
        '                    await self.bot.db.execute("UPDATE tickets SET status=?,operation=\'\',last_error=? WHERE id=? AND status=? AND operation=?", (expected, detail, ticket["id"], pending, expected))\n'
        '                else:\n'
        '                    await self.bot.db.execute("UPDATE tickets SET last_error=? WHERE id=?", (detail, ticket["id"]))\n')
    repair_node, _ = method(source, 'repair')
    lines = source.splitlines(keepends=True)
    edits = [(node.lineno - 1, node.end_lineno, transition.rstrip() + '\n'),
             (repair_node.lineno - 1, repair_node.end_lineno, REPAIR)]
    for start, end, replacement in sorted(edits, reverse=True):
        lines[start:end] = [replacement]
    result = MARKER + '\n' + ''.join(lines)
    compile(result, 'tickets.py', 'exec')
    return result


BACKUP_CODE = '''import os, sqlite3, sys
from pathlib import Path
source, target = map(Path, sys.argv[1:])
if target.exists(): raise SystemExit('Backup destination already exists')
src = sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True, timeout=15)
dst = sqlite3.connect(target)
try:
    src.backup(dst)
    if dst.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
        raise RuntimeError('Database backup failed verification')
finally:
    dst.close()
    src.close()
target.chmod(0o600)
print('Verified database backup:', target)
'''


def atomic_write(path, data, original_stat):
    handle, name = tempfile.mkstemp(prefix='.crew-ticket-fix-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(handle, 'wb') as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.chown(temporary, original_stat.st_uid, original_stat.st_gid)
        temporary.chmod(stat.S_IMODE(original_stat.st_mode))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def apply():
    if os.geteuid() != 0:
        raise SystemExit('Run this repair as root.')
    if (ROOT / 'VERSION').read_text().strip() != '1.1.1':
        raise SystemExit('This targeted repair requires Crew 1.1.1. No changes made.')
    target = (ROOT / 'ripcars_crew/tickets.py').resolve(strict=True)
    data = target.read_bytes()
    if data.startswith((MARKER + '\n').encode()):
        print('This repair is already installed.')
        return
    if hashlib.sha256(data).hexdigest() != EXPECTED_SHA256:
        raise SystemExit('Installed tickets.py differs from the inspected 1.1.1 file. No changes made.')
    updated = patched_source(data.decode()).encode()
    metadata = target.stat()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S-%fZ')
    backup_dir = CODE_BACKUPS / stamp
    backup_dir.mkdir(parents=True, mode=0o700)
    backup_dir.parent.chmod(0o700)
    original = backup_dir / 'tickets.py'
    shutil.copy2(target, original)
    print('Original code backup:', original, flush=True)
    active = subprocess.run(['systemctl', 'is-active', '--quiet', SERVICE]).returncode == 0
    changed = False
    try:
        subprocess.run(['systemctl', 'stop', SERVICE], check=True, timeout=90)
        if (ROOT / 'ripcars_crew/tickets.py').resolve() != target or target.read_bytes() != data:
            raise RuntimeError('The running release changed during preparation. Repair cancelled.')
        account = pwd.getpwnam('ripcarscrew')
        db_dir = DATA_BACKUPS
        db_dir.mkdir(mode=0o700, exist_ok=True)
        os.chown(db_dir, account.pw_uid, account.pw_gid)
        db_dir.chmod(0o700)
        subprocess.run(['runuser', '-u', 'ripcarscrew', '--', '/usr/bin/python3', '-c',
            BACKUP_CODE, str(DATABASE), str(db_dir / (stamp + '.sqlite3'))],
            check=True, timeout=90)
        atomic_write(target, updated, metadata)
        changed = True
        subprocess.run(['runuser', '-u', 'ripcarscrew', '--', str(ROOT / '.venv/bin/python'),
            str(ROOT / 'scripts/preflight.py'), '--code-only'], check=True, timeout=90, cwd=ROOT)
        if active:
            print('Starting Ripcars Crew...', flush=True)
            subprocess.run(['systemctl', 'start', SERVICE], check=True, timeout=210)
        print('Ticket close/recovery repair installed. Crew version and topic configuration are preserved.')
        print('In Discord: /crew panel > Tickets > Review incomplete tickets > Confirm.')
        print('Free space in the closed-ticket category before retrying Close.')
    except BaseException:
        if changed:
            subprocess.run(['systemctl', 'stop', SERVICE], check=True, timeout=90)
            atomic_write(target, data, metadata)
            print('Original code restored.', flush=True)
        if active:
            subprocess.run(['systemctl', 'start', SERVICE], check=True, timeout=210)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Back up and apply the inspected repair')
    args = parser.parse_args()
    if args.apply:
        apply()
    else:
        parser.print_help()
