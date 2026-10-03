#!/usr/bin/env bash
set -Eeuo pipefail
if [ "$(id -u)" -ne 0 ]; then echo "Run with sudo." >&2; exit 1; fi
CREW_PREVIOUS_FILE=/opt/ripcars-crew/previous-release
if [ ! -f "$CREW_PREVIOUS_FILE" ]; then echo "No previous release recorded." >&2; exit 1; fi
CREW_PREVIOUS="$(<"$CREW_PREVIOUS_FILE")"
case "$CREW_PREVIOUS" in /opt/ripcars-crew/releases/*) ;; *) echo "Invalid rollback target." >&2; exit 1 ;; esac
if [ ! -f "$CREW_PREVIOUS/main.py" ] || [ ! -x "$CREW_PREVIOUS/.venv/bin/python" ]; then echo "Previous release is unavailable." >&2; exit 1; fi
systemctl stop ripcars-crew
ln -s "$CREW_PREVIOUS" /opt/ripcars-crew/current.rollback
mv -Tf /opt/ripcars-crew/current.rollback /opt/ripcars-crew/current
install -m 644 "$CREW_PREVIOUS/ripcars-crew.service" /etc/systemd/system/ripcars-crew.service
systemctl daemon-reload
systemctl start ripcars-crew
echo "Code rolled back only. Operational data, shared coordination and Discord actions were not rolled back."
