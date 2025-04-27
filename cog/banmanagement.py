from discord.ext import commands
import json
auditors = [814226043924643880, 1329933418427056191, 837048825859538995]
class BanManagement(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.load_data()

    def load_data(self):
        """Load global ban list from file"""
        try:
            with open('data/global_ban_list.json') as f:
                self.banned_accounts = json.load(f)['bans']
            print("Loaded global ban list with", len(self.banned_accounts), "entries")
        except FileNotFoundError as e:
            print(f"Error loading data files: {e}")
            self.banned_accounts = {}

    def save_data(self):
        """Save global ban list to file"""
        with open('data/global_ban_list.json', 'w') as f:
            json.dump({'bans': self.banned_accounts}, f, indent=2)

    @commands.command()
    async def add_to_banlist(self, ctx, user_id: int, *, reason: str = "No reason provided"):
        """Add a user to the global ban list"""
        if ctx.author.id in auditors:  # Check if the user is an auditor
            self.banned_accounts[user_id] = {"reason": reason}
            self.save_data()
            await ctx.send(f"✅ User with ID {user_id} added to the global ban list for the reason: {reason}")
        else:
            await ctx.send("❌ You do not have permission to use this command.")

    @commands.command()
    async def suggest_remove_from_banlist(self, ctx, user_id: int):
        """Suggest removal of a user from the global ban list"""
        # Send to the auditor channel
        auditor_channel = self.bot.get_channel(1365903180730335315)
        if auditor_channel:
            user = self.banned_accounts.get(user_id, None)
            if user:
                reason = user["reason"]
                await auditor_channel.send(
                    f"🔔 Suggestion to Remove User from Global Ban List:\n"
                    f"User ID: {user_id}\n"
                    f"Reason for Ban: {reason}\n\n"
                    f"Requested by: {ctx.author} ({ctx.author.id})\n"
                    f"To remove, use `v!remove {user_id}`."
                )
                await ctx.send(f"✅ Suggested removal of user {user_id} from the global ban list.")
            else:
                await ctx.send(f"❌ User ID {user_id} not found in the global ban list.")
        else:
            await ctx.send(f"❌ Auditor channel not found.")

    @commands.command()
    async def remove_from_banlist(self, ctx, user_id: int):
        """Remove a user from the global ban list (if they exist)"""
        if ctx.author.id in auditors:  # Check if the user is an auditor
            if user_id in self.banned_accounts:
                del self.banned_accounts[user_id]
                self.save_data()
                await ctx.send(f"✅ User ID {user_id} has been removed from the global ban list.")
            else:
                await ctx.send(f"❌ User ID {user_id} not found in the global ban list.")
        else:
            await ctx.send("❌ You do not have permission to use this command.")

async def setup(bot):
    await bot.add_cog(BanManagement(bot))

