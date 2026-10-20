"""Channel-first controls reuse the existing protection and revision checks."""
import copy
import discord

from . import config
from .ui_common import SafeView, embed, reply

LABELS={'text':'Text','images':'Images / GIFs','videos':'Videos','files':'Other files','links':'Links','mentions':'Mentions'}


class ChannelRules(SafeView):
    def __init__(self,bot,owner,cfg,revision,channel=None):
        super().__init__(bot,owner=owner,admin=True)
        self.cfg,self.revision,self.channel=cfg,revision,channel
        menu=discord.ui.ChannelSelect(placeholder='Choose a channel',channel_types=[discord.ChannelType.text,discord.ChannelType.forum],row=0)
        async def select(i):
            await i.response.defer(ephemeral=True)
            ch=i.guild.get_channel(menu.values[0].id)
            if ch is None:raise ValueError('Choose an existing channel in this server.')
            fresh,rev=await bot.db.settings(i.guild_id)
            await show(i,bot,fresh,rev,ch)
        menu.callback=select;self.add_item(menu)
        if channel is not None:
            policy=cfg.get('channel_rules',{}).get(str(channel.id),{key:True for key in config.CONTENT_TYPES})
            for n,key in enumerate(config.CONTENT_TYPES):
                button=discord.ui.Button(label=LABELS[key]+(': Allowed' if policy[key] else ': Blocked'),style=discord.ButtonStyle.success if policy[key] else discord.ButtonStyle.secondary,row=1+n//3)
                async def toggle(i,k=key):
                    await i.response.defer(ephemeral=True)
                    updated=copy.deepcopy(self.cfg)
                    value=updated['channel_rules'].setdefault(str(channel.id),{x:True for x in config.CONTENT_TYPES})
                    value[k]=not value[k]
                    from .admin_ui import checked_save
                    await checked_save(bot,i.guild,updated,self.revision,i.user.id)
                    await show(i,bot,updated,self.revision+1,channel)
                button.callback=toggle;self.add_item(button)
            reset=discord.ui.Button(label='Use server defaults',row=3)
            async def clear(i):
                await i.response.defer(ephemeral=True)
                updated=copy.deepcopy(self.cfg);updated['channel_rules'].pop(str(channel.id),None)
                from .admin_ui import checked_save
                await checked_save(bot,i.guild,updated,self.revision,i.user.id)
                await show(i,bot,updated,self.revision+1,channel)
            reset.callback=clear;self.add_item(reset)

    @discord.ui.button(label='Home',row=4)
    async def home(self,i,button):
        from .admin_ui import Hub
        await reply(i,'Choose Channels, Features or Tickets. More settings keeps all detailed controls.',view=Hub(self.bot,i.user.id))


async def show(i,bot,cfg,revision,channel=None):
    text='Choose a channel, then click a content type to allow or block it. Threads follow their parent channel. Changes affect new messages.'
    text+='\nProtection: '+('On' if cfg['guard']['enabled'] else 'Off. Turn it on under Features to enforce these rules.')
    if not cfg['guard']['filters']['channel_content']['enabled']:text+='\nChannel content enforcement is off in detailed filter settings.'
    if channel is not None:
        text+='\nChannel: '+channel.mention+'\nAllowed means Crew does not block this content type. Discord permissions, Gate rules and safety filters still apply.'
        if not channel.permissions_for(i.guild.me).view_channel or not channel.permissions_for(i.guild.me).manage_messages:
            text+='\nCrew needs View Channel and Manage Messages here to enforce protection.'
    await reply(i,embed=embed(cfg,'Ripcars Crew | Channel rules',text),view=ChannelRules(bot,i.user.id,cfg,revision,channel))


class Features(SafeView):
    def __init__(self,bot,owner,cfg,revision):
        super().__init__(bot,owner=owner,admin=True)
        for row,(label,path) in enumerate((('Chat protection','guard.enabled'),('Tickets','tickets.enabled'),('Activity counters','monitoring.activity_enabled'),('Audit event history','monitoring.audit_events'))):
            enabled=config.get_path(cfg,path)
            button=discord.ui.Button(label=label+(': On' if enabled else ': Off'),style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.secondary,row=row)
            async def toggle(i,p=path):
                await i.response.defer(ephemeral=True)
                from .admin_ui import checked_save
                updated=config.set_path(cfg,p,not config.get_path(cfg,p))
                await checked_save(bot,i.guild,updated,revision,i.user.id)
                await reply(i,'Saved. Disabled features stop their own processing. Existing data is retained.',view=Features(bot,i.user.id,updated,revision+1))
            button.callback=toggle;self.add_item(button)

    @discord.ui.button(label='Home',row=4)
    async def home(self,i,button):
        from .admin_ui import Hub
        await reply(i,'Choose a section.',view=Hub(self.bot,i.user.id))
