# Quiet launch: Crew 1.1.0

Open `/crew panel`. The first page contains Channel rules, Features on / off, Tickets, and More settings. Every existing moderation command and advanced setting remains available.

## Channel rules

Choose an existing channel, then toggle Text, Images, Videos, Files, Links and Mentions between Allowed and Blocked. Threads inherit their parent channel's choice. Use server defaults removes only that channel's extra policy.

These controls restrict content. They do not grant Discord permissions, override a private channel, or disable separate safety filters. Staff exemptions and existing rule actions still apply. Chat protection and the Channel content filter must be enabled for these policies to act.

## Features and resource use

Features on / off controls Chat protection, Tickets, Activity counters and Audit history. Disabled protection does not run message filtering, burst tracking or cooldown queries. Disabled activity counters do not write activity records. Disabled ticket monitoring does not sweep tickets. Expired temporary restrictions still receive their necessary cleanup.

Discord Native AutoMod runs independently. If an owned native rule is still enabled, turn Native AutoMod off and Sync under More settings before turning Chat protection off. Changing a bot setting alone cannot turn off a rule running on Discord.

Crew skips inaccessible channels rather than repeatedly attempting moderation there. You may manually archive and hide channels; no special archive setup is necessary. Normal maintenance pruning runs hourly instead of every minute.

The audit event handler also handles Discord audit entries whose target role is unavailable. Existing tickets, cases, settings and member data are retained.
