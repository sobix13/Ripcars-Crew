# Source and implementation review

The supplied moderation archive contains a complete application and test suite. The supplied support archive contains only an update overlay (services/UI/texts/tests/updater), not its core/database/errors/helpers or complete previous application. The original support test suite cannot be run faithfully without those missing dependencies. This is a new independent implementation preserving the observable support lifecycle and its non-name close/reopen fix, not an undocumented claim that unavailable source was copied.

| Layer | Reviewed behavior | Result in this release |
| --- | --- | --- |
| App entry / SDK / command sync | Original multi-server setup, intents, global commands, health hooks | Pinned SDK, guild/global sync, real offline boot, private authorization on each action |
| Access and hierarchy | Staff-role delegation, target hierarchy, sensitive actions, self-role risks | Actor/bot permissions rechecked on confirmed actions; owner/admin/bot targets protected; self-roles deliberately stay with Gate |
| UI state | Role/channel selection persistence, confirmation owners, retry/defer behavior, component limits | Two-page setup keeps selected IDs/defaults; stale revision protection; 13 clear branches; SDK serialization tests |
| Data | SQLite concurrency, rolling previews, history, backups | Separate private operational/shared coordination files; cancellation-safe worker transactions; retention; previews opt-in; consistent backup integrity |
| Moderation | Kick/ban/timeout workflows and old cleanup scan | Warnings/cases/revocation/heat and local detectors added; cleanup now bounded and explicitly reported |
| Ticket creation | Category enabled, one-open constraint, API success/DB failure rollback | Unique active-user index, atomic reservation, explicit access, failed rollback retains review record |
| Close/reopen | One non-name channel PATCH before persisted final state | Preserved; pending transition journal added; repair settles only unambiguous observed state; foreign overwrites kept |
| Ticket transcript/delete | Previously unlimited history scan and optional timed delete | Bounded redacted export; no attachment download; auto-delete defaults off and requires a closed owned record |
| Errors/monitoring | Error storage, in-process health, daily backups, watchdog | Discord-private rate-limited error IDs, task/Gateway/SQLite checks, join burst/audit metadata, human review controls |
| Branding/build/deployment | Public copy, modules, filesystem/service/env IDs, release contents | New Rip Cars-only package; no older token/data/updater/cache included; clean install stage and rollback |
| Gate compatibility | Same-message deletion race, shared ownership/leases | Gate 1.0.1 narrow compatibility fix; explicit ticket handoff; temporary registry state; independent slash namespaces |

Original complete moderation tests: 107 passed in the SDK-equipped isolated environment. One SDK audioop deprecation warning was observed; no voice features are part of Crew. New tests exercise the equivalent support paths plus new failures, actual SDK serialization and command-tree boot. Production Discord/VPS acceptance remains a separate step.

Deliberate exclusions: a second verification flow, holder/wallet checks, duplicate self-role controller, automatic new-bot trust, automatic admin/third-party rollback, unbounded history/transcript scans, auto-ban from a heuristic, arbitrary regex, remote URL fetch/OCR, and production database migration.
