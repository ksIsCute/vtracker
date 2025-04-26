import asyncio
import json
import logging
from colorama import init, Fore, Style
import discord
import io
from discord.ext import commands

# Initialize colorama
init(autoreset=True)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

INTENTS = discord.Intents.all()

bot = commands.Bot(command_prefix="v!", intents=INTENTS)

@bot.event
async def on_ready():
    logger.info(f"{Fore.GREEN}Logged in as {bot.user}{Style.RESET_ALL}")

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


async def update_global_ban_list():
    """Update the global ban list by merging all verified servers' bans"""
    global_ban_list = load_global_ban_list()
    verified_servers = load_verified_servers()

    for server_id in verified_servers:
        try:
            guild = bot.get_guild(int(server_id))
            if guild:
                async for ban_entry in guild.bans(limit=None):
                    if ban_entry.reason and "vorth" in ban_entry.reason.lower():
                        user_id = str(ban_entry.user.id)
                        if user_id not in global_ban_list:
                            global_ban_list[user_id] = {
                                "name": str(ban_entry.user),
                                "reason": ban_entry.reason,
                                "servers": [server_id]
                            }
                        elif server_id not in global_ban_list[user_id]["servers"]:
                            global_ban_list[user_id]["servers"].append(server_id)
        except Exception as e:
            logger.error(f"Error processing bans for server {server_id}: {e}")

    save_global_ban_list(global_ban_list)
    return global_ban_list


@bot.command(name="syncglobal")
@commands.has_permissions(administrator=True)
async def sync_global_ban_list(ctx):
    """Update the global ban list with all verified servers' bans"""
    await ctx.send("Updating global ban list... This may take a while.")

    global_ban_list = await update_global_ban_list()
    count = len(global_ban_list)

    await ctx.send(f"Global ban list updated with {count} entries.")


@bot.command(name="massban", aliases=['setup', 'ban'])
@commands.has_permissions(ban_members=True)
async def mass_ban(ctx, confirm: str = None):
    """Ban all users from the global ban list (type !massban confirm to proceed)"""
    global_ban_list = load_global_ban_list()
    if confirm != "confirm":
        await ctx.send(f"This will ban all users from the global ban list.\n-# *(Currently {len(global_ban_list)})*"
                       f"\n-# Type `{ctx.prefix}{ctx.invoked_with} confirm` to proceed.")
        return

    if not global_ban_list:
        await ctx.send("Global ban list is empty.")
        return

    await ctx.send(f"Starting mass ban of {len(global_ban_list)} users...")

    success = 0
    failed = 0
    progress_msg = await ctx.send("Progress: 0/0")

    for i, (user_id, ban_data) in enumerate(global_ban_list.items(), 1):
        try:
            await ctx.guild.ban(
                discord.Object(id=int(user_id)),
                reason=f"Global ban: {ban_data['reason']}"
            )
            success += 1
        except Exception as e:
            logger.error(f"Failed to ban {user_id}: {e}")
            failed += 1

        if i % 5 == 0 or i == len(global_ban_list):
            await progress_msg.edit(content=f"Progress: {i}/{len(global_ban_list)} "
                                            f"(Success: {success}, Failed: {failed})")
            await asyncio.sleep(1)  # Rate limiting

    await ctx.send(f"Mass ban completed. Success: {success}, Failed: {failed}")

@bot.command(name="verify")
@commands.is_owner()
async def _verify(ctx, server_id: str):
    """Verify a server, owner only."""
    verified_servers = load_verified_servers()

    if server_id not in verified_servers:
        verified_servers.append(server_id)
        save_verified_servers(verified_servers)
        server = bot.get_guild(int(server_id))
        logger.info(f"Added new verified server: {server.name} ({server.id})")

@bot.command(name="banlist")
async def banlist(ctx):
    """Show bans containing 'vorth' in the reason"""
    # Add server to verified list if not already there

    # Fetch and filter bans
    ban_list = []
    user_ids = []
    async for ban_entry in ctx.guild.bans(limit=400):
        if ban_entry.reason and "vorth" in ban_entry.reason.lower():
            ban_list.append(f"{ban_entry.user} ({ban_entry.user.id}) - Reason: {ban_entry.reason}\n")
            user_ids.append(str(ban_entry.user.id))

    if not ban_list:
        await ctx.send("No bans found.")
        return

    # Paginate into embeds (5 bans per page)
    pages = []
    verified_text = " This server is verified! Thanks for keeping our communities safe!"
    for i in range(0, len(ban_list), 5):
        embed = discord.Embed(
            title=f"Returned List ({len(ban_list)} total)",
            description="".join(ban_list[i:i + 5]),
            color=discord.Color.red()
        )
        embed.set_footer(text=f"Page {i // 5 + 1}/{(len(ban_list) // 5) + 1}" + verified_text)
        pages.append(embed)

    # Send the first page
    message = await ctx.send(embed=pages[0])

    # Add reactions for navigation and file export
    if len(pages) > 1:
        await message.add_reaction("⬅️")
        await message.add_reaction("➡️")
    await message.add_reaction("🔼")  # Export as file

    def check(reaction, user):
        return user == ctx.author and str(reaction.emoji) in ["⬅️", "➡️", "🔼"]

    current_page = 0
    while True:
        try:
            reaction, user = await bot.wait_for("reaction_add", timeout=60.0, check=check)

            # Handle navigation
            if str(reaction.emoji) == "➡️" and current_page < len(pages) - 1:
                current_page += 1
                await message.edit(embed=pages[current_page])
            elif str(reaction.emoji) == "⬅️" and current_page > 0:
                current_page -= 1
                await message.edit(embed=pages[current_page])
            elif str(reaction.emoji) == "🔼":
                # Export as file when 🔼 is clicked
                file_content = "\n".join(user_ids)
                file = io.BytesIO(file_content.encode('utf-8'))
                await ctx.send(
                    content=f"📁 Raw list export *({len(ban_list)} entries)*:",
                    file=discord.File(file, filename="ban_list.txt")
                )

            await message.remove_reaction(reaction, user)

        except asyncio.TimeoutError:
            await message.clear_reactions()
            break


# Run the bot
TOKEN = load_config().get('vtoken')
bot.run(TOKEN)