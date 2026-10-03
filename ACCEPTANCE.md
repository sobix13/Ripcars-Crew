# Live acceptance checklist (not yet executed)

Use the fresh Rip Cars server and a regular non-admin test account. Do not test a destructive purge/ban on production conversation history. Local mocked tests do not prove real channel visibility, enabled privileged intents, token validity, or live role hierarchy.

1. Install/start the dedicated non-root service; confirm `online as` and `/crew health`.
2. Developer Portal Members/Message Content intents are enabled; no disallowed-intents close.
3. `/crew setup` channel/role selections remain visible when another field is selected; review/save persists after restart.
4. A non-admin cannot open admin settings, submit an admin modal, import/restore settings, or use protected review controls.
5. A Moderator can review cases/health and moderate members below its role, but cannot grant settings/admin authority.
6. Both actor and bot hierarchy/permissions reject an above-role, owner/admin/bot target.
7. `/crew doctor` shows missing channel read/history/manage flags and private-log blockers accurately.
8. Gate keeps CAPTCHA + at least one answer; Rippers is still granted only there. Notification roles/gCARS remain Gate-owned.
9. Import Gate IDs alone grants no access/ownership; tickets refuse to open before explicit Gate handoff.
10. Ticket/Open/Closed handoff to Crew succeeds without altering unrelated objects or OG/holder policy.
11. A Rippers member can open one ticket; another ordinary/OG member cannot view it; moderators can read its history.
12. Double-click/parallel opens do not create duplicate active channels.
13. Claim/reassign updates the control embed without a channel rename.
14. Close removes the author's view/history/post access, moves to Closed once, leaves staff access and unrelated bot overwrites intact.
15. Reopen restores only the opener/staff access; a second active ticket prevents reopening an old archive.
16. Transcripts deliver only to private staff/ephemeral staff view, include bounded/redacted text, and do not download attachments.
17. Delete/archive cleanup is off initially. Test an explicitly disposable closed ticket before enabling it.
18. Add a test word from Words & links; exact `hi` must not reject `high`. Remove it without editing source.
19. Observe mode reports a match without deletion/warning/timeout; protect mode performs the configured rule action.
20. Banned-word/blocked-domain warn creates one case and DM notice; edits and duplicate gateway handling do not double count.
21. Blocked DM or missing Manage Messages is reported without falsely claiming the action completed.
22. Repeat warnings/heat cause configured timeout, never automatic ban. Revocation by an admin removes an active warning from escalation.
23. Official `app.ripcars.io` is not a lookalike, whereas a test lookalike or masked destination produces a review alert, not a default ban.
24. Configured role/channel exemptions and include-only scope behave as intended, including threads.
25. Member cooldown affects only that member/channel and survives process restart; native channel slowmode affects the whole channel.
26. Temporary slowmode expiry restores the old value if untouched; an admin change is preserved and reported for review.
27. Right-click report is private/rate-limited and does not itself punish the reported user.
28. Optional native word rule sync creates/edits only this bot's rule. Test blocking with the process briefly stopped; manually edited rules pause sync.
29. After disabling native.enabled, run Sync to disable its live rule. Observe mode/settings restore do not silently undo stored Discord native rules.
30. Restart; ticket buttons, warnings/cooldowns, settings/history and recorded IDs still work; no duplicate public panel.
31. Unknown bot-created channels/roles and manual permission changes are untouched; same-name standalone category collision asks for an explicit ID binding.
32. Install Gate 1.0.1; concurrent delete of the same test message causes no false Unknown Message error. Forbidden still appears as an error.
33. Backups restore consistently into a disposable database; watchdog/task health and journal errors agree.
34. Holder Verification remains hidden, OG is assigned manually, and no previous project deployment was changed.

Record the test account, time, outcome and error IDs. Do not label deployment production-accepted until these live checks have been completed.
