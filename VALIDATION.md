# Ripcars Crew validation results

Version 1.0.0 is a standalone ticket and chat protection bot. Gate remains the independent entry application. Tests used Python, the real discord.py SDK and real SQLite, with mocked Discord network boundaries.

| Check | Result |
| --- | --- |
| New release suite | 158 passed; zero failures, errors or skips |
| Clean environment without inherited packages | All tests passed |
| Complete application boot and mocked command sync | Passed |
| Permissions, panels, modals and native AutoMod with real SDK objects | Passed |
| Compilation, Pyflakes and dependency checks | Passed |
| Installer and rollback shell syntax | Passed |
| systemd unit syntax | Passed with the local Python path substituted; production paths are absent here |
| Original moderation baseline | 107 passed; one voice SDK deprecation warning |
| Original support baseline | Overlay only; a complete original test run was unavailable |
| Gate 1.0.1 compatibility | 52 passed, zero skips |
| Live Discord and production VPS installation | Not run |

Coverage includes configuration types/ranges, Unicode/word handling, masked links and domains, spam/duplicates/mentions, warnings/heat, exceptions, hierarchy, API failures and closed DMs, private single-creation tickets, close/reopen without rename, uncertain operation review/repair, preservation of external overwrites, persistent panels, retained channel selections, restarts, shared Gate locks, manual slowmode changes, backup integrity/retention, administrator authorization and member message reports.

Gate 1.0.1 only suppresses Unknown Message errors when another bot has already deleted the same message. Forbidden failures remain visible. CAPTCHA, the minimum one-answer rule, Rippers, OG and Holder policies are unchanged.

Scam protection uses configurable signals with possible detections set to Alert by default. It does not read DMs, fetch member links or files, or operate on wallets/secrets. Automatic timeout is configurable. Automatic ban/kick based on a suspected scam is not implemented.

Use `DEPLOYMENT.md` for installation and `ACCEPTANCE.md` for live acceptance. `TEST_RESULTS.txt` contains the actual recorded release test output. Injected tracebacks represent expected failure scenarios, not failed tests.
