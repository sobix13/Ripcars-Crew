#!/usr/bin/env python3
"""Offline, token-redacted code and startup environment checks."""
from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--code-only", action="store_true")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        raise SystemExit("Python 3.11 or newer is required.")
    if importlib.metadata.version("discord.py") != "2.7.1":
        raise SystemExit("Install the exact SDK in requirements.txt.")
    from ripcars_crew import config, operations
    from ripcars_crew.bot import CrewBot
    config.validate(config.defaults())
    assert not operations.permissions().administrator
    with tempfile.TemporaryDirectory(prefix="ripcars-crew-preflight-") as td:
        async with CrewBot(Path(td) / "crew.db", Path(td) / "coord.db", sync=False) as bot:
            await bot.setup_hook()
            group = next(g for g in bot.tree.get_commands() if g.name == "crew")
            assert len(group.commands) <= 25
            assert len(bot.persistent_views) == 2
    if not args.code_only:
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token or token.startswith("replace") or any(v.isspace() for v in token):
            raise SystemExit("Set DISCORD_TOKEN in /etc/ripcars-crew.env; never print it.")
        guild = os.getenv("GUILD_ID", "").strip()
        if guild and (not guild.isdigit() or int(guild) <= 0):
            raise SystemExit("GUILD_ID must be a numeric server ID or empty for global sync.")
        paths = []
        for key in ("DB_PATH", "COORDINATION_PATH"):
            value = os.getenv(key, "")
            if not value or not Path(value).is_absolute():
                raise SystemExit(f"{key} must be an absolute writable state path.")
            path = Path(value)
            if not path.parent.is_dir() or not os.access(path.parent, os.W_OK):
                raise SystemExit(f"{key} parent is not writable by the service account.")
            if path.exists() and not os.access(path, os.W_OK):
                raise SystemExit(f"{key} database is not writable by the service account.")
            paths.append(path.resolve())
        if paths[0] == paths[1]:
            raise SystemExit("Operational and coordination state paths must be separate.")
    print("Preflight PASS: SDK, configuration, permission flags, command tree, SQLite, persistent views, shutdown.")


if __name__ == "__main__":
    asyncio.run(main())
