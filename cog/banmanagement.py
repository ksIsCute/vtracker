from discord.ext import commands
import json

# File paths
CONFIG_FILE = "data/asd.json"
VERIFIED_SERVERS_FILE = "data/verified_servers.json"
GLOBAL_BAN_LIST_FILE = "data/global_ban_list.json"

def load_config():
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

def load_verified_servers():
    try:
        with open(VERIFIED_SERVERS_FILE, "r") as f:
            return json.load(f).get("servers", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_verified_servers(servers):
    with open(VERIFIED_SERVERS_FILE, "w") as f:
        json.dump({"servers": servers}, f, indent=4)

def load_global_ban_list():
    try:
        with open(GLOBAL_BAN_LIST_FILE, "r") as f:
            return json.load(f).get("bans", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_global_ban_list(ban_list):
    with open(GLOBAL_BAN_LIST_FILE, "w") as f:
        json.dump({"bans": ban_list}, f, indent=4)

# Load auditors from config file
auditors = load_config()["auditors"]

def is_auditor():
    """Decorator to check if the user is an auditor."""
    def predicate(ctx):
        return ctx.author.id in auditors  # Check if the user ID is in the auditors list
    return commands.check(predicate)

class BanManagement(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.load_data()

    def load_data(self):
        """Load global ban list from file"""
        self.banned_accounts = load_global_ban_list()
        print("Loaded global ban list with", len(self.banned_accounts), "entries")

    def save_data(self):
        """Save global ban list to file"""
        save_global_ban_list(self.banned_accounts)

    @commands.command(aliases=['banadd', 'addban'])
    @is_auditor()  # Only auditors can use this command
    async def add_to_banlist(self, ctx, user_id: int, *, reason: str = "No reason provided"):
        """Add a user to the global ban list"""
        self.banned_accounts[user_id] = {"reason": reason}
        self.save_data()
        await ctx.send(f"✅ User with ID {user_id} added to the global ban list for the reason: {reason}")

    @commands.command(aliases=['suggestremove', 'removesuggest'])
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

    @commands.command(aliases=['banremove', 'removeban', 'remove'])
    @is_auditor()  # Only auditors can use this command
    async def remove_from_banlist(self, ctx, user_id: int):
        """Remove a user from the global ban list (if they exist)"""
        if user_id in self.banned_accounts:
            del self.banned_accounts[user_id]
            self.save_data()
            await ctx.send(f"✅ User ID {user_id} has been removed from the global ban list.")
        else:
            await ctx.send(f"❌ User ID {user_id} not found in the global ban list.")

async def setup(bot):
    await bot.add_cog(BanManagement(bot))
