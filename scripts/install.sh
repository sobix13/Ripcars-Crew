#!/usr/bin/env bash
# Install code only. No Discord mutations or other bot service changes.
set -Eeuo pipefail
if [ "$(id -u)" -ne 0 ]; then echo "Run with sudo." >&2; exit 1; fi
CREW_SOURCE="$(realpath "${1:-$(dirname "$0")/..}")"
if [ ! -f "$CREW_SOURCE/main.py" ] || [ ! -f "$CREW_SOURCE/VERSION" ]; then echo "Invalid release source." >&2; exit 1; fi
CREW_VERSION="$(<"$CREW_SOURCE/VERSION")"
if [[ ! "$CREW_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then echo "Invalid version." >&2; exit 1; fi
CREW_ROOT=/opt/ripcars-crew
CREW_STAMP="$(date -u +%Y%m%dT%H%M%S%N)"
CREW_RELEASE="$CREW_ROOT/releases/$CREW_VERSION-$CREW_STAMP"
if [ -e "$CREW_RELEASE" ]; then echo "Release already exists." >&2; exit 1; fi
getent group ripcars-bots >/dev/null || groupadd --system ripcars-bots
id ripcarscrew >/dev/null 2>&1 || useradd --system --user-group --home-dir /var/lib/ripcars-crew --shell /usr/sbin/nologin ripcarscrew
usermod -aG ripcars-bots ripcarscrew
install -d -m 700 -o ripcarscrew -g ripcarscrew /var/lib/ripcars-crew
install -d -m 2770 -o root -g ripcars-bots /var/lib/ripcars-bots
# Do not change or delete Gate's private data or credentials.
if [ -f /var/lib/ripcars-bots/coordination.sqlite3 ]; then
    chgrp ripcars-bots /var/lib/ripcars-bots/coordination.sqlite3
    chmod g+rw /var/lib/ripcars-bots/coordination.sqlite3
fi
for CREW_SHARED in /var/lib/ripcars-bots/coordination.sqlite3-wal /var/lib/ripcars-bots/coordination.sqlite3-shm; do
    if [ -f "$CREW_SHARED" ]; then chgrp ripcars-bots "$CREW_SHARED"; chmod g+rw "$CREW_SHARED"; fi
done
install -d -m 755 "$CREW_RELEASE"
tar -C "$CREW_SOURCE" --exclude=.venv --exclude=.env --exclude=.git --exclude=releases --exclude=logs --exclude=__pycache__ --exclude=.pytest_cache --exclude=data --exclude=backups --exclude='*.sqlite3*' --exclude='*.db*' -cf - . | tar -C "$CREW_RELEASE" -xf -
python3 -m venv "$CREW_RELEASE/.venv"
"$CREW_RELEASE/.venv/bin/python" -m pip install -r "$CREW_RELEASE/requirements-lock.txt"
PYTHON_BIN="$CREW_RELEASE/.venv/bin/python" bash "$CREW_RELEASE/run_tests.sh"
chown -R root:root "$CREW_RELEASE"
chmod -R go-w "$CREW_RELEASE"
if [ ! -f /etc/ripcars-crew.env ]; then install -m 600 "$CREW_SOURCE/.env.example" /etc/ripcars-crew.env; fi
CREW_PREVIOUS="$(readlink -f "$CREW_ROOT/current" 2>/dev/null || true)"
if [ -n "$CREW_PREVIOUS" ]; then printf '%s\n' "$CREW_PREVIOUS" > "$CREW_ROOT/previous-release"; fi
if [ -e "$CREW_ROOT/current.next" ] || [ -L "$CREW_ROOT/current.next" ]; then echo "A pending release link exists; inspect it before proceeding." >&2; exit 1; fi
ln -s "$CREW_RELEASE" "$CREW_ROOT/current.next"
mv -Tf "$CREW_ROOT/current.next" "$CREW_ROOT/current"
install -m 644 "$CREW_RELEASE/ripcars-crew.service" /etc/systemd/system/ripcars-crew.service
systemctl daemon-reload
echo "Installed and tested: $CREW_RELEASE"
echo "Edit /etc/ripcars-crew.env, then enable/start ripcars-crew explicitly. No other bot was installed, started or stopped."
