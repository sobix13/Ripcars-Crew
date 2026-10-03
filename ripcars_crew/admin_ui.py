"""Branch-first Discord settings UI; all behavior configurable without source edits."""
from __future__ import annotations

import io
import json

import discord

from . import config, coordination, operations, ticket_ui
from .detection import inspect
from .ui_common import Confirm, SafeModal, SafeView, embed, reply, require


SECTIONS = {
    "setup": ("Quick setup", ["moderator_roles", "member_role", "log_channel", "tickets.panel_channel", "tickets.open_category", "tickets.closed_category"]),
    "protection": ("Chat protection", ["guard.enabled", "guard.mode", "guard.scan_webhooks", "guard.scan_bots"]),
    "words": ("Words & links", ["guard.words", "guard.allowed_domains", "guard.blocked_domains", "guard.official_domains", "guard.blocked_extensions", "guard.protected_names"]),
    "spam": ("Spam thresholds", ["guard.mention_limit", "guard.flood_count", "guard.flood_seconds", "guard.duplicate_count", "guard.duplicate_seconds", "guard.caps_ratio", "guard.caps_min_letters", "guard.newline_limit"]),
    "exceptions": ("Exceptions & trusted IDs", ["guard.exempt_roles", "guard.exempt_channels", "guard.include_channels", "guard.trusted_bot_ids"]),
    "warnings": ("Warnings & cases", ["guard.warn_expiry_days", "guard.timeout_after", "guard.timeout_seconds", "guard.heat_threshold", "guard.heat_decay_per_minute", "guard.notice_seconds"]),
    "tickets": ("Tickets", ["tickets.enabled", "tickets.categories", "tickets.create_cooldown_seconds", "tickets.auto_delete_days", "tickets.transcript_messages", "tickets.transcript_bytes"]),
    "messages": ("Messages & brand", ["brand", "bot_name", "color", "website"] + ["texts." + key for key in config.DEFAULTS["texts"]]),
    "controls": ("Chat controls", []),
    "monitor": ("Monitoring & data", ["monitoring." + key for key in config.DEFAULTS["monitoring"]]),
    "native": ("Native AutoMod", ["native.enabled"]),
    "gate": ("Gate & coordination", []),
    "guide": ("Guide & troubleshooting", []),
}

GUIDE = (
    "1. Quick setup: select staff, member role, and private log. Import Gate IDs if available.\n"
    "2. Gate: activate this bot's support ID, then hand off Ticket/Open/Closed.\n"
    "3. Tickets: publish the panel, then enable tickets.\n"
    "4. Chat protection: use observe to review hits, then protect to enforce. Filters have their own switches/actions/weights.\n"
    "5. Words: exact avoids matching inside innocent words; contains is broader. No regex executes.\n"
    "6. Chat controls: native slowmode affects a channel; member cooldown deletes that member's posts while online; timeout is server-wide.\n"
    "7. Native AutoMod is optional and continues blocking while the bot is offline. Disable and sync it separately.\n"
    "8. Health reports blockers, channel coverage, pending actions, background tasks and error IDs.\n"
    "No verification role, OG assignment, role-claim panel, holder check, wallet action or external scam-site fetch happens here.\n"
    "Commands: /crew panel, setup, health, doctor, member, warn, revoke, cases, timeout, untimeout, kick, ban, purge, cooldown, slowmode, test-rule, export-config, import-config, ticket-list, transcript, guide.")


async def checked_save(bot, guild, cfg, revision, actor):
    config.validate(cfg)
    if cfg["guard"]["enabled"] or cfg["tickets"]["enabled"]:
        blockers, _ = await operations.doctor(bot, guild, cfg)
        if blockers:
            raise ValueError("Setup blockers:\n" + "\n".join(blockers))
    if cfg["tickets"]["enabled"]:
        await coordination.check_ticket_scope(bot, guild, cfg)
        rows = await bot.db.query("SELECT * FROM panels WHERE guild=? AND key='tickets'", (guild.id,))
        if not rows:
            raise ValueError("Publish the ticket panel before enabling tickets.")
    await bot.db.save(guild.id, cfg, revision, actor)


class SettingModal(SafeModal):
    def __init__(self, bot, owner, cfg, revision, path):
        super().__init__(bot, title="Edit " + path.rsplit(".", 1)[-1], owner=owner, admin=True)
        self.cfg, self.revision, self.path = cfg, revision, path
        old = config.get_path(cfg, path)
        current = old if isinstance(old, str) else json.dumps(old, ensure_ascii=False, indent=2)
        if len(current) > 4000:
            raise ValueError("This list is too large for a Discord form. Use Add/remove entry, or export-config/import-config.")
        self.value = discord.ui.TextInput(label=path[:45], default=current, style=discord.TextStyle.paragraph, max_length=4000)
        self.add_item(self.value)

    async def on_submit(self, i):
        if not await require(self.bot, i, True):
            return
        cfg = config.parse_value(self.cfg, self.path, self.value.value)
        await checked_save(self.bot, i.guild, cfg, self.revision, i.user.id)
        await reply(i, f"Saved {self.path}. Public panels need republish; native rules need explicit Sync.")


class ListEntryModal(SafeModal):
    def __init__(self, bot, owner, cfg, revision, path, remove=False):
        super().__init__(bot, title="Remove entry" if remove else "Add entry", owner=owner, admin=True)
        self.cfg, self.revision, self.path, self.remove = cfg, revision, path, remove
        self.text = discord.ui.TextInput(label="Word, phrase, domain, name or ID", max_length=253)
        self.add_item(self.text)
        if path == "guard.words":
            self.mode = discord.ui.TextInput(label="Match mode: exact / phrase / contains", default="exact", max_length=8)
            self.add_item(self.mode)

    async def on_submit(self, i):
        if not await require(self.bot, i, True):
            return
        old = list(config.get_path(self.cfg, self.path))
        value = {"text": self.text.value.strip(), "mode": self.mode.value.strip().lower()} if self.path == "guard.words" else self.text.value.strip()
        if old and type(old[0]) is int or self.path in ("moderator_roles", "guard.exempt_roles", "guard.exempt_channels", "guard.include_channels", "guard.trusted_bot_ids"):
            try:
                value = int(value)
            except (ValueError, TypeError) as exc:
                raise ValueError("Enter a positive numeric ID.") from exc
        if self.remove:
            if value not in old:
                raise ValueError("That exact entry is not in the list.")
            old.remove(value)
        else:
            if value in old:
                raise ValueError("Entry already exists.")
            old.append(value)
        cfg = config.set_path(self.cfg, self.path, old)
        await checked_save(self.bot, i.guild, cfg, self.revision, i.user.id)
        await reply(i, "List saved. Native word rules require an explicit Sync if enabled.")


class PathSelect(discord.ui.Select):
    def __init__(self, paths):
        super().__init__(placeholder="Choose a setting to edit", options=[discord.SelectOption(label=p[:100], value=p) for p in paths])

    async def callback(self, i):
        cfg, revision = await self.view.bot.db.settings(i.guild_id)
        path = self.values[0]
        value = config.get_path(cfg, path)
        if isinstance(value, list) and path != "tickets.categories":
            await reply(i, f"{path}: {len(value)} entries.\n{json.dumps(value, ensure_ascii=False)[:1500]}", view=ListEditor(self.view.bot, i.user.id, cfg, revision, path))
        else:
            await i.response.send_modal(SettingModal(self.view.bot, i.user.id, cfg, revision, path))


class ListEditor(SafeView):
    def __init__(self, bot, owner, cfg, revision, path):
        super().__init__(bot, owner=owner, admin=True)
        self.cfg, self.revision, self.path = cfg, revision, path

    @discord.ui.button(label="Add entry", style=discord.ButtonStyle.primary)
    async def add(self, i, button):
        await i.response.send_modal(ListEntryModal(self.bot, i.user.id, self.cfg, self.revision, self.path))

    @discord.ui.button(label="Remove entry")
    async def remove(self, i, button):
        await i.response.send_modal(ListEntryModal(self.bot, i.user.id, self.cfg, self.revision, self.path, True))

    @discord.ui.button(label="Edit list (JSON)")
    async def edit(self, i, button):
        await i.response.send_modal(SettingModal(self.bot, i.user.id, self.cfg, self.revision, self.path))


class FilterSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder="Choose a detection rule", options=[discord.SelectOption(label=key.replace("_", " ").title(), value=key) for key in config.FILTERS])

    async def callback(self, i):
        cfg, revision = await self.view.bot.db.settings(i.guild_id)
        await i.response.send_modal(FilterModal(self.view.bot, i.user.id, cfg, revision, self.values[0]))


class FilterModal(SafeModal):
    def __init__(self, bot, owner, cfg, revision, key):
        super().__init__(bot, title="Filter: " + key, owner=owner, admin=True)
        self.cfg, self.revision, self.key = cfg, revision, key
        rule = cfg["guard"]["filters"][key]
        self.enabled = discord.ui.TextInput(label="Enabled: true / false", default=str(rule["enabled"]).lower())
        self.action = discord.ui.TextInput(label="Action: alert / delete / warn / timeout", default=rule["action"])
        self.weight = discord.ui.TextInput(label="Heat weight: 0–100", default=str(rule["weight"]))
        for item in (self.enabled, self.action, self.weight):
            self.add_item(item)

    async def on_submit(self, i):
        if not await require(self.bot, i, True):
            return
        path = "guard.filters." + self.key
        try:
            rule = {"enabled": json.loads(self.enabled.value.lower()), "action": self.action.value.strip().lower(), "weight": int(self.weight.value)}
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Use true/false and a numeric heat weight.") from exc
        cfg = config.set_path(self.cfg, path, rule)
        await checked_save(self.bot, i.guild, cfg, self.revision, i.user.id)
        await reply(i, f"Saved {self.key}. Changes apply to new messages immediately.")


class BranchSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder="Choose a section", options=[discord.SelectOption(label=label, value=key) for key, (label, _) in SECTIONS.items()])

    async def callback(self, i):
        cfg, _ = await self.view.bot.db.settings(i.guild_id)
        section = self.values[0]
        await i.response.edit_message(embed=section_embed(cfg, section), view=Section(self.view.bot, i.user.id, section), content=None)


def section_embed(cfg, section):
    label, paths = SECTIONS[section]
    description = "\n".join(f"{p}: {str(config.get_path(cfg, p))[:160]}" for p in paths)
    if section == "guide":
        description = GUIDE
    elif section == "gate":
        description = "Import IDs is read-only for Gate resources. Configure/activate the support integration and confirm responsibility handoff from Gate. No role claims or CAPTCHA controller is moved. Shared server-setup leases coordinate participating bots; arbitrary third-party edits remain possible."
    elif section == "controls":
        description = "Use /crew cooldown for a member in one channel; /crew slowmode for temporary native channel-wide slowmode; /crew timeout for server-wide timeout. No channel control activates automatically. Expired temporary slowmode restores only if its owned value has not been manually edited."
    elif section == "native":
        description += "\nSync is explicit. Only the bot's own rule ID is changed. If an admin edits that rule, syncing stops for review. Bot mode changes alone do not remove a previously synced native rule."
    return embed(cfg, "Ripcars Crew | " + label, description or "Choose an action below.")


class Hub(SafeView):
    def __init__(self, bot, owner):
        super().__init__(bot, owner=owner, admin=True)
        self.add_item(BranchSelect())

    @discord.ui.button(label="Health", row=1)
    async def health(self, i, button):
        await i.response.defer(ephemeral=True)
        await reply(i, embed=await operations.health(self.bot, i.guild))

    @discord.ui.button(label="Setup wizard", row=1, style=discord.ButtonStyle.primary)
    async def setup(self, i, button):
        cfg, revision = await self.bot.db.settings(i.guild_id)
        await reply(i, embed=section_embed(cfg, "setup"), view=SetupWizard(self.bot, i.user.id, cfg, revision))


class Section(Hub):
    def __init__(self, bot, owner, section):
        super().__init__(bot, owner)
        paths = SECTIONS[section][1]
        if paths:
            self.add_item(PathSelect(paths))
        if section == "protection":
            self.add_item(FilterSelect())
        actions = {
            "gate": [("Import Gate IDs", self.import_gate)],
            "tickets": [("Publish/update panel", self.publish), ("Review incomplete tickets", self.repair), ("Create standalone containers", self.standalone)],
            "native": [("Sync native rule", self.native_sync)],
            "monitor": [("Backup now", self.backup), ("Export settings", self.export), ("Recent events", self.events), ("Statistics", self.stats)],
            "warnings": [("Recent cases", self.cases)],
        }
        for label, callback in actions.get(section, []):
            button = discord.ui.Button(label=label)
            button.callback = callback
            self.add_item(button)

    async def import_gate(self, i):
        async def action(j):
            cfg = await coordination.import_gate(self.bot, j.guild, j.user.id)
            await reply(j, embed=section_embed(cfg, "setup"))
        await reply(i, "Import registered Gate IDs into Crew settings? This does not activate or take ownership of anything.", view=Confirm(self.bot, i.user.id, action, admin=True))

    async def publish(self, i):
        await i.response.defer(ephemeral=True)
        message_id = await ticket_ui.publish(self.bot, i.guild, i.user.id)
        await reply(i, f"Ticket panel ready. Message ID: {message_id}.")

    async def repair(self, i):
        async def action(j):
            results = await self.bot.tickets.repair(j.guild, j.user.id)
            await reply(j, "\n".join(results)[:1900])
        await reply(i, "Review ticket records against Discord? Only unambiguous completed transitions are settled. Missing channels are not recreated.", view=Confirm(self.bot, i.user.id, action, admin=True))

    async def standalone(self, i):
        async def action(j):
            cfg = await coordination.standalone_setup(self.bot, j.guild, j.user.id)
            await reply(j, embed=section_embed(cfg, "setup"))
        await reply(i, "Create private Open and Closed categories only? Do not use this if Gate already created them; import and hand off instead.", view=Confirm(self.bot, i.user.id, action, admin=True))

    async def native_sync(self, i):
        async def action(j):
            await reply(j, await self.bot.native.sync(j.guild, j.user.id))
        await reply(i, "Apply current word rules to the bot's own Discord AutoMod rule? Native blocking works while the bot is offline.", view=Confirm(self.bot, i.user.id, action, admin=True))

    async def backup(self, i):
        await i.response.defer(ephemeral=True)
        await self.bot.take_backup()
        await reply(i, "Operational database backup completed and integrity checked.")

    async def export(self, i):
        cfg, revision = await self.bot.db.settings(i.guild_id)
        file = discord.File(io.BytesIO(json.dumps({"revision": revision, "config": cfg}, indent=2, ensure_ascii=False).encode()), filename="ripcars-crew-config.json")
        await reply(i, "Settings export contains no bot token or member answers.", file=file)

    async def cases(self, i):
        rows = await self.bot.db.query("SELECT * FROM cases WHERE guild=? ORDER BY id DESC LIMIT 15", (i.guild_id,))
        await reply(i, "\n".join(f"#{r['id']} · member {r['user']} · {r['action']} · {r['status']} · revoked={r['revoked']}" for r in rows) or "No cases yet.")

    async def events(self, i):
        rows = await self.bot.db.query("SELECT * FROM audit WHERE guild=? ORDER BY id DESC LIMIT 15", (i.guild_id,))
        await reply(i, "\n".join(f"<t:{int(r['at'])}:t> · {r['area']} · actor {r['actor']}\n{r['detail'][:90]}" for r in rows)[:1900] or "No recorded events.")

    async def stats(self, i):
        import time
        counts = await self.bot.db.query("SELECT action,COUNT(*) AS n FROM cases WHERE guild=? AND at>=? GROUP BY action", (i.guild_id, time.time() - 86400))
        tickets = await self.bot.db.query("SELECT status,COUNT(*) AS n FROM tickets WHERE guild=? GROUP BY status", (i.guild_id,))
        cfg, _ = await self.bot.db.settings(i.guild_id)
        await reply(i, embed=embed(cfg, "Rip Cars | Statistics", "Cases in 24h:\n" + ("\n".join(f"{r['action']}: {r['n']}" for r in counts) or "None") + "\n\nTickets:\n" + ("\n".join(f"{r['status']}: {r['n']}" for r in tickets) or "None")))


class SetupWizard(SafeView):
    def __init__(self, bot, owner, cfg, revision, page=0):
        super().__init__(bot, owner=owner, admin=True)
        self.cfg, self.revision, self.page = cfg, revision, page
        if page == 0:
            staff = discord.ui.RoleSelect(placeholder="Moderator roles", min_values=1, max_values=10,
                 default_values=[discord.SelectDefaultValue(id=v, type=discord.SelectDefaultValueType.role) for v in cfg["moderator_roles"][:10]])
            member = discord.ui.RoleSelect(placeholder="Member role after CAPTCHA", max_values=1,
                 default_values=[discord.SelectDefaultValue(id=cfg["member_role"], type=discord.SelectDefaultValueType.role)] if cfg["member_role"] else [])
            log = discord.ui.ChannelSelect(placeholder="Private staff log", channel_types=[discord.ChannelType.text],
                 default_values=[discord.SelectDefaultValue(id=cfg["log_channel"], type=discord.SelectDefaultValueType.channel)] if cfg["log_channel"] else [])
            async def staff_cb(i):
                self.cfg["moderator_roles"] = [v.id for v in staff.values]
                staff.default_values = [discord.SelectDefaultValue(id=v, type=discord.SelectDefaultValueType.role) for v in self.cfg["moderator_roles"]]
                await self.refresh(i)
            async def member_cb(i):
                self.cfg["member_role"] = member.values[0].id
                member.default_values = [discord.SelectDefaultValue(id=self.cfg["member_role"], type=discord.SelectDefaultValueType.role)]
                await self.refresh(i)
            async def log_cb(i):
                self.cfg["log_channel"] = log.values[0].id
                log.default_values = [discord.SelectDefaultValue(id=self.cfg["log_channel"], type=discord.SelectDefaultValueType.channel)]
                await self.refresh(i)
            for item, callback in ((staff, staff_cb), (member, member_cb), (log, log_cb)):
                item.callback = callback
                self.add_item(item)
        else:
            for key, label, channel_type in (("panel_channel", "Ticket panel channel", discord.ChannelType.text),
                                              ("open_category", "Open tickets category", discord.ChannelType.category),
                                              ("closed_category", "Closed tickets category", discord.ChannelType.category)):
                value = cfg["tickets"][key]
                select = discord.ui.ChannelSelect(placeholder=label, channel_types=[channel_type], default_values=[discord.SelectDefaultValue(id=value, type=discord.SelectDefaultValueType.channel)] if value else [])
                async def callback(i, selected=select, field=key):
                    self.cfg["tickets"][field] = selected.values[0].id
                    selected.default_values = [discord.SelectDefaultValue(id=self.cfg["tickets"][field], type=discord.SelectDefaultValueType.channel)]
                    await self.refresh(i)
                select.callback = callback
                self.add_item(select)

    async def refresh(self, i):
        await i.response.edit_message(embed=section_embed(self.cfg, "setup"), view=self, content=None)

    @discord.ui.button(label="Other setup page", row=3)
    async def switch(self, i, button):
        await i.response.edit_message(embed=section_embed(self.cfg, "setup"), view=SetupWizard(self.bot, i.user.id, self.cfg, self.revision, 1 - self.page))

    @discord.ui.button(label="Review & save", style=discord.ButtonStyle.primary, row=3)
    async def save(self, i, button):
        async def action(j):
            await checked_save(self.bot, j.guild, self.cfg, self.revision, j.user.id)
            await reply(j, "Setup saved. Open /crew doctor before enabling modules.")
        await reply(i, embed=section_embed(self.cfg, "setup"), view=Confirm(self.bot, i.user.id, action, admin=True))


class RuleTestModal(SafeModal):
    def __init__(self, bot, owner):
        super().__init__(bot, title="Test rules without a sanction", owner=owner, admin=True)
        self.text = discord.ui.TextInput(label="Example message", style=discord.TextStyle.paragraph, max_length=2000)
        self.add_item(self.text)

    async def on_submit(self, i):
        if not await require(self.bot, i, True):
            return
        cfg, _ = await self.bot.db.settings(i.guild_id)
        hits = inspect(self.text.value, cfg["guard"])
        await reply(i, "\n".join(f"{hit.rule}: {hit.reason} → {cfg['guard']['filters'][hit.rule]['action']}" for hit in hits) or "No configured static rule matched. Burst rules need real message sequences.")
