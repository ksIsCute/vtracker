import json
import os
from difflib import SequenceMatcher
import discord
from discord.ext import commands


class AutoScreener(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.load_data()

    def load_data(self):
        """Load banned accounts, server settings, and verified servers"""
        try:
            with open('data/global_ban_list.json') as f:
                self.banned_accounts = json.load(f)['bans']

            with open('data/servers.json') as f:
                self.servers = json.load(f)

            with open('data/verified_servers.json') as f:
                self.verified_servers = set(json.load(f)['servers'])

            self._extract_name_patterns()
            self.validate_servers()  # Ensure all servers have required fields
            print("Loaded ban list with", len(self.banned_accounts), "entries")

        except FileNotFoundError as e:
            print(f"Error loading data files: {e}")
            self.banned_accounts = {}
            self.servers = {}
            self.verified_servers = set()
            self.banned_name_patterns = set()

    def save_servers(self):
        """Save updated server settings"""
        os.makedirs('data', exist_ok=True)
        with open('data/servers.json', 'w') as f:
            json.dump(self.servers, f, indent=4)

    def validate_servers(self):
        """Ensure all servers have valid default fields"""
        updated = False

        for guild_id, settings in self.servers.items():
            # If a key is missing, add it
            if 'screening' not in settings:
                settings['screening'] = False
                updated = True
            if 'do' not in settings:
                settings['do'] = 'log'
                updated = True
            if 'vorth_logs_channel' not in settings:
                settings['vorth_logs_channel'] = None
                updated = True

        if updated:
            self.save_servers()
            print("✅ Fixed missing fields in servers.json")

    def _extract_name_patterns(self):
        """Extract patterns from banned names"""
        banned_names = [account['name'].lower() for account in self.banned_accounts.values()]
        self.banned_name_patterns = set()

        for name in banned_names:
            parts = []
            for sep in ['_', '.', '-', ' ']:
                if sep in name:
                    parts.extend(name.split(sep))

            if not parts:
                parts = [name]

            for part in parts:
                if len(part) >= 3:
                    self.banned_name_patterns.add(part)

    def is_similar_name(self, name):
        """Check if name matches any banned patterns"""
        name_lower = name.lower()
        banned_names = [acc['name'].lower() for acc in self.banned_accounts.values()]

        if name_lower in banned_names:
            return True

        for pattern in self.banned_name_patterns:
            if pattern in name_lower:
                return True

        for banned_name in banned_names:
            if SequenceMatcher(None, name_lower, banned_name).ratio() > 0.7:
                return True

        return False

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Handle new member screening"""
        if member.bot:
            return

        guild_id = str(member.guild.id)

        # Auto-add missing server to servers.json
        if guild_id not in self.servers:
            self.servers[guild_id] = {
                "screening": False,
                "do": "log",
                "vorth_logs_channel": None
            }
            self.save_servers()
            print(f"Auto-added server {guild_id} to servers.json")

        server_settings = self.servers.get(guild_id, {})

        if not self.is_similar_name(member.name):
            return

        screening_enabled = server_settings.get('screening', False)
        action = server_settings.get('do', 'kick') if screening_enabled else 'log'
        logs_channel = self.bot.get_channel(server_settings.get('vorth_logs_channel'))

        message = await self._take_action(member, action)

        if logs_channel:
            try:
                await logs_channel.send(message)
            except discord.Forbidden:
                print(f"Missing permissions in logs channel {logs_channel.id}")

    async def _take_action(self, member, action):
        """Execute the appropriate moderation action"""
        reason = "Potential banned user pattern match"

        try:
            if action == 'ban':
                await member.ban(reason=reason)
                return f"🚨 **Banned potential banned user**: {member.mention} (`{member.name}`)"
            elif action == 'kick':
                await member.kick(reason=reason)
                return f"🚨 **Kicked potential banned user**: {member.mention} (`{member.name}`)"
            else:
                return f"⚠️ **Potential banned user detected**: {member.mention} (`{member.name}`)"
        except discord.Forbidden:
            return f"❌ **Failed to {action} potential banned user**: {member.mention} - Missing permissions"
        except Exception as e:
            return f"❌ **Error processing {member.mention}**: {str(e)}"

    def is_verified_server(self, ctx):
        """Check if the command is run in a verified server"""
        return str(ctx.guild.id) in self.verified_servers

    def verified_only():
        """Custom check decorator"""
        async def predicate(ctx):
            cog = ctx.cog
            if cog is None:
                return False
            return cog.is_verified_server(ctx)
        return commands.check(predicate)

    @commands.command()
    @commands.has_permissions(manage_guild=True)
    @verified_only()
    async def reloadbans(self, ctx):
        """Reload the ban list and patterns"""
        self.load_data()
        await ctx.send("✅ Reloaded ban list with "
                       f"{len(self.banned_accounts)} entries and "
                       f"{len(self.banned_name_patterns)} patterns")

    @commands.command()
    @commands.has_permissions(manage_guild=True)
    @verified_only()
    async def checkname(self, ctx, *, name):
        """Check if a name matches banned patterns"""
        if self.is_similar_name(name):
            await ctx.send(f"⚠️ `{name}` matches banned patterns!")
        else:
            await ctx.send(f"✅ `{name}` appears clean")

    @commands.command()
    @commands.has_permissions(manage_guild=True)
    @verified_only()
    async def reloadservers(self, ctx):
        """Reload server settings and verified servers"""
        try:
            with open('data/servers.json') as f:
                self.servers = json.load(f)

            with open('data/verified_servers.json') as f:
                self.verified_servers = set(json.load(f)['servers'])

            await ctx.send(f"✅ Reloaded server settings for {len(self.servers)} servers "
                           f"and {len(self.verified_servers)} verified servers.")
        except FileNotFoundError as e:
            await ctx.send(f"❌ Error loading server data: {e}")

    @commands.command(name="listservers")
    @commands.has_permissions(manage_guild=True)
    @verified_only()
    async def listservers(self, ctx):
        """List all servers configured with AutoScreener"""
        if not self.servers:
            await ctx.send("❌ No servers found in configuration.")
            return

        description = ""

        for guild_id, settings in self.servers.items():
            screening_status = "✅ Screening" if settings.get('screening', False) else "❌ No Screening"
            action = settings.get('do', 'N/A')
            logs_channel = settings.get('vorth_logs_channel')

            description += (
                f"**Server ID**: `{guild_id}`\n"
                f"- Status: {screening_status}\n"
                f"- Action: `{action}`\n"
                f"- Logs Channel: {logs_channel if logs_channel else 'None'}\n\n"
            )

        # Split message if too long for Discord (optional, not needed unless your list is massive)
        if len(description) > 2000:
            # In real big bots you'd paginate, but you're probably fine for now
            await ctx.send("⚠️ Too many servers to list!")
            return

        embed = discord.Embed(
            title="🛡️ AutoScreener Servers",
            description=description,
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(AutoScreener(bot))
