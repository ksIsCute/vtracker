import json
import re
import logging
from colorama import init, Fore, Style
import discord
import io
import asyncio
from datetime import datetime
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

# Global tracking variables
active_paginators = {}  # {user_id: message_id}
original_ban_data = {}  # {message_id: {'user_ids': [], 'ban_list': [], 'timestamp': datetime}}

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
                    if ban_entry.reason and re.search(r'\b(vorth|racc)\b', ban_entry.reason, re.IGNORECASE):
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


@bot.event
async def on_message(message):
    # Don't process commands if they're in the message
    await bot.process_commands(message)

    # Check if message is in bot's DMs and not from the bot itself
    if isinstance(message.channel, discord.DMChannel) and message.author != bot.user:
        # Check if the message is a server ID where the bot is present
        try:
            server_id = int(message.content.strip())
            guild = bot.get_guild(server_id)

            if not guild:
                await message.channel.send(
                    "❌ This bot is not in the server with this ID.\n"
                    "DM functionality is only for server verification requests to be added to the global ban list.\n"
                    "Please make sure:\n"
                    "1. The bot is in your server\n"
                    "2. You're sending the correct server ID\n"
                    "3. You have administrator permissions in that server"
                )
                return

            # Check if user is in the server and has admin permissions
            member = guild.get_member(message.author.id)
            if not member or not member.guild_permissions.administrator:
                await message.channel.send(
                    "❌ You must be an administrator in the server to request verification."
                )
                return

            # Try to create an invite link
            invite_link = "Failed to generate invite link"
            try:
                # Check for vanity URL first
                if guild.vanity_url:
                    invite_link = guild.vanity_url
                else:
                    # Create a new invite
                    invites = await guild.invites()
                    if invites:
                        invite_link = invites[0].url
                    else:
                        channel = guild.text_channels[0] if guild.text_channels else None
                        if channel:
                            invite = await channel.create_invite(max_age=300, reason="Verification request")
                            invite_link = invite.url
            except Exception as e:
                logger.error(f"Error creating invite for {guild.id}: {e}")
                invite_link = "Failed to generate invite link"

            # Get bot owner and send DM
            try:
                owner = bot.get_user(814226043924643880)
                try:
                    await owner.create_dm()
                except Exception as e:
                    print(e)
                await owner.send(
                    f"🔔 Verification Request:\n"
                    f"Server: {guild.name} ({guild.id})\n"
                    f"Requested by: {message.author} ({message.author.id})\n"
                    f"Invite: {invite_link}\n\n"
                    f"Use `v!verify {guild.id}` to approve."
                )
            except Exception as e:
                print(e)
                await message.channel.send("Something went wrong. Please join our support server if this is a problem.")
                return

            await message.channel.send(
                "✅ Verification request sent to bot owner!\n"
                f"Server: {guild.name}\n"
                f"Invite Link: {invite_link if invite_link != 'Failed to generate invite link' else 'None available'}"
            )

        except ValueError:
            # Message wasn't a valid server ID
            await message.channel.send(
                "❌ DM functionality is only for server verification requests.\n"
                "Please send just your server ID (a long number) to request verification.\n"
                "You can get your server ID by enabling Developer Mode in Discord settings, "
                "then right-clicking your server name and selecting 'Copy ID'."
            )

@bot.command(name="syncglobal", aliases=['sgb', 'sg'])
@commands.has_permissions(administrator=True)
async def sync_global_ban_list(ctx):
    """Update the global banlist with all verified servers' bans"""
    await ctx.send("Updating global ban list... This may take a while.")

    global_ban_list = await update_global_ban_list()
    count = len(global_ban_list)

    await ctx.send(f"Global ban list updated with {count} entries.")


@bot.command(name="massban", aliases=['setup', 'ban'])
@commands.has_permissions(ban_members=True)
async def mass_ban(ctx, confirm: str = None):
    """Ban all users from the global banlist (type !massban confirm to proceed)"""
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
    if failed > 0:
        await ctx.send("Failed bans detected. [Is the bot above your member role?] Or is a banned user above the bot?")
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

async def create_paginator(ctx, ban_list, user_ids, title):
    pages = []
    verified_text = " This server is verified! Thanks for keeping our communities safe!"
    total_pages = (len(ban_list) // 5) + (1 if len(ban_list) % 5 else 0)

    for i in range(0, len(ban_list), 5):
        embed = discord.Embed(
            title=f"{title} ({len(ban_list)} total)",
            description="".join(ban_list[i:i + 5]),
            color=discord.Color.red()
        )
        embed.set_footer(text=f"Page {i // 5 + 1}/{total_pages}{verified_text}")
        pages.append(embed)

    message = await ctx.send(embed=pages[0])
    active_paginators[ctx.author.id] = message.id
    original_ban_data[message.id] = {
        'user_ids': user_ids.copy(),
        'ban_list': ban_list.copy(),
        'timestamp': datetime.now()
    }

    reactions = ["🔼", "🗒️", "❌"]
    if len(pages) > 1:
        reactions.extend(["⬅️", "➡️"])

    for reaction in reactions:
        await message.add_reaction(reaction)

    return pages, message

async def handle_pagination(ctx, message, pages, title):
    def check(reaction, user):
        return (user == ctx.author and
                str(reaction.emoji) in ["⬅️", "➡️", "🔼", "❌", "🗒️"] and
                reaction.message.id == message.id)

    current_page = 0
    try:
        while True:
            reaction, user = await bot.wait_for("reaction_add", timeout=30.0, check=check)
            emoji = str(reaction.emoji)

            if emoji == "❌":
                await message.delete()
                break

            elif emoji == "🔼":
                file_content = "\n".join(original_ban_data[message.id]['ban_list'])
                file = io.BytesIO(file_content.encode('utf-8'))
                await ctx.send(content="📁 Organized list export:", file=discord.File(file, filename="ban_list.txt"))

            elif emoji == "🗒️":
                file_content = "\n".join(original_ban_data[message.id]['user_ids'])
                file = io.BytesIO(file_content.encode('utf-8'))
                await ctx.send(content="📁 Raw ID export:", file=discord.File(file, filename="user_ids.txt"))


            elif emoji in ["⬅️", "➡️"]:
                if emoji == "➡️" and current_page < len(pages) - 1:
                    current_page += 1
                elif emoji == "⬅️" and current_page > 0:
                    current_page -= 1
                await message.edit(embed=pages[current_page])

            await message.remove_reaction(reaction, user)

    except asyncio.TimeoutError:
        await message.clear_reactions()


@bot.command(name="banlist", aliases=['bans'])
async def banlist(ctx):
    """Show server bans containing 'vorth' or 'racc' in the reason."""
    global active_paginators, original_ban_data

    if ctx.author.id in active_paginators:
        try:
            msg = await ctx.channel.fetch_message(active_paginators[ctx.author.id])
            await ctx.send("You already have an active banlist. Please finish using that one first.")
            return
        except:
            pass

    ban_list = []
    user_ids = []

    try:
        active_paginators[ctx.author.id] = None
        title = "Server Ban List"

        async for ban_entry in ctx.guild.bans(limit=1000):
            if ban_entry.reason and re.search(r'\b(vorth|racc)\b', ban_entry.reason, re.IGNORECASE):
                ban_list.append(f"{ban_entry.user} ({ban_entry.user.id}) - Reason: {ban_entry.reason}\n")
                user_ids.append(str(ban_entry.user.id))

        if not ban_list:
            await ctx.send("No bans found with 'vorth' or 'racc' in the reason.")
            return

        pages, message = await create_paginator(ctx, ban_list, user_ids, title)
        await handle_pagination(ctx, message, pages, title)

    except Exception as e:
        await ctx.send(f"❌ Error loading ban list: {e}")
    finally:
        if ctx.author.id in active_paginators:
            del active_paginators[ctx.author.id]


@bot.command(name="banlist_all", aliases=['ball'])
async def banlist_all(ctx):
    """Show all server bans."""
    global active_paginators, original_ban_data

    if ctx.author.id in active_paginators:
        try:
            msg = await ctx.channel.fetch_message(active_paginators[ctx.author.id])
            await ctx.send("You already have an active banlist. Please finish using that one first.")
            return
        except:
            pass

    ban_list = []
    user_ids = []

    try:
        active_paginators[ctx.author.id] = None
        title = "Server Ban List (All Bans)"

        async for ban_entry in ctx.guild.bans(limit=1000):
            reason = ban_entry.reason or "No reason provided"
            ban_list.append(f"{ban_entry.user} ({ban_entry.user.id}) - Reason: {reason}\n")
            user_ids.append(str(ban_entry.user.id))

        if not ban_list:
            await ctx.send("No bans found.")
            return

        pages, message = await create_paginator(ctx, ban_list, user_ids, title)
        await handle_pagination(ctx, message, pages, title)

    except Exception as e:
        await ctx.send(f"❌ Error loading ban list: {e}")
    finally:
        if ctx.author.id in active_paginators:
            del active_paginators[ctx.author.id]

@bot.command(name="globalbanlist", aliases=['gb', 'gbl'])
async def globalbanlist(ctx):
    """Show global bans (only 'vorth' or 'racc' reasons)."""
    global active_paginators, original_ban_data

    if ctx.author.id in active_paginators:
        try:
            msg = await ctx.channel.fetch_message(active_paginators[ctx.author.id])
            await ctx.send("You already have an active banlist. Please finish using that one first.")
            return
        except:
            pass

    ban_list = []
    user_ids = []

    try:
        active_paginators[ctx.author.id] = None
        title = "Global Ban List"



        is_verified = ctx.guild.features and 'VERIFIED' in ctx.guild.features
        title = "The Global Ban List"
        bans = load_global_ban_list()  # <-- NOT global_banlist.get("bans")

        for user_id, ban_data in bans.items():
            reason = ban_data.get("reason", "")
            if reason and re.search(r'\b(vorth|racc)\b', reason, re.IGNORECASE):
                user_ids.append(user_id)
                servers = ban_data.get("servers", [])
                entry = f"{ban_data['name']} ({user_id}) - Reason: {ban_data['reason']}"

                if str(ctx.guild.id) in servers:
                    ban_list.append(f":star: {entry}\n")
                else:
                    ban_list.append(f"{entry}\n")

        if not ban_list:
            await ctx.send("No global bans found with 'vorth' or 'racc' in the reason.")
            return

        pages, message = await create_paginator(ctx, ban_list, user_ids, title)
        await handle_pagination(ctx, message, pages, title)

    except Exception as e:
        await ctx.send(f"❌ Error loading global ban list: {e}")
    finally:
        if ctx.author.id in active_paginators:
            del active_paginators[ctx.author.id]


# Run the bot
TOKEN = load_config().get('vtoken')
bot.run(TOKEN)