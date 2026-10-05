# Ripcars Crew 1.0.1
## Repository files and installation packages

This repository contains the complete 1.0.1 source, tests, dependency files, installation/rollback scripts, systemd service, settings examples and English operational documentation.

| File or directory | Purpose |
| --- | --- |
| [DEPLOYMENT.md](DEPLOYMENT.md) | English Windows-to-VPS installation and Discord setup steps |
| [ACCEPTANCE.md](ACCEPTANCE.md) | Live server acceptance checklist |
| [COORDINATION.md](COORDINATION.md) | Cross-bot ownership and integration protocol |
| [TEST_RESULTS.txt](TEST_RESULTS.txt) | Actual offline verification output |
| [VALIDATION.json](VALIDATION.json) | Machine-readable validation status |
| [requirements.txt](requirements.txt) | Runtime requirements |
| [.env.example](.env.example) | Token and server configuration placeholders |
| [scripts/install.sh](scripts/install.sh) | Isolated release installation with tests |
| [scripts/rollback.sh](scripts/rollback.sh) | Code rollback |
| [ripcars-crew.service](ripcars-crew.service) | Non-root systemd service |
| [tests](tests) | Complete offline suite |
| [releases/1.0.1](releases/1.0.1) | English TAR/ZIP packages, checksums and matching guides |

The source and archives contain no credentials, member databases or virtual environments. Installation excludes Git metadata, previous packaged releases and runtime state. Version 1.0.1 fixes shared coordination and renews/checks long setup leases; moderation and ticket policies are unchanged.

See [SUITE_DEPLOYMENT.md](SUITE_DEPLOYMENT.md) and [SUITE_TEST_RESULTS.txt](SUITE_TEST_RESULTS.txt) for the compatible four-bot versions, rollout and multi-process test evidence. All four ship identical `ripcars_coordination.py`.

```bash
git clone https://github.com/sobix13/Ripcars-Crew.git
cd Ripcars-Crew
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
bash run_tests.sh
```

For VPS installation from this checkout, follow [DEPLOYMENT.md](DEPLOYMENT.md) and replace its archive extraction step with the clone above. Run `sudo bash scripts/install.sh "$PWD"` from the checkout, configure the private environment file, and start only this bot's service as described in the guide.

Also included: [VALIDATION.md](VALIDATION.md), [RESEARCH.md](RESEARCH.md), [REVIEW.md](REVIEW.md), [requirements-dev.txt](requirements-dev.txt), and [requirements-lock.txt](requirements-lock.txt).


One independent Rip Cars application combining chat moderation and private support. Ripcars Gate remains a separate application for CAPTCHA, at least one onboarding answer, Rippers, optional role claims, and the daily gCARS posting UI. This release does not assign OG, run holder verification, migrate a production database, or replace another running service.

Python 3.11+; pinned discord.py 2.7.1; standard SQLite; English public copy; burgundy `#800020`; official app `https://app.ripcars.io`. All code, package names, service names, environment names, messages, tests, and docs are Rip Cars-specific. No legacy package or updater is shipped.

## Features

- Branch-first private admin center: setup, protection, words/links, spam thresholds, exceptions, warnings, tickets, messages/brand, chat controls, monitoring/data, native AutoMod, Gate coordination, guide.
- Staff/member/channel/category selectors keep the selected value visible; selections are drafts until review/save. Stale forms cannot overwrite a newer revision.
- Individually configurable filter enable/action/heat weight. Actions: alert, delete, warn, timeout. No automatic kick or ban.
- Editable words: exact, phrase, contains; NFKC/casefold/zero-width normalization. No arbitrary regex execution. Word lists are empty initially.
- Explicit blocked/allowed/official domains; subdomain boundary checks; URL user-info, some Unicode/lookalike domains, masked links, secret requests, gift/reward lures, invite links, dangerous filename extensions, protected staff display names.
- Sliding-window message flood and duplicate detection, mention limit, optional capitals/newlines. Bounded in-memory state; spam windows intentionally reset on restart.
- Persistent warnings, decay-based heat, configurable repeat-warning timeout, warning expiry/revocation, pending/failed/reviewed case states, deduplicated message/edit processing.
- Staff moderation with reason, confirmation, actor and bot role hierarchy, actual action permissions, timeout/untimeout/kick/ban. Ban uses Discord-native cleanup up to seven days. Kick cleanup is bounded (50 channels, 100 recent messages per channel), reports counts and may leave older messages. Purge is separately confirmed and bounded to 200 recent messages.
- Member-only per-channel cooldown: persisted suppression while online, implemented by deleting subsequent messages after publication. Discord-native slowmode is channel-wide and capped at 21,600 seconds. Temporary channel slowmode preserves its previous value and restores only if no external edit/ownership change occurred. It never changes gCARS' modal-based 24-hour posting policy.
- Private support: editable topics, one active ticket per member, claim/reassign without rename, confirmed close/reopen/delete, staff-only archives, bounded redacted text transcript, opt-in timed closed-channel deletion (off by default), interrupted-operation review.
- Right-click a message and choose **Report to Rip Cars Crew**: private, rate-limited reporting; reports never automatically punish the reported user.
- Optional native Discord AutoMod word mirror. Only the recorded rule ID created by this bot is edited. A manual edit/deletion pauses sync for review. Native blocking can keep working while the process is offline. Its matcher uses Discord's rules, not the same Unicode normalization as the local engine; literal `*` entries are omitted from the mirror.
- Private in-Discord logs/errors, stable error IDs, permission doctor, Gateway/database/coordination/task/backup health, consistent SQLite backups with integrity check and 14-copy retention, systemd watchdog and restart.
- Join-burst/new-account monitoring and audit-event metadata. New accounts are not automatically kicked, banned, or quarantined. No anti-nuke rollback fights admins or other bots.
- Opt-in recent message previews (off initially), bounded activity counters, settings export/import/history/paused restore, configurable retention, recent events/statistics.

## First deployment

See `DEPLOYMENT.md` for Windows-to-VPS copy/paste steps. Install a new application named Ripcars Crew, use a new token, and enable Members and Message Content privileged intents. The installer tests a staged physical release, atomically switches the release symlink, and does not start any service until you explicitly do so.

Operational database: `/var/lib/ripcars-crew/crew.sqlite3`, private 0700 directory. Shared registry/leases: `/var/lib/ripcars-bots/coordination.sqlite3`, shared group `ripcars-bots`. Token: `/etc/ripcars-crew.env`, root-owned mode 0600. Runtime service: `ripcars-crew`, non-root `ripcarscrew`.

Start with `/crew setup` or `/crew panel`. Configure valid moderator role IDs, member role, and private log. Use Gate & coordination > Import Gate IDs if Gate created the server. Activate this numeric bot ID as a support integration inside Gate and explicitly hand off Ticket/Open/Closed. Crew refuses to control those registered resources while Gate still owns them. If no Gate-created containers exist, the confirmed standalone option can create private Open/Closed categories only; same-name objects are not automatically adopted. Bind the ticket panel/log channels yourself in Setup.

Publish the ticket panel before enabling tickets. Enable chat protection only after Doctor blockers are resolved. `observe` reports detections without deletion, warnings, or timeout; `protect` executes configured actions. Do not enable optional native blocking in observe mode. Native rule sync/disable is an explicit separate action; restoring settings or stopping the process does not undo a rule already stored in Discord.

Install Gate 1.0.2 for the shared four-bot protocol. It also retains the safe Discord NotFound handling when another controller already deleted a message; real permission failures still surface. Membership flow is unchanged. Gate's upgrade remains separate from Crew installation. Follow the reviewed rollout in SUITE_DEPLOYMENT.md.

## Editing in Discord

The panel exposes every runtime behavior setting. Select a field, enter its value, and save. Booleans use `true/false`; numeric values use seconds or the named unit; lists use JSON or Add/remove entry. Text/branding fields use plain text. For large lists use `/crew export-config` and `/crew import-config` with a JSON attachment (max 256 KB); imports are validated and confirmed. The token, service paths, and application identity remain deployment settings, not editable by a moderator panel.

Public support/warning/safety copy and form labels are editable under Messages & brand. Structural command names, permission checks, private error formats, and admin guide labels stay fixed code so configuration cannot bypass authorization. If moderator roles or channel visibility later change, Doctor and private-log checks show the problem; the bot does not grant itself more authority.

## Responsibilities and limits

See `COORDINATION.md`, `REVIEW.md`, `RESEARCH.md`, and `ACCEPTANCE.md`.

- Scam rules are configurable signals, not an all-scams guarantee. Heuristics default to alert for human review; raising them to delete/timeout is an admin decision. Allowlisting a link does not exempt its words, mentions, attachments, or burst behavior.
- No external reputation feed, remote link opening, redirect expansion, attachment download, OCR, DM surveillance, wallet access, or malware-execution environment. Executable-extension filtering does not prove that image/file contents are safe.
- Bot-only filters delete after a message appears and need the process online. Native AutoMod can block before posting but does not follow the bot's local heat/warning escalation automatically. Discord exempts some privileged accounts from native rules.
- Trusted bot IDs are explicit; there is no trust-by-name or blanket new-bot permission grant. Arbitrary third-party bots/admins do not honor our shared lease, and Discord has no atomic compare-and-swap; a narrow final-write race cannot be eliminated.
- Gate's basic media/link restrictions and daily gCARS workflow stay with Gate. Crew does not independently implement those channel access policies. Messages submitted through other bots' modals cannot be universally inspected from another application before they are posted.
- Ticket starter or API/DB interruptions remain visible for review rather than claiming success. Closed-ticket auto-delete defaults off. If enabled, deletion is permanent in Discord; use transcripts first. Transcript exports are bounded text and may omit older messages.
- Settings/code rollback does not undo Discord bans, deletions, native rules, or data state. Credentials and databases from the earlier projects are not copied.

## Tests

`PYTHON_BIN=/path/to/python bash run_tests.sh` runs offline preflight, syntax, optional pyflakes, and the full unittest suite. Missing real discord.py fails instead of silently skipping SDK tests. Tests use actual SDK permission/UI/AutoMod/command-tree types, actual SQLite, and mocked API methods. A clean isolated venv was also used, with no inherited site packages.

See `TEST_RESULTS.txt` and `VALIDATION.json` for the measured result. Live Discord acceptance and the production VPS service check remain pending; no token or SSH connection was used here.

Build new English packages with `python3 scripts/build_release.py /tmp/ripcars-crew-release-output`. The output directory must be outside this checkout and must not contain archives with the same version.
