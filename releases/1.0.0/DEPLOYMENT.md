# Install Ripcars Crew on the VPS

This is a separate chat protection and ticket application. Ripcars Gate remains independent. No old project database or token is migrated. Code is installed under `/opt/ripcars-crew`; the process runs as restricted user `ripcarscrew`. Run installation commands with `sudo`.

The main package is `ripcars-crew-1.0.0.tar.gz`. ZIP contains the same source for Windows inspection. Gate compatibility release `ripcars-gate-1.0.1.tar.gz` is separate. It only handles already-deleted message errors; the entry flow is unchanged.

## 1. Prepare the Discord application

Create a new application named `Ripcars Crew`, obtain its own token and invite it to the Rip Cars server. Keep that token out of chat and terminal output.

Enable these two settings under Bot > Privileged Gateway Intents:

- Server Members Intent
- Message Content Intent

For the invite, select scopes `bot` and `applications.commands` and these permissions:

View Channels, Read Message History, Send Messages, Embed Links, Attach Files, Manage Messages, Manage Channels, Manage Roles, Moderate Members, Kick Members, Ban Members and View Audit Log.

Administrator is not required. Add Manage Server yourself only if native AutoMod is enabled. Crew does not assign the entry, OG or self-claim roles. Manage Roles is required to edit ticket channel overwrites.

Place the bot role above Rippers and the members it manages. A moderator's own role must also be above the target and have the action permission. Crew does not automatically ban, kick or time out the owner, administrators or other bots.

## 2. Upload from Windows

Put the downloaded files in this directory:

```text
C:\Users\macbook\Desktop\files
```

PowerShell:

```powershell
$RipcarsVps = Read-Host 'VPS IP or SSH host alias'
Set-Location 'C:\Users\macbook\Desktop\files'
Get-FileHash '.\ripcars-crew-1.0.0.tar.gz' -Algorithm SHA256
scp '.\ripcars-crew-1.0.0.tar.gz' '.\ripcars-crew-1.0.0.zip' '.\RIPCARS_CREW_SHA256SUMS.txt' "memecult@${RipcarsVps}:/tmp/"
ssh "memecult@${RipcarsVps}"
```

If you use a different SSH account or connection method for this VPS, adjust that part of the commands. No VPS IP is hardcoded in the bot.

## 3. File integrity and prerequisites

On the VPS:

```bash
cd /tmp
sha256sum -c RIPCARS_CREW_SHA256SUMS.txt
```

Both files should report OK. Crew's `RIPCARS_CREW_SHA256SUMS.txt` covers its TAR and ZIP. Verify the separate Gate package with Gate's own checksum file before applying its compatibility update.

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip ca-certificates fonts-dejavu-core
python3 --version
```

Crew requires Python 3.11 or newer. If your version is older, install a supported Python for your operating system before continuing. Keep the virtual environments of other bots separate.

## 4. Install the Gate compatibility update separately

If Gate is already installed, apply the separate compatibility update before enabling overlapping Crew protection. Download it from [Ripcars-Gate](https://github.com/sobix13/Ripcars-Gate/tree/main/releases/1.0.1), verify it using Gate's own checksum file, and upload the verified TAR to `/tmp` before these commands. Existing releases and settings remain available. Gate's installer stages its own code and environment without deleting member data, CAPTCHA state or answers. If Gate is already on 1.0.1, this update step is unnecessary.

```bash
RIPCARS_GATE_STAGE="$(mktemp -d /tmp/ripcars-gate-update.XXXXXX)"
tar -xzf /tmp/ripcars-gate-1.0.1.tar.gz -C "$RIPCARS_GATE_STAGE"
sudo bash "$RIPCARS_GATE_STAGE/ripcars-gate/scripts/install.sh" "$RIPCARS_GATE_STAGE/ripcars-gate"
sudo systemctl restart ripcars-gate
sudo systemctl status ripcars-gate --no-pager -l
sudo journalctl -u ripcars-gate --since '5 minutes ago' --no-pager -l
```

For a first Gate installation, use its own deployment guide. Crew does not replace CAPTCHA or assign Rippers.

## 5. Install Crew

```bash
RIPCARS_CREW_STAGE="$(mktemp -d /tmp/ripcars-crew-install.XXXXXX)"
tar -xzf /tmp/ripcars-crew-1.0.0.tar.gz -C "$RIPCARS_CREW_STAGE"
sudo bash "$RIPCARS_CREW_STAGE/ripcars-crew/scripts/install.sh" "$RIPCARS_CREW_STAGE/ripcars-crew"
```

Installation runs all tests in the staged release. If a dependency or test fails, resolve that failure before starting the service. Crew installation does not start or stop other services and does not change Discord settings.

Environment configuration:

```bash
sudo nano /etc/ripcars-crew.env
```

Values:

```dotenv
DISCORD_TOKEN=YOUR_NEW_CREW_BOT_TOKEN
GUILD_ID=YOUR_RIP_CARS_SERVER_ID
DB_PATH=/var/lib/ripcars-crew/crew.sqlite3
COORDINATION_PATH=/var/lib/ripcars-bots/coordination.sqlite3
LOG_LEVEL=INFO
```

Store the real token only in this private file. Keep the completed configuration out of chat. An empty `GUILD_ID` uses global command sync. The Rip Cars server ID enables faster guild-scoped setup checks.

```bash
sudo chmod 600 /etc/ripcars-crew.env
sudo systemctl enable --now ripcars-crew
sudo systemctl status ripcars-crew --no-pager -l
sudo journalctl -u ripcars-crew --since '5 minutes ago' --no-pager -l
```

Wait for `online as` and version `1.0.0`. A running service does not enable tickets or protection. Both start disabled inside Discord.

## 6. Configure Discord and keep bot responsibilities separate

Start with Crew:

```text
/crew panel
```

Choose Gate & coordination > Import Gate IDs and confirm. This imports IDs only; it transfers no permission or ownership. If IDs are absent, check that both services use `/var/lib/ripcars-bots/coordination.sqlite3`. Resources are not adopted by guessed names.

Then open Gate:

```text
/gate panel
```

Under Future bots:

1. Register Crew's numeric bot user ID with scope `support`.
2. Review and confirm Review / activate.
3. Separately confirm the Ticket / Open / Closed ownership handoff to that Crew bot.

Gate retains CAPTCHA, questions, role claims, gCARS and general server structure. Holder and OG responsibilities are not handed to Crew.

Return to Crew:

```text
/crew setup
/crew doctor
```

First setup page: moderator role, Rippers role and private log channel. Second page: Ticket, Open and Closed. Selected values remain visible; save with Review & save.

Open and Closed must be hidden from ordinary members and OG. The ticket author receives a private overwrite for their own open ticket, without category-wide access. Closing removes author access to the archived ticket. Moderator, Admin and Team retain access through their configured staff roles.

If Gate created and registered these categories, use the handoff rather than Create standalone containers. The standalone option creates only two private categories when there is no registered structure. Same-name objects require explicit ID binding.

## 7. Enable tickets and protection

In `/crew panel` > Tickets:

1. Review the topics and messages.
2. Choose Publish/update panel.
3. Save `tickets.enabled` as `true`.

Default topics: Packs & rips, Account & site, Report a bug and Something else. Add, remove or edit topics as JSON in the panel. `key` is the stable topic identifier; `label` is its display name.

Under Chat protection:

1. Set `guard.mode` to `observe` first.
2. Set `guard.enabled` to `true`.
3. Check sample text with `/crew test-rule` and inspect the logs.
4. After review, set `guard.mode` to `protect`.

Observe mode records review cases without deleting messages, adding warnings or applying timeouts. Protect executes each filter's configured action. The word list starts empty. Add entries under Words & links > guard.words > Add entry. `exact` does not match inside another harmless word; `contains` matches more broadly. Local rule changes apply to new messages.

Potential lookalike links, gift/Nitro lures, secret requests and staff impersonation default to `alert`. These signals do not trigger automatic bans. Change an action only after reviewing the policy.

## 8. Operating commands

```text
/crew health
/crew doctor
/crew member user:@member
/crew warn user:@member reason:Server rule reminder
/crew timeout user:@member length:10m reason:Repeated spam
/crew untimeout user:@member reason:Reviewed
/crew cooldown user:@member channel:#general seconds:60
/crew slowmode channel:#general seconds:10 lifetime:600
/crew cases
/crew ticket-list
/crew guide
```

Member cooldown deletes that member's subsequent messages in the same channel while Crew is online. Native slowmode affects the entire channel. Gate retains the daily gCARS flow. Sensitive actions require private confirmation. Use purge, ban and ticket deletion only for an intended action on real data.

Members report messages through Right-click > Apps > Report to Rip Cars Crew. A report creates a review case without automatic punishment.

## 9. Optional native AutoMod

To block configured words before publication even while Crew is offline:

1. Grant Crew Manage Server.
2. Use Guard mode `protect`, not `observe`.
3. Set `native.enabled` to `true`.
4. Confirm Sync native rule in Native AutoMod.

Only Crew's recorded rule is managed; other bots' rules remain unchanged. Discord's native matcher differs from the local Unicode normalization and does not automatically mirror warning/heat processing. A manual rule change pauses synchronization for review.

To disable it, set `native.enabled=false` and run Sync. Stopping the service, changing Guard mode or restoring settings alone does not disable a rule already stored in Discord.

## 10. Errors, review and maintenance

```bash
sudo journalctl -u ripcars-crew -f
```

In Discord, use Health, Doctor and Monitoring & data > Recent events / Statistics / Backup now. Error IDs start with `RCC-`. Logs are sent only to a staff-only channel; a public log channel does not receive private user data.

For an uncertain operation:

```text
/crew review case case_id:123 note:Checked the live member state
/crew review control channel:#general
/crew review native
```

These are confirmed administrator actions. They mark an uncertain case Reviewed after inspection rather than assuming success. Native control is resumed only after explicit review of the same rule's current baseline. Rules with another creator are rejected.

Use Tickets > Review incomplete tickets. Missing tickets are not recreated automatically. If a Discord move already completed, only verifiable database state is repaired. An incomplete starter requires manual review; unknown channels or records are not removed by guesswork.

For troubleshooting, share an Error ID, service status and panel screenshot. Keep tokens, environment files and member databases private.

## 11. Retest and roll back

```bash
sudo /opt/ripcars-crew/current/.venv/bin/python /opt/ripcars-crew/current/scripts/preflight.py --code-only
sudo env PYTHON_BIN=/opt/ripcars-crew/current/.venv/bin/python bash /opt/ripcars-crew/current/run_tests.sh
```

If an earlier code release is installed, roll back with:

```bash
sudo bash /opt/ripcars-crew/current/scripts/rollback.sh
```

Code rollback does not undo bans, message deletion, Discord changes or database updates. SQLite backups live under `/var/lib/ripcars-crew/backups`. Database restoration requires stopping this service and a separate reviewed procedure.

Before accepting the deployment, complete `ACCEPTANCE.md` with ordinary and staff accounts. These recorded tests use an isolated environment and mocked API boundaries, without a real Discord token or production VPS deployment.
