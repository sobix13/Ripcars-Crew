from __future__ import annotations

import discord

from . import config
from .coordination import check_ticket_scope
from .ui_common import Confirm, SafeModal, SafeView, allowed, embed, reply, require


class TicketModal(SafeModal):
    def __init__(self, bot, owner, cfg, topic):
        super().__init__(bot, title="Rip Cars | Open a ticket", owner=owner)
        self.topic = topic
        self.subject = discord.ui.TextInput(label=cfg["texts"]["ticket_subject"], max_length=200)
        self.details = discord.ui.TextInput(label=cfg["texts"]["ticket_details"], style=discord.TextStyle.paragraph, max_length=1800)
        self.add_item(self.subject)
        self.add_item(self.details)

    async def on_submit(self, i):
        if i.user.id != self.owner:
            raise ValueError("This form belongs to another member.")
        await i.response.defer(ephemeral=True)
        channel = await self.bot.tickets.open(i.guild, i.user, self.topic, self.subject.value, self.details.value)
        await reply(i, f"Your pit stop is ready: <#{channel.id}>.")


class TopicSelect(discord.ui.Select):
    def __init__(self, cfg):
        super().__init__(placeholder="Choose a support topic", custom_id="ripcars:crew:ticket:topic",
                         options=[discord.SelectOption(label=cat["label"], value=cat["key"]) for cat in cfg["tickets"]["categories"] if cat["enabled"]] or [discord.SelectOption(label="Tickets are paused", value="paused")])

    async def callback(self, i):
        bot = self.view.bot
        cfg, _ = await bot.db.settings(i.guild_id)
        if not cfg["tickets"]["enabled"]:
            await reply(i, "Tickets are paused right now.")
            return
        await i.response.send_modal(TicketModal(bot, i.user.id, cfg, self.values[0]))


class TicketPanel(SafeView):
    def __init__(self, bot, cfg=None):
        super().__init__(bot, timeout=None)
        self.add_item(TopicSelect(cfg or config.defaults()))


class TicketControls(SafeView):
    def __init__(self, bot):
        super().__init__(bot, timeout=None)

    @discord.ui.button(label="Claim", custom_id="ripcars:crew:ticket:claim", style=discord.ButtonStyle.primary)
    async def claim(self, i, button):
        if not await require(self.bot, i):
            return
        await i.response.defer(ephemeral=True)
        number = await self.bot.tickets.claim(i.guild, i.channel, i.user)
        await reply(i, f"Ticket #{number:04d} is assigned to you.")

    @discord.ui.button(label="Close", custom_id="ripcars:crew:ticket:close")
    async def close(self, i, button):
        ticket = await self.bot.db.ticket(i.guild_id, i.channel_id)
        if not ticket or (ticket["user"] != i.user.id and not await allowed(self.bot, i)):
            await reply(i, "Only the ticket opener or staff can close this ticket.")
            return
        async def action(confirmation):
            current = await self.bot.db.ticket(i.guild_id, i.channel_id)
            if not current or (current["user"] != confirmation.user.id and not await allowed(self.bot, confirmation)):
                raise ValueError("Ticket permissions changed. Ask staff for help.")
            number = await self.bot.tickets.transition(confirmation.guild, confirmation.channel, confirmation.user)
            await reply(confirmation, f"Ticket #{number:04d} is closed. It is now visible to staff only.")
        await reply(i, "Close this ticket? The opener will lose access to the archived channel.", view=Confirm(self.bot, i.user.id, action, staff=False))

    @discord.ui.button(label="Reopen", custom_id="ripcars:crew:ticket:reopen")
    async def reopen(self, i, button):
        if not await require(self.bot, i):
            return
        async def action(confirmation):
            if not await require(self.bot, confirmation):
                return
            number = await self.bot.tickets.transition(confirmation.guild, confirmation.channel, confirmation.user, False)
            await reply(confirmation, f"Ticket #{number:04d} is open again.")
        await reply(i, "Reopen this ticket and restore the opener's access?", view=Confirm(self.bot, i.user.id, action))

    @discord.ui.button(label="Delete archive", custom_id="ripcars:crew:ticket:delete", style=discord.ButtonStyle.danger)
    async def delete(self, i, button):
        if not await require(self.bot, i):
            return
        async def action(confirmation):
            await self.bot.tickets.delete(confirmation.guild, confirmation.channel, confirmation.user.id)
            await reply(confirmation, "Closed ticket channel deleted. The database audit record remains.")
        await reply(i, "Permanently delete this closed ticket channel? Export its transcript first if needed.", view=Confirm(self.bot, i.user.id, action))


async def publish(bot, guild, actor):
    async with bot.db.lease(guild.id):
        cfg, _ = await bot.db.settings(guild.id)
        await check_ticket_scope(bot, guild, cfg)
        channel = guild.get_channel(cfg["tickets"]["panel_channel"])
        rows = await bot.db.query("SELECT * FROM panels WHERE guild=? AND key='tickets'", (guild.id,))
        view = TicketPanel(bot, cfg)
        card = embed(cfg, cfg["texts"]["ticket_title"], cfg["texts"]["ticket_description"])
        if rows:
            if rows[0]["channel"] != channel.id:
                raise ValueError("A ticket panel is registered in a different channel. Move/delete it explicitly before replacing its binding.")
            try:
                message = await channel.fetch_message(rows[0]["message"])
            except discord.NotFound:
                raise ValueError("The registered panel was deleted. In Discord, use /crew forget-panel to confirm replacement, then publish again.")
            await message.edit(embed=card, view=view, allowed_mentions=discord.AllowedMentions.none())
        else:
            message = await channel.send(embed=card, view=view, allowed_mentions=discord.AllowedMentions.none())
            await bot.db.execute("INSERT INTO panels VALUES(?,'tickets',?,?)", (guild.id, channel.id, message.id))
        await bot.db.audit(guild.id, actor, "ticket_panel", f"channel={channel.id}; message={message.id}")
        return message.id
