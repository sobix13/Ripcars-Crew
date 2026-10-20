import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from ripcars_crew import config, detection
from ripcars_crew.bot import CrewBot
from ripcars_crew.channel_ui import ChannelRules, Features
from ripcars_crew.admin_ui import Hub
from tests.fakes import AsyncFixture, message, interaction


class QuietLaunchTests(AsyncFixture, unittest.IsolatedAsyncioTestCase):
    async def policy(self, **changes):
        cfg,rev=await self.bot.db.settings(101)
        cfg['channel_rules']['305']={key:changes.get(key,True) for key in config.CONTENT_TYPES}
        await self.bot.db.save(101,cfg,rev,603)
        return cfg

    async def test_channel_rule_enforced_and_other_channels_unchanged(self):
        await self.policy(links=False)
        msg=message(self.bot,'https://example.com')
        hits=await self.bot.moderation.process(msg)
        self.assertIn('channel_content',[h.rule for h in hits]);msg.delete.assert_awaited_once()
        other=message(self.bot,'https://example.com',message_id=702,channel=self.guild.log)
        await self.bot.moderation.process(other);other.delete.assert_not_awaited()

    async def test_media_policy_and_thread_inheritance(self):
        cfg=await self.policy(images=False,videos=False,files=False,mentions=False,text=False)
        thread=SimpleNamespace(id=999,parent_id=305)
        self.assertEqual(config.channel_policy(cfg,thread),cfg['channel_rules']['305'])
        for mime in ('image/png','video/mp4',None):
            msg=message(self.bot);msg.content='';msg.attachments=[SimpleNamespace(content_type=mime)]
            self.assertTrue(detection.content_hits(msg,config.channel_policy(cfg,self.guild.general)))
        msg=message(self.bot,'@here hello')
        self.assertTrue(detection.content_hits(msg,config.channel_policy(cfg,self.guild.general)))

    async def test_disabled_guard_cooldowns_burst_and_activity_do_no_work(self):
        cfg,rev=await self.bot.db.settings(101)
        cfg['guard']['enabled']=False;cfg['monitoring']['activity_enabled']=False
        await self.bot.db.save(101,cfg,rev,603)
        with patch.object(self.bot.db,'query',AsyncMock()) as query, patch.object(self.bot.db,'activity_message',AsyncMock()) as activity, patch.object(self.bot.moderation.burst,'record') as burst:
            await self.bot.moderation.process(message(self.bot,'spam'))
            await self.bot.moderation.activity(message(self.bot,'spam'))
        query.assert_not_awaited();activity.assert_not_awaited();burst.assert_not_called()

    async def test_disabled_filters_skip_expensive_text_work(self):
        guard=config.defaults()['guard']
        for rule in guard['filters'].values():rule['enabled']=False
        with patch.object(detection,'hosts') as hosts, patch.object(detection,'normalize') as normalize:
            self.assertEqual(detection.inspect('https://example.com',guard),[])
        hosts.assert_not_called();normalize.assert_not_called()
        cfg,rev=await self.bot.db.settings(101);cfg['guard']=guard;cfg['guard']['enabled']=True
        await self.bot.db.save(101,cfg,rev,603)
        with patch.object(self.bot.db,'query',AsyncMock()) as query,patch.object(self.bot.moderation.burst,'record') as burst:
            await self.bot.moderation.process(message(self.bot))
        query.assert_not_awaited();burst.assert_not_called()

    async def test_inaccessible_channel_does_not_call_discord(self):
        await self.policy(links=False)
        self.guild.general.permissions_for=lambda member:discord.Permissions.none()
        msg=message(self.bot,'https://example.com')
        self.assertEqual(await self.bot.moderation.process(msg),[])
        msg.delete.assert_not_awaited()

    async def test_channel_buttons_save_and_preserve_unrelated_settings(self):
        cfg,rev=await self.bot.db.settings(101)
        view=ChannelRules(self.bot,603,cfg,rev,self.guild.general)
        button=next(c for c in view.children if getattr(c,'label','').startswith('Links:'))
        await button.callback(interaction(self.bot))
        after,_=await self.bot.db.settings(101)
        self.assertFalse(after['channel_rules']['305']['links'])
        self.assertEqual(after['tickets'],cfg['tickets']);self.assertEqual(after['guard'],cfg['guard'])
        with self.assertRaises(ValueError):await button.callback(interaction(self.bot))

    async def test_legacy_read_is_additive_and_does_not_write(self):
        cfg,rev=await self.bot.db.settings(101)
        cfg.pop('channel_rules');cfg['monitoring'].pop('activity_enabled');cfg['guard']['filters'].pop('channel_content')
        raw=json.dumps(cfg)
        await self.bot.db.execute('UPDATE crew_settings SET body=? WHERE guild=101',(raw,))
        value,current=await self.bot.db.settings(101)
        config.validate(value);self.assertEqual(value['channel_rules'],{});self.assertEqual(current,rev)
        self.assertEqual((await self.bot.db.query('SELECT body FROM crew_settings WHERE guild=101'))[0]['body'],raw)

    async def test_new_views_are_small_and_all_advanced_sections_remain(self):
        cfg,rev=await self.bot.db.settings(101)
        hub=Hub(self.bot,603)
        self.assertEqual(len(next(c for c in hub.children if isinstance(c,discord.ui.Select)).options),4)
        advanced=Hub(self.bot,603,advanced=True)
        self.assertGreater(len(next(c for c in advanced.children if isinstance(c,discord.ui.Select)).options),4)
        for view in (ChannelRules(self.bot,603,cfg,rev,self.guild.general),Features(self.bot,603,cfg,rev)):
            self.assertLessEqual(len(view.to_components()),5)

    async def test_monitor_disabled_modules_skip_sweeps_and_native_fetch(self):
        cfg,rev=await self.bot.db.settings(101);cfg['tickets']['enabled']=False
        await self.bot.db.save(101,cfg,rev,603)
        self.bot._prune_after={}
        self.guild.fetch_automod_rule=AsyncMock()
        with patch.object(self.bot.db,'prune',AsyncMock()) as prune,patch.object(self.bot.tickets,'sweep',AsyncMock()) as sweep:
            await CrewBot.monitor_cycle(self.bot,self.guild)
            await CrewBot.monitor_cycle(self.bot,self.guild)
        prune.assert_awaited_once();sweep.assert_not_awaited();self.guild.fetch_automod_rule.assert_not_awaited()

    async def test_on_message_reads_settings_once(self):
        with patch.object(self.bot.db,'settings',wraps=self.bot.db.settings) as settings:
            await CrewBot.on_message(self.bot,message(self.bot))
        self.assertEqual(settings.await_count,1)

    async def test_invalid_channel_policy_rejected(self):
        for policy in ({'links':False},{key:'yes' for key in config.CONTENT_TYPES}):
            cfg=config.defaults();cfg['channel_rules']={'305':policy}
            with self.assertRaises(ValueError):config.validate(cfg)

    async def test_guard_off_requires_disabling_existing_discord_rule(self):
        from ripcars_crew.admin_ui import checked_save
        cfg,rev=await self.bot.db.settings(101)
        cfg['guard']['enabled']=False
        await self.bot.db.execute("INSERT INTO native VALUES(?,?,?,'active')",(101,123,json.dumps({'enabled':True})))
        with self.assertRaisesRegex(ValueError,'Sync first'):
            await checked_save(self.bot,self.guild,cfg,rev,603)
        after,_=await self.bot.db.settings(101)
        self.assertTrue(after['guard']['enabled'])

    async def test_inaccessible_raw_edit_skips_message_fetch(self):
        self.bot.get_channel=lambda channel_id:self.guild.general
        self.guild.general.permissions_for=lambda member:discord.Permissions.none()
        self.guild.general.fetch_message=AsyncMock()
        payload=SimpleNamespace(cached_message=None,guild_id=101,channel_id=305,message_id=123,data={'content':'edit'})
        await CrewBot.on_raw_message_edit(self.bot,payload)
        self.guild.general.fetch_message.assert_not_awaited()
