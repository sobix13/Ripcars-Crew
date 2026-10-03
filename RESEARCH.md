# Primary-source review and implementation decisions

Reviewed 2026-10-02. Product documentation is treated as a feature reference, not independent proof of its accuracy or detection quality.

| Source | Relevant idea | Crew decision |
| --- | --- | --- |
| [Dyno AutoMod](https://docs.dyno.gg/en/modules/automod) | Separate word/link/spam rules and configurable exemptions/actions | Separate enabled/action/weight per rule; exact/phrase/contains instead of an opaque one-switch filter |
| [Wick Features](https://docs.wickbot.com/intro/features/) | Repetition/flood/mentions, heat and escalating timeouts, join monitoring | Bounded windows plus persistent decaying heat/warnings; new-account/join bursts alert only; no automatic anti-nuke rollback |
| [Discord Auto Moderation API](https://docs.discord.com/developers/resources/auto-moderation) | Before-post word blocking, owned rule objects, action types/exemption limits | Optional explicit native mirror; own creator/ID/baseline checks; no edits to other rules; distinct native matcher limits |
| [Discord Channel API](https://docs.discord.com/developers/resources/channel) | Native slowmode is per-channel, max 21600 seconds; overwrite edits | Persistent member-only suppression separately; safe temporary channel slowmode; no false claim of 24h native slowmode |
| [Discord Permissions](https://docs.discord.com/developers/topics/permissions) | Role hierarchy, Administrator bypass, channel overwrites | Explicit actor/bot preflight and private log/category checks; no automatic Administrator grant |
| [Discord Rate Limits](https://docs.discord.com/developers/topics/rate-limits) | Limits vary; honor API headers/buckets rather than hardcode delays | Rely on discord.py HTTP handling; one non-name close/reopen PATCH; no claim of measured live throughput |
| [discord.py API](https://discordpy.readthedocs.io/en/stable/api.html) | Real UI, modal, AutoMod, intents, application command interfaces | Actual SDK serialization/permission/boot tests with mocked API boundaries |
| [discord.py 2.7.1 release](https://pypi.org/project/discord.py/2.7.1/) | Exact runtime release | Pinned version tested in both SDK-equipped and clean isolated environments |

Implemented configurable protections include blocked domains, some official-domain lookalikes/Unicode/user-info tricks, masked destinations, secret-request and gift/reward cues, filename extensions, staff-name impersonation cues, word rules, burst/duplicate/mentions, per-user cooldown, and member reports. Heuristic cues default to review-only to avoid punishing legitimate discussions of scams or normal Rip Cars giveaways.

Not included: external phishing feeds, shortened-link expansion, machine-learning abuse classification, OCR/QR decoding, DM surveillance, malware scanning, coordinated anti-nuke rollback, or automatic wallet verification. Adding a vetted feed later would require an explicit trust/update/expiry policy and safe fetching limited to that feed, not visiting user-posted URLs. Current local lists/thresholds/exemptions remain editable entirely in Discord.

Native AutoMod's whole-word/phrase matching is whitespace-oriented and its own exemptions apply; local normalization/boundaries differ. Native blocking does not automatically duplicate Crew warning/heat escalation. The documentation and UI explicitly separate these behaviors.
