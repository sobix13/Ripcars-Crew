# Gate / Crew responsibility contract

Both processes use the exact same `resources`, `integrations`, and `leases` schema in `/var/lib/ripcars-bots/coordination.sqlite3`. Member profiles/answers remain private to Gate, and cases/tickets remain private to Crew. Neither process reads the other's token or operational database.

| Responsibility | Controller |
| --- | --- |
| CAPTCHA, minimum one onboarding answer, Rippers | Gate |
| Optional notification/collector role claims | Gate |
| Daily gCARS modal posting, basic no-link/media policies | Gate |
| Server blueprint, non-ticket owned permission templates | Gate |
| Private ticket lifecycle after confirmed handoff | Crew |
| Word/scam/spam filters, warnings, moderation history | Crew |
| Native Crew word rule after explicit sync | Crew |
| Temporary channel slowmode after confirmation | Crew, temporarily pausing Gate reconciliation |
| OG assignment | Administrator |
| Holder verification | Future dedicated bot; currently hidden |
| Unknown/admin/third-party resources | Not auto-adopted or repaired |

Importing Gate IDs does not transfer ownership or grant permissions. In Gate, register the numeric Crew bot ID with support scope, activate reviewed limited member overwrites, then confirm responsibility handoff for `channel:ticket`, `channel:open_tickets`, `channel:closed_tickets`. They then carry `owner=bot:<Crew ID>` and `state=external`, which Gate ignores. Crew validates category privacy before opening tickets. Gate Log remains Gate-owned; Crew only posts there if the log is private and accessible. No Administrator is automatically granted.

Every schema mutation uses SQLite `BEGIN IMMEDIATE`; setup/handoff/native sync/temporary slowmode use the shared guild `server-setup` lease. Ticket create/close/reopen also use that lease; per-ticket delete uses a narrower ticket lock. Locks expire and releases match the holder's random token. Local user locks serialize duplicate message handling. Leases coordinate participating processes, not arbitrary Discord actors.

For explicit temporary slowmode, Crew stores the old value and registry record, then changes the registered resource state to `crew-temporary`, which Gate's existing active-only reconciliation skips. At expiry, Crew restores only when the live slowmode still matches the applied value and ownership/baselines have not changed; it then restores the old registry state. Otherwise it preserves the live edit, marks manual/review, and reports it. `/crew review control` explicitly accepts the current value, and Gate's Keep current can later pin a protected resource. No complete overwrite dictionary is replaced for ordinary existing community channels.

Unknown channels, categories, roles, and incoming bot accounts are never silently adopted by name. Existing ticket overwrites for unrelated targets/fields survive close/reopen. A newly created ticket receives staff/author-specific access; a closed ticket receives an explicit author deny. An administrator can deliberately add participants outside this template; the bot does not pretend that such manual participants never exist.

Gate 1.0.1 handles deletion races by treating message NotFound as already completed, without swallowing Forbidden. Crew already makes that distinction. Different command groups (`/gate`, `/crew`) and application-scoped persistent custom IDs prevent command/control collision. Crew has no role-claim panel and no verification stage.

Do not run the previous moderation/support controllers with this bot on the same Rip Cars channels. They are separate deployments; this release neither stops nor repurposes them. If you choose to move a previous bot account, review its commands/privileges separately; a fresh application/token is recommended for this fresh server.

External edits in the final Discord API write window are still possible. Native/manual resources pause rather than being force-repaired. No claim of universal cross-bot conflict prevention is made.
