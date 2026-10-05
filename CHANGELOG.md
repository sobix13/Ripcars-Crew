# 1.0.1 four-bot coordination update

- Shared protocol 2 mirrors both legacy lease tables and enforces unique resource IDs.
- Long leases renew; ticket, slowmode and native-rule operations check the lease before their next controlled change.
- Protected Gate bindings cannot be silently imported as active configuration.
- Added shared protocol regression tests and matching four-bot rollout/test documentation.
- Existing moderation, support and human review policies are unchanged.

## 1.0.0

Independent combined Rip Cars moderation/support process. Branch-first settings and setup wizard, 15 local configurable filters, warning/case/heat escalation, private ticket lifecycle and bounded transcripts, optional owned native word rule, member message reports, human review controls, Gate ownership/lease coordination, private diagnostics/logs, clean staged deployment and rollback. Source support archive was an overlay; missing application layers were implemented independently and are identified in REVIEW.md.

Native rule fields and wizard selections are validated using real discord.py types. Ambiguous API/DB outcomes remain visible. Other bot services, Gate verification/role claims, OG and holder policy are not replaced. Gate 1.0.1 is supplied separately for safe same-message deletion races. Live Discord/VPS acceptance remains pending.

## English GitHub distribution

- All deployment and validation documentation is English.
- Complete source, tests, dependencies and English TAR/ZIP packages are included.
- The installer excludes Git metadata and release archives when run from a checkout.
- No member onboarding, moderation, ticket or database behavior changed.
- Added the reusable clean release builder at `scripts/build_release.py`.
