import discord
from discord.ext import commands
import json


class Settings(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Load server settings
        try:
            with open('data/servers.json') as f:
                self.servers = json.load(f)
        except FileNotFoundError:
            self.servers = {}

    def save_settings(self):
        """Save server settings to the file"""
        with open('data/servers.json', 'w') as f:
            json.dump(self.servers, f, indent=4)

    @commands.group(name='settings', invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def settings_group(self, ctx):
        """Configure detection settings for your server"""
        embed = discord.Embed(
            title="🛠️ Detection Settings",
            description="Configure how the bot handles potential bad actors in your server.",
            color=discord.Color.blue()
        )

        # Get current settings
        guild_id = str(ctx.guild.id)
        settings = self.servers.get(guild_id, {})
        logs = settings.get("logs_channel", "Not set")

        embed.add_field(
            name="Current Settings",
            value=(
                f"**Screening Enabled:** {settings.get('screening', False)}\n"
                f"**Action:** {settings.get('do', 'log').title()}\n"
                f"**Logs Channel:** {f'<#{logs}>' if logs != 'Not set' else 'Not set'}"
            ),
            inline=False
        )

        embed.add_field(
            name="Available Commands",
            value=(
                "`v!settings screening <on/off>` - Enable/disable automatic screening\n"
                "`v!settings action <ban/kick/log>` - Set action for matches\n"
                "`v!settings logchannel <#channel>` - Set logging channel\n"
                "`v!settings view` - View current settings\n"
                "`v!settings reset` - Reset all settings to default"
            ),
            inline=False
        )

        await ctx.send(embed=embed)

    @settings_group.command(name='screening')
    @commands.has_permissions(manage_guild=True)
    async def screening_setting(self, ctx, state: str):
        """Enable or disable automatic screening of new members"""
        guild_id = str(ctx.guild.id)

        if state.lower() in ('on', 'enable', 'true'):
            self.servers.setdefault(guild_id, {})['screening'] = True
            action = "enabled"
        elif state.lower() in ('off', 'disable', 'false'):
            self.servers.setdefault(guild_id, {})['screening'] = False
            action = "disabled"
        else:
            await ctx.send("❌ Invalid state. Use `on` or `off`")
            return

        self.save_settings()

        # Send confirmation message
        embed = discord.Embed(
            title="✅ Setting Changed",
            description=f"Automatic screening has been **{action}** for your server.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @settings_group.command(name='action')
    @commands.has_permissions(manage_guild=True)
    async def action_setting(self, ctx, action: str):
        """Set what action to take when a potential bad actor is detected (ban/kick/log)"""
        guild_id = str(ctx.guild.id)
        action = action.lower()

        if action not in ('ban', 'kick', 'log'):
            await ctx.send("❌ Invalid action. Use `ban`, `kick`, or `log`")
            return

        self.servers.setdefault(guild_id, {})['do'] = action
        self.save_settings()

        # Send confirmation message
        embed = discord.Embed(
            title="✅ Setting Changed",
            description=f"Action for potential bad actors has been set to **{action}**.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @settings_group.command(name='logchannel')
    @commands.has_permissions(manage_guild=True)
    async def logchannel_setting(self, ctx, channel: discord.TextChannel):
        """Set the channel where detection logs will be sent"""
        guild_id = str(ctx.guild.id)

        self.servers.setdefault(guild_id, {})['logs_channel'] = str(channel.id)
        self.save_settings()

        # Send confirmation message
        embed = discord.Embed(
            title="✅ Setting Changed",
            description=f"Log channel has been set to {channel.mention}.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @settings_group.command(name='view')
    @commands.has_permissions(manage_guild=True)
    async def view_settings(self, ctx):
        """View current server settings"""
        guild_id = str(ctx.guild.id)
        settings = self.servers.get(guild_id, {})

        embed = discord.Embed(
            title="⚙️ Current Server Settings",
            color=discord.Color.blue()
        )

        embed.add_field(
            name="Screening",
            value="Enabled" if settings.get('screening', False) else "Disabled",
            inline=True
        )

        embed.add_field(
            name="Action",
            value=settings.get('do', 'log').title(),
            inline=True
        )

        logs_channel = settings.get('logs_channel')
        embed.add_field(
            name="Logs Channel",
            value=f"<#{logs_channel}>" if logs_channel else "Not set",
            inline=False
        )

        await ctx.send(embed=embed)

    @settings_group.command(name='reset')
    @commands.has_permissions(administrator=True)
    async def reset_settings(self, ctx):
        """Reset all settings to default"""
        guild_id = str(ctx.guild.id)

        if guild_id in self.servers:
            del self.servers[guild_id]
            self.save_settings()

            # Send confirmation message
            embed = discord.Embed(
                title="✅ Settings Reset",
                description="All settings have been reset to their default values.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
        else:
            await ctx.send("ℹ️ No settings to reset - already using defaults")

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        """Auto-initialize settings for new servers that join"""
        guild_id = str(guild.id)

        # Ensure server has default settings
        if guild_id not in self.servers:
            self.servers[guild_id] = {
                'screening': False,
                'do': 'log',
                'logs_channel': None
            }
            self.save_settings()

        # Optionally send welcome message to server admin or log channel
        logs_channel_id = self.servers[guild_id].get('logs_channel', None)
        if logs_channel_id:
            logs_channel = self.bot.get_channel(int(logs_channel_id))
            if logs_channel:
                await logs_channel.send(f"👋 **New server joined:** {guild.name} - Default detection settings applied.")

        print(f"Auto-initialized settings for {guild.name} ({guild.id})")

async def setup(bot):
    await bot.add_cog(Settings(bot))
