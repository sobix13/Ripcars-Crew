from __future__ import annotations

import discord


def embed(cfg, title, description=""):
    result = discord.Embed(title=title[:256], description=description[:4000] or None, color=int(cfg["color"][1:], 16))
    result.set_footer(text=cfg["bot_name"])
    return result


async def reply(i, content=None, **kwargs):
    kwargs.setdefault("ephemeral", True)
    kwargs.setdefault("allowed_mentions", discord.AllowedMentions.none())
    if i.response.is_done():
        return await i.followup.send(content, **kwargs)
    return await i.response.send_message(content, **kwargs)


async def allowed(bot, i, admin=False):
    if not i.guild or i.user.bot:
        return False
    if i.user.id == i.guild.owner_id or i.user.guild_permissions.administrator:
        return True
    if admin:
        return False
    cfg, _ = await bot.db.settings(i.guild_id)
    return bool(set(cfg["moderator_roles"]) & {role.id for role in i.user.roles})


async def require(bot, i, admin=False):
    ok = await allowed(bot, i, admin)
    if not ok:
        await reply(i, "This control is for server admins." if admin else "This control is for configured moderators.")
    return ok


def can_target(guild, actor, target, permission=None):
    if target.bot or target.id == guild.owner_id or target.guild_permissions.administrator:
        raise ValueError("Owner, admin, and bot accounts are protected.")
    if actor and (target.id == actor.id or (actor.id != guild.owner_id and actor.top_role <= target.top_role)):
        raise ValueError("Your role must be above the target. You cannot target yourself.")
    if guild.me is None or guild.me.top_role <= target.top_role:
        raise ValueError("The bot role must be above the target.")
    if permission and not getattr(guild.me.guild_permissions, permission):
        raise ValueError(f"The bot is missing {permission.replace('_', ' ')}.")
    if permission and actor and not (actor.id == guild.owner_id or actor.guild_permissions.administrator or getattr(actor.guild_permissions, permission)):
        raise ValueError(f"You need {permission.replace('_', ' ')} for this action.")


class SafeView(discord.ui.View):
    def __init__(self, bot, *, owner=None, admin=False, staff=False, timeout=300):
        super().__init__(timeout=timeout)
        self.bot, self.owner, self.admin, self.staff = bot, owner, admin, staff

    async def interaction_check(self, i):
        if self.owner is not None and i.user.id != self.owner:
            await reply(i, "Open your own panel to use this control.")
            return False
        if self.admin or self.staff:
            return await require(self.bot, i, self.admin)
        return i.guild is not None

    async def on_error(self, i, error, item):
        if isinstance(error, ValueError):
            await reply(i, str(error))
        else:
            error_id = await self.bot.report(i.guild, "panel", error)
            await reply(i, f"Something went wrong. Error ID: {error_id}. Use /crew health.")


class SafeModal(discord.ui.Modal):
    def __init__(self, bot, *, title, owner=None, admin=False):
        super().__init__(title=title[:45], timeout=300)
        self.bot, self.owner, self.admin = bot, owner, admin

    async def interaction_check(self, i):
        if self.owner is not None and i.user.id != self.owner:
            await reply(i, "This form belongs to another member.")
            return False
        return await require(self.bot, i, True) if self.admin else i.guild is not None

    async def on_error(self, i, error):
        if isinstance(error, ValueError):
            await reply(i, str(error))
        else:
            error_id = await self.bot.report(i.guild, "form", error)
            await reply(i, f"Something went wrong. Error ID: {error_id}.")


class Confirm(SafeView):
    def __init__(self, bot, owner, action, *, admin=False, staff=True):
        super().__init__(bot, owner=owner, admin=admin, staff=staff)
        self.action = action
        self.used = False

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, i, button):
        if self.used:
            await reply(i, "This confirmation has already been used.")
            return
        self.used = True
        await i.response.defer(ephemeral=True)
        await self.action(i)
        self.stop()

    @discord.ui.button(label="Cancel")
    async def cancel(self, i, button):
        self.used = True
        await i.response.edit_message(content="Cancelled.", embed=None, view=None)
        self.stop()
