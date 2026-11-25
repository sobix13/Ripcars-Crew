#!/usr/bin/env python3
"""Fix the unknown-channel regression in the installed Crew 1.1.1 ticket hotfix."""
import argparse
import ast
import os
import shutil
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/opt/ripcars-crew/current')
BACKUPS = Path('/var/backups/ripcars-crew-ticket-fix')
SERVICE = 'ripcars-crew.service'
MARKER = '# Crew 1.1.1 ticket recovery hotfix 2026-11-25'
FIX_MARKER = '# Crew ticket capacity: tolerate unrelated unknown channel types'
REPLACEMENTS = (
    ('            live_channels = await guild.fetch_channels()\n'
     '            category = next((c for c in live_channels if c.id == category_id), None)\n',
     '            live_channels = await guild._state.http.get_all_guild_channels(guild.id)\n'
     '            category = await guild.fetch_channel(category_id)\n'),
    ('            occupants = sum(1 for c in live_channels if getattr(c, "category_id", None) == category_id and c.id != channel.id)\n',
     '            occupants = sum(1 for c in live_channels if int(c.get("parent_id") or 0) == category_id and int(c["id"]) != channel.id)\n'),
)


def patched_source(source):
    if not source.startswith(MARKER + '\n'):
        raise ValueError('The inspected ticket recovery hotfix is required. No changes made.')
    if FIX_MARKER in source:
        for _, after in REPLACEMENTS:
            if source.count(after) != 1:
                raise ValueError('Existing fix differs from the inspected code.')
        return source
    tree = ast.parse(source)
    nodes = [n for c in tree.body if isinstance(c, ast.ClassDef) and c.name == 'Tickets'
             for n in c.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'transition']
    if len(nodes) != 1:
        raise ValueError('Expected one Tickets.transition method.')
    node = nodes[0]
    lines = source.splitlines(keepends=True)
    method = ''.join(lines[node.lineno - 1:node.end_lineno])
    for before, after in REPLACEMENTS:
        if method.count(before) != 1 or source.count(before) != 1:
            raise ValueError('The capacity check differs from the inspected code. No changes made.')
        method = method.replace(before, after, 1)
    lines[node.lineno - 1:node.end_lineno] = [method]
    lines.insert(1, FIX_MARKER + '\n')
    updated = ''.join(lines)
    compile(updated, 'tickets.py', 'exec')
    return updated


def atomic_write(path, data, metadata):
    descriptor, name = tempfile.mkstemp(prefix='.tickets-type20-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary, metadata.st_uid, metadata.st_gid)
        os.chmod(temporary, stat.S_IMODE(metadata.st_mode))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def apply():
    if os.geteuid() != 0:
        raise SystemExit('Run as root.')
    if (ROOT / 'VERSION').read_text().strip() != '1.1.1':
        raise SystemExit('This repair requires Crew 1.1.1.')
    target = (ROOT / 'ripcars_crew/tickets.py').resolve(strict=True)
    original = target.read_bytes()
    updated = patched_source(original.decode()).encode()
    if updated == original:
        print('Unknown-channel repair is already installed.')
        return
    metadata = target.stat()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S-%fZ')
    backup = BACKUPS / ('type20-' + stamp) / 'tickets.py'
    backup.parent.mkdir(parents=True, mode=0o700)
    shutil.copy2(target, backup)
    print('Original code backup:', backup, flush=True)
    active = subprocess.run(['systemctl', 'is-active', '--quiet', SERVICE]).returncode == 0
    changed = False
    try:
        subprocess.run(['systemctl', 'stop', SERVICE], check=True, timeout=90)
        if (ROOT / 'ripcars_crew/tickets.py').resolve() != target or target.read_bytes() != original:
            raise RuntimeError('Release changed during preparation. Repair cancelled.')
        atomic_write(target, updated, metadata)
        changed = True
        subprocess.run(['runuser', '-u', 'ripcarscrew', '--', str(ROOT / '.venv/bin/python'),
                        str(ROOT / 'scripts/preflight.py'), '--code-only'],
                       cwd=ROOT, check=True, timeout=90)
        if active:
            subprocess.run(['systemctl', 'start', SERVICE], check=True, timeout=210)
    except BaseException:
        if changed:
            subprocess.run(['systemctl', 'stop', SERVICE], check=True, timeout=90)
            atomic_write(target, original, metadata)
            print('Previous code restored.', flush=True)
        if active:
            subprocess.run(['systemctl', 'start', SERVICE], check=True, timeout=210)
        raise
    print('Unknown-channel capacity check repaired. No database records were changed.')
    print('Retry Close on one affected ticket.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.apply:
        apply()
    else:
        parser.print_help()
