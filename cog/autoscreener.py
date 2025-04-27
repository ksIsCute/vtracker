import json
import os
from difflib import SequenceMatcher
import discord
from discord.ext import commands

# File paths
CONFIG_FILE = "data/asd.json"

def load_config():
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

auditors = load_config()["auditors"]

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

    def validate_servers(self):
        """Ensure all servers have valid default fields"""
        updated = False

        for guild_id, settings in self.servers.items():
            # If a key is missing, add it
            if 'whitelist' not in settings:
                settings['whitelist'] = []  # Default: no users are whitelisted
                updated = True
            if 'screening' not in settings:
                settings['screening'] = False
                updated = True
            if 'do' not in settings:
                settings['do'] = 'log'  # Default action: log only
                updated = True
            if 'logs_channel' not in settings:
                settings['logs_channel'] = None
                updated = True

        if updated:
            self.save_servers()
            print("✅ Fixed missing fields in servers.json")

    def _is_valid_action(self, action):
        """Check if an action string is valid"""
        if not isinstance(action, str):
            return False

        # Normalize the input (lowercase, remove spaces, strip commas)
        normalized = action.lower().replace(" ", "").strip(',')

        # Check single actions
        if normalized in ['ban', 'kick', 'log']:
            return True

        # Check combined actions
        parts = [p for p in normalized.split(',') if p]  # Remove empty parts

        if len(parts) == 2:
            # All valid combinations (order doesn't matter)
            valid_combos = [
                {'ban', 'log'},
                {'kick', 'log'}
            ]
            return set(parts) in valid_combos

        return False

        return False

    def _extract_name_patterns(self):
        """Extract patterns from banned names"""
        banned_names = []

        # Ensure 'name' key exists in each banned account before accessing
        for account in self.banned_accounts.values():
            name = account.get('name')
            if name:
                banned_names.append(name.lower())

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

    def is_similar_name(self, name, guild_id):
        """Check if name matches any banned patterns, excluding whitelisted users"""
        # Check if the user is whitelisted
        if guild_id in self.servers and self.servers[guild_id].get('whitelist'):
            whitelisted_ids = self.servers[guild_id]['whitelist']
            if str(name) in whitelisted_ids:
                return False  # User is whitelisted, no need to check for banned patterns

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
                "do": "log",  # Default action is to log
                "logs_channel": None,
                "whitelist": []  # Default: no users whitelisted
            }
            self.save_servers()
            print(f"Auto-added server {guild_id} to servers.json")

        # Skip screening if the user is whitelisted
        if member.id in self.servers[guild_id].get('whitelist', []):
            print(f"✅ {member.mention} is whitelisted, no screening.")
            return

        server_settings = self.servers.get(guild_id, {})

        if not self.is_similar_name(member.name, guild_id):
            return

        screening_enabled = server_settings.get('screening', False)
        action = server_settings.get('do', 'log') if screening_enabled else 'log'
        logs_channel = self.bot.get_channel(server_settings.get('logs_channel'))

        message = await self._take_action(member, action)

        if logs_channel:
            try:
                await logs_channel.send(message)
            except discord.Forbidden:
                print(f"Missing permissions in logs channel {logs_channel.id}")

    async def _take_action(self, member, action):
        """Execute the appropriate moderation action"""
        reason = "Potential banned user pattern match"
        actions_taken = []

        # Ensure action is properly normalized (just in case)
        normalized_action = action.lower().replace(" ", "").strip(',')
        actions = [a for a in normalized_action.split(',') if a]

        for action in actions:
            try:
                if action == 'ban':
                    await member.ban(reason=reason)
                    actions_taken.append('banned')
                elif action == 'kick':
                    await member.kick(reason=reason)
                    actions_taken.append('kicked')
                elif action == 'log':
                    actions_taken.append('logged')
            except discord.Forbidden:
                actions_taken.append(f"failed to {action} (missing permissions)")
            except Exception as e:
                actions_taken.append(f"error during {action} ({str(e)})")

        if not actions_taken:
            return f"⚠️ **Potential banned user detected**: {member.mention} (`{member.name}`)"

        actions_str = ", ".join(actions_taken)
        return f"🚨 **{actions_str.capitalize()} potential banned user**: {member.mention} (`{member.name}`)"

    def save_servers(self):
        """Save server settings to file"""
        with open('data/servers.json', 'w') as f:
            json.dump(self.servers, f, indent=2)

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

    @commands.group()
    @commands.has_permissions(manage_guild=True)
    @verified_only()
    async def vsettings(self, ctx):
        """Configure AutoScreener settings"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @commands.command(name="update")
    async def update(self, ctx):
        """ Update Auditors List """
        auditors = load_config()["auditors"]
        await ctx.send(f"There are now {len(auditors)} auditors!")
        return

    @vsettings.command()
    async def action(self, ctx, *, action: str):
        """Set the action to take when a banned user is detected
        Options: ban, kick, log, or combinations like ban,log or kick,log"""

        # Normalize the input
        normalized_action = action.lower().replace(" ", "").strip(',')

        if not self._is_valid_action(normalized_action):
            await ctx.send("❌ Invalid action. Valid options are:\n"
                           "- `ban` (ban only)\n"
                           "- `kick` (kick only)\n"
                           "- `log` (log only)\n"
                           "- `ban,log` or `log,ban` (ban and log)\n"
                           "- `kick,log` or `log,kick` (kick and log)")
            return

        guild_id = str(ctx.guild.id)
        if guild_id not in self.servers:
            self.servers[guild_id] = {
                "screening": False,
                "do": "log",
                "logs_channel": None
            }

        # Store the normalized action
        self.servers[guild_id]['do'] = normalized_action
        self.save_servers()
        await ctx.send(f"✅ Action set to: `{normalized_action}`")

    def _is_valid_action(self, action):
        """Check if an action string is valid"""
        if not isinstance(action, str):
            return False

        # Normalize the input (lowercase, remove spaces, strip commas)
        normalized = action.lower().replace(" ", "").strip(',')

        # Check single actions
        if normalized in ['ban', 'kick', 'log']:
            return True

        # Check combined actions
        parts = [p for p in normalized.split(',') if p]  # Remove empty parts

        if len(parts) == 2:
            # All valid combinations (order doesn't matter)
            valid_combos = [
                {'ban', 'log'},
                {'kick', 'log'}
            ]
            return set(parts) in valid_combos

        return False

    @vsettings.command()
    async def screening(self, ctx, state: str):
        """Enable or disable screening (on/off)"""
        guild_id = str(ctx.guild.id)
        if guild_id not in self.servers:
            self.servers[guild_id] = {
                "screening": False,
                "do": "log",
                "logs_channel": None
            }

        if state.lower() in ['on', 'enable', 'true']:
            self.servers[guild_id]['screening'] = True
            self.save_servers()
            await ctx.send("✅ Screening enabled")
        elif state.lower() in ['off', 'disable', 'false']:
            self.servers[guild_id]['screening'] = False
            self.save_servers()
            await ctx.send("✅ Screening disabled")
        else:
            await ctx.send("Invalid state. Use [on/off]")

    @vsettings.command()
    async def logchannel(self, ctx, channel: discord.TextChannel = None):
        """Set the log channel for screening notifications"""
        guild_id = str(ctx.guild.id)
        if guild_id not in self.servers:
            self.servers[guild_id] = {
                "screening": False,
                "do": "log",
                "logs_channel": None
            }

        if channel is None:
            self.servers[guild_id]['logs_channel'] = None
            self.save_servers()
            await ctx.send("✅ Log channel cleared")
        else:
            self.servers[guild_id]['logs_channel'] = channel.id
            self.save_servers()
            await ctx.send(f"✅ Log channel set to {channel.mention}")

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
            logs_channel = settings.get('logs_channel')

            logging_status = "Logging disabled" if action == "kick" else f"Logs Channel: {logs_channel if logs_channel else 'None'}"

            description += (
                f"**Server ID**: `{guild_id}`\n"
                f"- Status: {screening_status}\n"
                f"- Action: `{action}`\n"
                f"- {logging_status}\n\n"
            )

        # Split message if too long for Discord
        if len(description) > 2000:
            await ctx.send("⚠️ Too many servers to list!")
            return

        embed = discord.Embed(
            title="🛡️ AutoScreener Servers",
            description=description,
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)

    @vsettings.command()
    async def addwhitelist(self, ctx, user: discord.User):
        """Add a user to the whitelist for the server"""
        guild_id = str(ctx.guild.id)
        if guild_id not in self.servers:
            self.servers[guild_id] = {
                "whitelist": [],  # Default: no users whitelisted
                "screening": False,
                "do": "log",
                "logs_channel": None
            }

        if user.id not in self.servers[guild_id]['whitelist']:
            self.servers[guild_id]['whitelist'].append(user.id)
            self.save_servers()
            await ctx.send(f"✅ {user.mention} has been added to the whitelist.")
        else:
            await ctx.send(f"⚠️ {user.mention} is already whitelisted.")

    @vsettings.command()
    async def removewhitelist(self, ctx, user: discord.User):
        """Remove a user from the whitelist for the server"""
        guild_id = str(ctx.guild.id)
        if guild_id not in self.servers or user.id not in self.servers[guild_id].get('whitelist', []):
            await ctx.send(f"⚠️ {user.mention} is not whitelisted.")
            return

        self.servers[guild_id]['whitelist'].remove(user.id)
        self.save_servers()
        await ctx.send(f"✅ {user.mention} has been removed from the whitelist.")


async def setup(bot):
    await bot.add_cog(AutoScreener(bot))