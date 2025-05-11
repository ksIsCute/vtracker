import json
import re
import logging
from colorama import init, Fore, Style
from pathlib import Path
import discord
import io
import os
import asyncio
from datetime import datetime
from discord.ext import commands
from difflib import SequenceMatcher

# Initialize colorama
init(autoreset=True)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

INTENTS = discord.Intents.all()
# Make sure you have Guild Bans intent enabled in the Discord Developer Portal
INTENTS.bans = True # Explicitly ensure bans intent is enabled

bot = commands.Bot(command_prefix="v!", intents=INTENTS)
bot.remove_command("help")

@bot.event
async def on_ready():
    logger.info(f"{Fore.GREEN}Logged in as {bot.user}{Style.RESET_ALL}")
    # Ensure data directory exists
    Path("./data").mkdir(parents=True, exist_ok=True)
    # Ensure cog directory exists
    Path("./cog").mkdir(parents=True, exist_ok=True)
    await load_cogs(bot)
    logger.info(f"{Fore.CYAN}Bot is ready and cogs are loaded.{Style.RESET_ALL}")

# File paths
CONFIG_FILE = Path("data/asd.json")
VERIFIED_SERVERS_FILE = Path("data/verified_servers.json")
GLOBAL_BAN_LIST_FILE = Path("data/global_ban_list.json")

def load_config():
    if not CONFIG_FILE.exists():
        # Create a default config if it doesn't exist
        default_config = {"vtoken": "YOUR_BOT_TOKEN_HERE", "auditors": []}
        with open(CONFIG_FILE, "w") as f:
            json.dump(default_config, f, indent=4)
        logger.warning(f"Config file not found. Created a default one at {CONFIG_FILE}. Please add your bot token.")
        return default_config
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except json.JSONDecodeError:
        logger.error(f"Error decoding {CONFIG_FILE}. Please check its format.")
        return {"vtoken": None, "auditors": []} # Return default structure on error

# Global tracking variables
config_data = load_config()
auditors = config_data.get('auditors', []) # Load auditors from config
active_paginators = {}  # {user_id: message_id}
original_ban_data = {}  # {message_id: {'user_ids': [], 'ban_list': [], 'timestamp': datetime}}

def load_verified_servers():
    try:
        if not VERIFIED_SERVERS_FILE.exists():
            save_verified_servers([]) # Create file if it doesn't exist
            return []
        with open(VERIFIED_SERVERS_FILE, "r") as f:
            data = json.load(f)
            # Ensure the key exists and it's a list
            servers = data.get("servers", [])
            if not isinstance(servers, list):
                 logger.warning(f"{VERIFIED_SERVERS_FILE} 'servers' key is not a list. Resetting.")
                 save_verified_servers([])
                 return []
            return servers
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"Error loading verified servers from {VERIFIED_SERVERS_FILE}: {e}. Returning empty list.")
        # Attempt to create/reset the file on error
        save_verified_servers([])
        return []


def save_verified_servers(servers):
    try:
        with open(VERIFIED_SERVERS_FILE, "w") as f:
            json.dump({"servers": servers}, f, indent=4)
    except IOError as e:
        logger.error(f"Could not write to {VERIFIED_SERVERS_FILE}: {e}")


def load_global_ban_list():
    try:
        if not GLOBAL_BAN_LIST_FILE.exists():
            save_global_ban_list({}) # Create file if it doesn't exist
            return {}
        with open(GLOBAL_BAN_LIST_FILE, "r") as f:
            data = json.load(f)
            # Ensure the key exists and it's a dictionary
            bans = data.get("bans", {})
            if not isinstance(bans, dict):
                 logger.warning(f"{GLOBAL_BAN_LIST_FILE} 'bans' key is not a dictionary. Resetting.")
                 save_global_ban_list({})
                 return {}
            return bans
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"Error loading global ban list from {GLOBAL_BAN_LIST_FILE}: {e}. Returning empty dictionary.")
        # Attempt to create/reset the file on error
        save_global_ban_list({})
        return {}


def save_global_ban_list(ban_list):
     try:
        with open(GLOBAL_BAN_LIST_FILE, "w") as f:
            # Ensure the top-level structure is correct
            if not isinstance(ban_list, dict):
                 logger.error(f"Attempted to save non-dictionary data to global ban list. Aborting save.")
                 return # Prevent saving incorrect data type
            json.dump({"bans": ban_list}, f, indent=4)
     except IOError as e:
        logger.error(f"Could not write to {GLOBAL_BAN_LIST_FILE}: {e}")


async def load_cogs(bot):
    cog_dir = Path('./cog')
    if not cog_dir.exists():
        logger.warning(f"Cog directory '{cog_dir}' does not exist. No cogs loaded.")
        return

    loaded_cogs = 0
    for filename in os.listdir(cog_dir):
        if filename.endswith('.py') and not filename.startswith('_'): # Avoid loading files like __init__.py
            try:
                await bot.load_extension(f'cog.{filename[:-3]}')
                logger.info(f'Successfully loaded cog: {filename}')
                loaded_cogs += 1
            except commands.ExtensionNotFound:
                logger.error(f"Cog '{filename}' not found.")
            except commands.ExtensionAlreadyLoaded:
                logger.warning(f"Cog '{filename}' was already loaded.")
            except commands.NoEntryPointError:
                 logger.error(f"Cog '{filename}' does not have a 'setup' function.")
            except commands.ExtensionFailed as e:
                 logger.error(f'Failed to load cog {filename}: {e.__cause__ or e}') # Log the original error
            except Exception as e:
                 logger.error(f'An unexpected error occurred loading cog {filename}: {e}')
    logger.info(f"Finished loading cogs. Total loaded: {loaded_cogs}")


# --- Syncing Logic ---

async def update_global_ban_list():
    """
    Rebuild the global ban list from scratch based on current bans
    in all verified servers matching the specific reason criteria.
    This ensures users unbanned everywhere are removed.
    """
    logger.info("Starting global ban list update...")
    verified_servers = load_verified_servers()
    new_global_ban_list = {} # Build a fresh list

    if not verified_servers:
        logger.warning("No verified servers found. Global ban list will be empty.")
        save_global_ban_list({}) # Save empty list if no servers are verified
        return {}

    processed_servers = 0
    total_bans_added = 0

    for server_id_str in verified_servers:
        try:
            server_id = int(server_id_str)
            guild = bot.get_guild(server_id)
            if guild:
                logger.debug(f"Processing bans for server: {guild.name} ({server_id})")
                ban_count_for_server = 0
                # Use the async iterator correctly
                async for ban_entry in guild.bans(limit=None):
                    # Check if reason exists and matches the pattern
                    if ban_entry.reason and re.search(r'\b(vorth|racc)\b', ban_entry.reason, re.IGNORECASE):
                        user_id = str(ban_entry.user.id)
                        user_name = str(ban_entry.user) # Get current username if available

                        if user_id not in new_global_ban_list:
                            # Add new entry
                            new_global_ban_list[user_id] = {
                                "name": user_name,
                                "reason": ban_entry.reason,
                                "servers": [server_id_str] # Store as string
                            }
                            ban_count_for_server += 1
                        elif server_id_str not in new_global_ban_list[user_id]["servers"]:
                            # Add server ID to existing entry if not already present
                            new_global_ban_list[user_id]["servers"].append(server_id_str)
                            # Optionally update name/reason if desired (e.g., keep newest)
                            new_global_ban_list[user_id]["name"] = user_name # Update name
                            new_global_ban_list[user_id]["reason"] = ban_entry.reason # Update reason
                            ban_count_for_server += 1

                if ban_count_for_server > 0:
                    logger.info(f"Added/updated {ban_count_for_server} relevant bans from {guild.name}")
                    total_bans_added += ban_count_for_server
                processed_servers += 1

            else:
                logger.warning(f"Could not find guild with ID: {server_id}. Skipping.")

        except discord.Forbidden:
             logger.error(f"Bot lacks permissions (View Audit Log or Ban Members) in server {server_id_str}. Skipping.")
        except discord.HTTPException as e:
             logger.error(f"HTTP error fetching bans for server {server_id_str}: {e}. Skipping.")
        except ValueError:
             logger.error(f"Invalid server ID format in verified list: '{server_id_str}'. Skipping.")
        except Exception as e:
             logger.error(f"Unexpected error processing bans for server {server_id_str}: {e}")

    logger.info(f"Global ban list update complete. Processed {processed_servers}/{len(verified_servers)} verified servers.")
    logger.info(f"Final global ban list contains {len(new_global_ban_list)} entries.")
    save_global_ban_list(new_global_ban_list)
    return new_global_ban_list

# --- Bot Events ---

@bot.event
async def on_message(message):
    # Ignore bots
    if message.author.bot:
        return

    # Process commands first
    await bot.process_commands(message)

    if "1365751555810263070" in message.content and message.author.id in auditors:
        await message.reply(f"Watching a current list of `{len(load_global_ban_list())}`\n-# `{round(bot.latency * 1000, 2)}ms`")
    elif "1365751555810263070" in message.content:
        await message.reply(f"Hi! Use v!help for more information about what I do.")

    # --- DM Verification Logic ---
    if isinstance(message.channel, discord.DMChannel) and not message.content.startswith(bot.command_prefix):
        logger.info(f"Received DM from {message.author} ({message.author.id}): '{message.content}'")
        try:
            server_id = int(message.content.strip())
            guild = bot.get_guild(server_id)

            if not guild:
                await message.channel.send(
                    "❌ This bot is not in the server with this ID, or the ID is incorrect.\n"
                    "DM functionality is **only** for server verification requests to join the global ban list network.\n\n"
                    "Please ensure:\n"
                    "1. The bot has been added to your server.\n"
                    "2. You are sending the correct numerical Server ID.\n"
                    "3. You have Administrator permissions in that server."
                )
                logger.warning(f"Verification attempt failed: Bot not in server {server_id}.")
                return

            # Check if user is in the server and has admin permissions
            member = guild.get_member(message.author.id)
            if not member:
                 await message.channel.send(
                     f"❌ It seems you are not currently a member of the server '{guild.name}'. Please join the server first."
                 )
                 logger.warning(f"Verification attempt failed: User {message.author.id} not in server {guild.id}.")
                 return
            if not member.guild_permissions.administrator:
                await message.channel.send(
                    f"❌ You must have **Administrator** permissions in the server '{guild.name}' to request verification."
                )
                logger.warning(f"Verification attempt failed: User {message.author.id} lacks Admin perms in {guild.id}.")
                return

            # Check if server is already verified
            verified_servers = load_verified_servers()
            if str(server_id) in verified_servers:
                 await message.channel.send(f"✅ Server '{guild.name}' ({guild.id}) is already verified.")
                 logger.info(f"Verification attempt: Server {guild.id} already verified.")
                 return

            # Try to create an invite link
            invite_link = "Failed to generate invite link"
            try:
                # Check for vanity URL first
                if guild.vanity_url:
                    invite_link = guild.vanity_url
                    logger.info(f"Using vanity URL for {guild.name}: {invite_link}")
                else:
                    # Try creating a temporary invite
                    # Find a suitable channel (prefer system channel or first text channel)
                    target_channel = guild.system_channel or (guild.text_channels[0] if guild.text_channels else None)
                    if target_channel and target_channel.permissions_for(guild.me).create_instant_invite:
                         invite = await target_channel.create_invite(max_age=300, max_uses=1, reason="Auditor Verification Request") # Short-lived, single-use
                         invite_link = invite.url
                         logger.info(f"Created temporary invite for {guild.name}: {invite_link}")
                    else:
                         logger.warning(f"Could not find suitable channel or lack permissions to create invite in {guild.name} ({guild.id}).")

            except discord.Forbidden:
                 logger.error(f"Lacking 'Create Invite' permission in {guild.name} ({guild.id}).")
                 invite_link = "Failed (Bot lacks permissions)"
            except Exception as e:
                 logger.error(f"Error creating invite for {guild.id}: {e}")
                 invite_link = "Failed (Error)"

            # Send to auditor channel (Replace with your actual audit channel ID)
            audit_channel_id = 1365903180730335315 # <<< YOUR AUDIT CHANNEL ID HERE
            try:
                audit_channel = bot.get_channel(audit_channel_id)
                if audit_channel:
                    await audit_channel.send(
                        f"🔔 **Verification Request**\n\n"
                        f"**Server:** {guild.name} (`{guild.id}`)\n"
                        f"**Requested by:** {message.author} ({message.author.mention} - `{message.author.id}`)\n"
                        f"**Server Invite:** {invite_link}\n\n"
                        f"Auditors, use `v!verify {guild.id}` to approve or `v!reject {guild.id} [reason]` to deny."
                    )
                    logger.info(f"Verification request for {guild.name} ({guild.id}) sent to audit channel.")
                    await message.channel.send(
                        f"✅ Verification request for **{guild.name}** has been sent to the audit team!\n"
                        f"They will review your server. You will be notified if it's approved.\n"
                        f"*Invite Link (for auditor use):* {invite_link}"
                     )
                else:
                    logger.error(f"Audit channel with ID {audit_channel_id} not found.")
                    await message.channel.send("❌ Could not send the request to the audit team (Internal Error). Please contact support.")

            except discord.Forbidden:
                 logger.error(f"Bot lacks permissions to send messages in the audit channel ({audit_channel_id}).")
                 await message.channel.send("❌ Could not send the request to the audit team (Internal Error). Please contact support.")
            except Exception as e:
                 logger.error(f"Error sending verification request to audit channel: {e}")
                 await message.channel.send("❌ An unexpected error occurred while sending the request. Please contact support.")

        except ValueError:
            # Message wasn't a valid server ID
            await message.channel.send(
                "❌ DM functionality is **only** for server verification requests.\n"
                "Please send **only** your server's numerical ID.\n\n"
                "To get your server ID:\n"
                "1. Enable Developer Mode in Discord Settings (User Settings > Advanced).\n"
                "2. Right-click your server icon or name.\n"
                "3. Select 'Copy Server ID'."
            )
            logger.info(f"Received non-ID DM from {message.author}: '{message.content}' - Instructed on getting ID.")
        except Exception as e:
             logger.error(f"Error processing DM from {message.author}: {e}")
             await message.channel.send("❌ An unexpected error occurred. Please try again later or contact support.")

# --- Categorization mapping ---
CATEGORIES = {
    "Configuration": ["settings", "vsettings", "reloadservers"],
    "Ban Management": ["reloadbans", "banlist", "banlist_all", "globalbanlist", "add_to_banlist", "remove_from_banlist", "suggest_remove_from_banlist"],
    "Global Ban Actions": ["massban", "synclocal", "syncglobal"],
    "Verification Management": ["verify", "unverify", "reject"],
    "Auditor Management": ["auditor", "strip", "listauditors", "update"],
    "Utilities": ["checkname", "listservers", "help"]
}

# --- Main help command group ---
@bot.command(name="help")
async def help_command(ctx, *, arg: str = None):
    """Shows help for commands and categories."""
    prefix = ctx.prefix
    embed = discord.Embed(color=discord.Color.blurple())

    if not arg:
        embed.title = "Help Menu"
        embed.description = (
            f"Use `{prefix}help <category>` to see commands in that category.\n"
            f"Use `{prefix}help <command>` for details on a command.\n\n"
            "**Categories:**"
        )
        for category, commands_list in CATEGORIES.items():
            embed.add_field(
                name=category,
                value=", ".join(f"`{cmd}`" for cmd in commands_list),
                inline=False
            )
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=getattr(ctx.author.avatar, 'url', None))
        return await ctx.send(embed=embed)

    # Try to match a category (case-insensitive)
    category = next((cat for cat in CATEGORIES if cat.lower() == arg.lower()), None)
    if category:
        embed.title = f"{category} Commands"
        for cmd_name in CATEGORIES[category]:
            command = bot.get_command(cmd_name)
            if command:
                aliases = ", ".join(command.aliases) if command.aliases else "None"
                usage = f"{prefix}{command.name} {command.signature}" if command.signature else f"{prefix}{command.name}"
                embed.add_field(
                    name=usage,
                    value=(command.help or "No description provided.") + f"\n**Aliases:** {aliases}",
                    inline=False
                )
            else:
                embed.add_field(
                    name=cmd_name,
                    value="*(Command not found or not loaded)*",
                    inline=False
                )
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=getattr(ctx.author.avatar, 'url', None))
        return await ctx.send(embed=embed)

    # Try to match a command
    command = bot.get_command(arg)
    if command:
        embed.title = f"Command: {prefix}{command.name}"
        embed.description = command.help or "No description provided."
        aliases = ", ".join(command.aliases) if command.aliases else "None"
        usage = f"{prefix}{command.name} {command.signature}" if command.signature else f"{prefix}{command.name}"
        embed.add_field(name="Usage", value=usage, inline=False)
        embed.add_field(name="Aliases", value=aliases, inline=False)
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=getattr(ctx.author.avatar, 'url', None))
        return await ctx.send(embed=embed)

    # Not found
    await ctx.send(f"❌ No category or command called `{arg}` found.")

# --- Core Commands ---

@bot.command(name="syncglobal", aliases=['sgb', 'sg'])
@commands.has_permissions(administrator=True) # Keep as admin permission for safety
@commands.cooldown(1, 300, commands.BucketType.guild) # Cooldown: 1 use per 5 mins per guild
async def sync_global_ban_list(ctx):
    """(Admin) Updates the central global ban list with bans from all verified servers."""
    await ctx.send("<a:loading:1371165596632219689> Updating global ban list... This can take a few minutes depending on the number of verified servers.") # Use a loading emoji if you have one

    start_time = datetime.now()
    try:
        global_ban_list = await update_global_ban_list()
        count = len(global_ban_list)
        duration = datetime.now() - start_time
        await ctx.send(f"✅ Global ban list updated successfully! It now contains **{count}** entries.\n"
                       f"*Sync took {duration.total_seconds():.2f} seconds.*")
        logger.info(f"Global ban list updated via command by {ctx.author} in {ctx.guild.id}. New count: {count}")

    except Exception as e:
        logger.exception(f"Error during triggered global sync in {ctx.guild.id}: {e}") # Log full traceback
        await ctx.send(f"❌ An error occurred during the global sync: `{e}`. Please check the bot logs or contact support.")
        ctx.command.reset_cooldown(ctx) # Reset cooldown on error


@bot.command(name="massban", aliases=['setup', 'banall']) # Added banall alias
@commands.has_permissions(ban_members=True)
@commands.cooldown(1, 60, commands.BucketType.guild) # Cooldown: 1 use per 60 seconds per guild
async def mass_ban(ctx, confirm: str = None):
    """(Ban Perms) Bans all users from the global ban list in this server."""
    global_ban_list = load_global_ban_list()

    if not global_ban_list:
        await ctx.send("⚠️ The global ban list is currently empty. Nothing to ban.")
        ctx.command.reset_cooldown(ctx)
        return

    if confirm != "confirm":
        await ctx.send(f"🚨 **Warning!** This command will attempt to ban **{len(global_ban_list)}** users listed in the global ban list.\n"
                       f"This action **cannot be undone** easily.\n\n"
                       f"Type `{ctx.prefix}{ctx.invoked_with} confirm` to proceed.")
        ctx.command.reset_cooldown(ctx) # Reset cooldown if not confirming
        return

    await ctx.send(f"🛡️ Starting mass ban of **{len(global_ban_list)}** users from the global list. This may take time...")

    success = 0
    failed = 0
    already_banned = 0
    total = len(global_ban_list)
    progress_msg = await ctx.send(f"Progress: 0/{total} (Success: 0, Failed: 0, Already Banned: 0)")

    start_time = datetime.now()

    # Fetch current bans once to check if already banned efficiently
    current_bans = set()
    try:
        async for ban_entry in ctx.guild.bans(limit=None):
            current_bans.add(ban_entry.user.id)
        logger.info(f"Fetched {len(current_bans)} existing bans for server {ctx.guild.id}")
    except discord.Forbidden:
        await ctx.send("❌ **Error:** Bot lacks permission to fetch the ban list. Cannot check for existing bans.")
        logger.error(f"Massban failed: Bot lacks fetch bans permission in {ctx.guild.id}")
        ctx.command.reset_cooldown(ctx)
        return
    except Exception as e:
        await ctx.send(f"❌ **Error:** Could not fetch existing bans: `{e}`. Proceeding without checks.")
        logger.error(f"Massban warning: Could not fetch existing bans in {ctx.guild.id}: {e}")
        current_bans = None # Indicate failure

    for i, (user_id_str, ban_data) in enumerate(global_ban_list.items(), 1):
        try:
            user_id = int(user_id_str)

            # Check if already banned (if fetch was successful)
            if current_bans is not None and user_id in current_bans:
                already_banned += 1
                logger.debug(f"Massban: User {user_id} already banned in {ctx.guild.id}.")
                continue # Skip to next user

            reason = f"Global Ban Sync: {ban_data.get('reason', 'Reason not specified in global list.')}"[:512] # Max reason length is 512

            # Check if the user is the bot itself or the server owner - IMPORTANT SAFETY CHECK
            if user_id == bot.user.id:
                logger.warning(f"Massban: Skipped banning the bot itself ({user_id}).")
                continue
            if user_id == ctx.guild.owner_id:
                logger.warning(f"Massban: Skipped banning the server owner ({user_id}).")
                failed += 1 # Count as failure as it cannot be done
                continue

            await ctx.guild.ban(
                discord.Object(id=user_id), # Use discord.Object for users not in the server
                reason=reason,
                delete_message_days=0 # Don't delete messages for global sync bans
            )
            success += 1
            logger.info(f"Massban: Successfully banned {user_id} in {ctx.guild.id}. Reason: {reason}")

        except discord.NotFound:
            failed += 1
            logger.warning(f"Massban: Failed to ban {user_id} in {ctx.guild.id} - User not found.")
        except discord.Forbidden:
            failed += 1
            logger.error(f"Massban: Failed to ban {user_id} in {ctx.guild.id} - Bot lacks permissions (likely role hierarchy).")
        except discord.HTTPException as e:
            failed += 1
            logger.error(f"Massban: Failed to ban {user_id} in {ctx.guild.id} - HTTP Error {e.status}: {e.text}")
        except ValueError:
             failed += 1
             logger.error(f"Massban: Invalid user ID format in global list: '{user_id_str}'. Skipping.")
        except Exception as e:
             failed += 1
             logger.error(f"Massban: Unexpected error banning {user_id} in {ctx.guild.id}: {e}")

        # Update progress message periodically
        if i % 10 == 0 or i == total: # Update every 10 bans or at the end
            try:
                 await progress_msg.edit(content=f"Progress: {i}/{total} "
                                                  f"(Success: {success}, Failed: {failed}, Already Banned: {already_banned})")
            except discord.HTTPException:
                 pass # Ignore if editing fails (e.g., message deleted)
            await asyncio.sleep(1) # Short sleep to avoid hitting rate limits aggressively

    duration = datetime.now() - start_time
    final_message = (f"✅ Mass ban complete.\n"
                     f"Banned: **{success}**\n"
                     f"Already Banned: **{already_banned}**\n"
                     f"Failed: **{failed}** (Check role hierarchy/permissions?)\n"
                     f"Total Processed: **{total}**\n"
                     f"Duration: {duration.total_seconds():.2f} seconds.")
    if failed > 0:
         final_message += "\n\n⚠️ **Failures detected.** This often happens if the bot's role is not high enough or if trying to ban users with higher roles."

    await ctx.send(final_message)


@bot.command(name="synclocal")
@commands.has_permissions(ban_members=True)
@commands.cooldown(1, 60, commands.BucketType.guild) # Cooldown: 1 use per 60 seconds per guild
async def sync_local(ctx, confirm: str = None):
    """(Ban Perms) Bans users from the global list who aren't already banned locally."""
    global_ban_list = load_global_ban_list()

    if not global_ban_list:
        await ctx.send("⚠️ The global ban list is currently empty. Nothing to sync.")
        ctx.command.reset_cooldown(ctx)
        return

    # --- Fetch current bans ---
    await ctx.send("<a:loading:1371165596632219689> Checking local bans against the global list...")
    current_bans = set()
    try:
        async for ban_entry in ctx.guild.bans(limit=None):
            current_bans.add(ban_entry.user.id)
        logger.info(f"SyncLocal: Fetched {len(current_bans)} existing bans for server {ctx.guild.id}")
    except discord.Forbidden:
        await ctx.send("❌ **Error:** Bot lacks permission to fetch the ban list. Cannot perform sync.")
        logger.error(f"SyncLocal failed: Bot lacks fetch bans permission in {ctx.guild.id}")
        ctx.command.reset_cooldown(ctx)
        return
    except Exception as e:
        await ctx.send(f"❌ **Error:** Could not fetch existing bans: `{e}`. Aborting sync.")
        logger.error(f"SyncLocal failed: Could not fetch existing bans in {ctx.guild.id}: {e}")
        ctx.command.reset_cooldown(ctx)
        return

    # --- Identify users to ban ---
    users_to_ban = {}
    for user_id_str, ban_data in global_ban_list.items():
        try:
            user_id = int(user_id_str)
            if user_id not in current_bans:
                 # Safety checks
                 if user_id == bot.user.id or user_id == ctx.guild.owner_id:
                      logger.warning(f"SyncLocal: Skipped adding bot/owner ({user_id}) to ban list.")
                      continue
                 users_to_ban[user_id_str] = ban_data # Store original string ID and data
        except ValueError:
            logger.error(f"SyncLocal: Invalid user ID format in global list: '{user_id_str}'. Skipping.")
            continue


    if not users_to_ban:
        await ctx.send("✅ Your server's ban list is already up-to-date with the global list. No new bans needed.")
        ctx.command.reset_cooldown(ctx)
        return

    # --- Confirmation ---
    if confirm != "confirm":
        await ctx.send(f"ℹ️ This command will attempt to ban **{len(users_to_ban)}** users found in the global list but not currently banned in this server.\n\n"
                       f"Type `{ctx.prefix}{ctx.invoked_with} confirm` to proceed.")
        ctx.command.reset_cooldown(ctx) # Reset cooldown if not confirming
        return

    # --- Execute Bans ---
    await ctx.send(f"🛡️ Syncing local bans... Attempting to ban **{len(users_to_ban)}** users.")

    success = 0
    failed = 0
    total_to_ban = len(users_to_ban)
    progress_msg = await ctx.send(f"Sync Progress: 0/{total_to_ban} (Success: 0, Failed: 0)")
    start_time = datetime.now()

    for i, (user_id_str, ban_data) in enumerate(users_to_ban.items(), 1):
        try:
            user_id = int(user_id_str) # Already validated, but good practice
            reason = f"Global Ban Sync: {ban_data.get('reason', 'Reason not specified in global list.')}"[:512]

            await ctx.guild.ban(
                discord.Object(id=user_id),
                reason=reason,
                delete_message_days=0
            )
            success += 1
            logger.info(f"SyncLocal: Successfully banned {user_id} in {ctx.guild.id}. Reason: {reason}")

        # Specific error handling is similar to massban
        except discord.NotFound:
            failed += 1
            logger.warning(f"SyncLocal: Failed to ban {user_id} in {ctx.guild.id} - User not found.")
        except discord.Forbidden:
            failed += 1
            logger.error(f"SyncLocal: Failed to ban {user_id} in {ctx.guild.id} - Bot lacks permissions (likely role hierarchy).")
        except discord.HTTPException as e:
            failed += 1
            logger.error(f"SyncLocal: Failed to ban {user_id} in {ctx.guild.id} - HTTP Error {e.status}: {e.text}")
        except Exception as e:
             failed += 1
             logger.error(f"SyncLocal: Unexpected error banning {user_id} in {ctx.guild.id}: {e}")

        # Update progress message periodically
        if i % 10 == 0 or i == total_to_ban:
            try:
                 await progress_msg.edit(content=f"Sync Progress: {i}/{total_to_ban} "
                                                  f"(Success: {success}, Failed: {failed})")
            except discord.HTTPException:
                 pass
            await asyncio.sleep(1) # Rate limiting

    duration = datetime.now() - start_time
    final_message = (f"✅ Local ban sync complete.\n"
                     f"Newly Banned: **{success}**\n"
                     f"Failed: **{failed}** (Check role hierarchy/permissions?)\n"
                     f"Total Needed: **{total_to_ban}**\n"
                     f"Duration: {duration.total_seconds():.2f} seconds.")
    if failed > 0:
         final_message += "\n\n⚠️ **Failures detected.** This often happens if the bot's role is not high enough or if trying to ban users with higher roles."

    await ctx.send(final_message)


# --- Verification Commands (Auditor Only) ---

@bot.command(name="verify")
@commands.is_owner() # Or use a custom check for auditors list
async def _verify(ctx, server_id_str: str):
    """(Auditor Only) Verify a server, adding it to the global list network."""
    # Custom auditor check (if not using is_owner)
    # if ctx.author.id not in auditors:
    #     return await ctx.send("❌ You are not authorized to use this command.")

    try:
        server_id = int(server_id_str)
    except ValueError:
        return await ctx.send("❌ Invalid Server ID format. Please provide the numerical ID.")

    guild = bot.get_guild(server_id)
    if not guild:
        return await ctx.send(f"❌ Bot is not in any server with the ID `{server_id}`.")

    verified_servers = load_verified_servers()

    if server_id_str in verified_servers:
        return await ctx.send(f"ℹ️ Server **{guild.name}** (`{server_id}`) is already verified.")

    verified_servers.append(server_id_str)
    save_verified_servers(verified_servers)
    logger.info(f"Auditor {ctx.author} ({ctx.author.id}) verified server: {guild.name} ({guild.id})")
    await ctx.send(f"✅ Server **{guild.name}** (`{server_id}`) has been **verified** and added to the network.")
    # Optionally DM the user who requested it if you store that info


@bot.command(name="unverify")
@commands.is_owner() # Or use a custom check for auditors list
async def _unverify(ctx, server_id_str: str):
    """(Auditor Only) Remove a server's verification."""
    # if ctx.author.id not in auditors:
    #    return await ctx.send("❌ You are not authorized to use this command.")

    try:
        server_id = int(server_id_str)
    except ValueError:
        return await ctx.send("❌ Invalid Server ID format. Please provide the numerical ID.")

    verified_servers = load_verified_servers()
    guild = bot.get_guild(server_id) # Get guild name for confirmation message, even if already removed
    guild_name = guild.name if guild else "Unknown Server"


    if server_id_str not in verified_servers:
        return await ctx.send(f"ℹ️ Server **{guild_name}** (`{server_id}`) is not currently verified.")

    verified_servers.remove(server_id_str)
    save_verified_servers(verified_servers)
    logger.warning(f"Auditor {ctx.author} ({ctx.author.id}) **unverified** server: {guild_name} ({server_id})")
    await ctx.send(f"➖ Server **{guild_name}** (`{server_id}`) has been **unverified** and removed from the network.")
    await ctx.send(f"ℹ️ Run `{ctx.prefix}syncglobal` soon to update the global list based on the remaining verified servers.")


@bot.command(name="reject")
@commands.is_owner() # Or use a custom check for auditors list
async def _reject(ctx, server_id_str: str, *, reason: str = "No reason provided."):
    """(Auditor Only) Rejects a verification request (logs and informs)."""
    # if ctx.author.id not in auditors:
    #    return await ctx.send("❌ You are not authorized to use this command.")

    try:
        server_id = int(server_id_str)
    except ValueError:
        return await ctx.send("❌ Invalid Server ID format. Please provide the numerical ID.")

    guild = bot.get_guild(server_id)
    guild_name = guild.name if guild else "Unknown Server"

    verified_servers = load_verified_servers()
    if server_id_str in verified_servers:
        return await ctx.send(f"ℹ️ Server **{guild_name}** (`{server_id}`) is already verified. Use `{ctx.prefix}unverify` to remove.")

    logger.info(f"Auditor {ctx.author} ({ctx.author.id}) rejected verification for server ID {server_id}. Reason: {reason}")
    await ctx.send(f"❌ Verification request for server ID `{server_id}` has been **rejected**.\n*Reason:* {reason}")
    # Potentially DM the original requester if you tracked them.


# --- Paginator Logic (Mostly unchanged, added minor logging) ---

async def create_paginator(ctx, ban_list, user_ids, title):
    if not ban_list: # Should ideally be checked before calling, but double-check
         logger.warning(f"create_paginator called with empty ban_list for title '{title}' by {ctx.author}")
         await ctx.send(f"No entries found for '{title}'.")
         return None, None # Indicate failure

    pages = []
    # Check if server is verified for the footer message
    verified_servers = load_verified_servers()
    is_verified = str(ctx.guild.id) in verified_servers
    verified_text = " ✅ This server is part of the verified network!" if is_verified else ""

    items_per_page = 5
    total_entries = len(ban_list)
    total_pages = (total_entries + items_per_page - 1) // items_per_page # Ceiling division

    for i in range(0, total_entries, items_per_page):
        page_content = "".join(ban_list[i : i + items_per_page])
        embed = discord.Embed(
            title=f"{title} ({total_entries} total)",
            description=page_content if page_content else "No entries on this page.", # Handle empty page possibility
            color=discord.Color.red()
        )
        embed.set_footer(text=f"Page {i // items_per_page + 1}/{total_pages}{verified_text}")
        pages.append(embed)

    if not pages: # If somehow pages list is empty after loop
        logger.error(f"Paginator creation failed for '{title}', no pages generated.")
        await ctx.send("Error: Could not create paginator pages.")
        return None, None

    try:
        message = await ctx.send(embed=pages[0])
    except discord.HTTPException as e:
         logger.error(f"Failed to send initial paginator message for '{title}': {e}")
         await ctx.send(f"Error sending ban list: `{e}`")
         return None, None


    # Store necessary info immediately after message creation
    active_paginators[ctx.author.id] = message.id
    original_ban_data[message.id] = {
        'user_ids': user_ids.copy(),
        'ban_list': ban_list.copy(), # Store the formatted strings
        'timestamp': datetime.now()
    }

    reactions = ["🔼", "🗒️", "❌"] # Upload formatted, Upload IDs, Close
    if len(pages) > 1:
        reactions.extend(["⬅️", "➡️"]) # Add navigation if multiple pages

    # Add reactions sequentially
    for reaction in reactions:
         try:
              await message.add_reaction(reaction)
         except discord.HTTPException:
              logger.warning(f"Failed to add reaction {reaction} to paginator message {message.id}")
              # Continue trying to add other reactions

    return pages, message

async def handle_pagination(ctx, message, pages, title):
    if not message or not pages: # Check if message/pages are valid
        logger.warning(f"handle_pagination called with invalid message or pages for '{title}'")
        # Clean up potentially stored data if message exists
        if message and message.id in original_ban_data:
             del original_ban_data[message.id]
        if ctx.author.id in active_paginators and active_paginators[ctx.author.id] == (message.id if message else None):
             del active_paginators[ctx.author.id]
        return


    def check(reaction, user):
        # Simple check: user is the command author, reaction is valid, message matches
        return (user.id == ctx.author.id and
                str(reaction.emoji) in ["⬅️", "➡️", "🔼", "❌", "🗒️"] and
                reaction.message.id == message.id)

    current_page = 0
    timeout_duration = 120.0 # Increased timeout to 2 minutes

    try:
        while True:
            reaction, user = await bot.wait_for("reaction_add", timeout=timeout_duration, check=check)
            emoji = str(reaction.emoji)

            # --- Reaction Handlers ---
            if emoji == "❌":
                logger.debug(f"Paginator closed by user {user.id} for message {message.id}")
                await message.delete()
                break # Exit the loop

            elif emoji == "🔼": # Upload formatted list
                 if message.id in original_ban_data:
                     data = original_ban_data[message.id]
                     file_content = "\n".join(data['ban_list']) # Use the stored formatted list
                     if not file_content:
                          await ctx.send("No data available to export.", delete_after=10)
                     else:
                          file = io.BytesIO(file_content.encode('utf-8'))
                          await ctx.send(content="📁 Formatted ban list export:", file=discord.File(file, filename=f"{title.replace(' ','_').lower()}_formatted.txt"))
                          logger.debug(f"User {user.id} exported formatted list for paginator {message.id}")

                 else:
                     logger.warning(f"Original data not found for paginator {message.id} during formatted export.")
                     await ctx.send("Error: Could not retrieve data for export.", delete_after=10)


            elif emoji == "🗒️": # Upload raw IDs
                if message.id in original_ban_data:
                    data = original_ban_data[message.id]
                    file_content = "\n".join(data['user_ids'])
                    if not file_content:
                         await ctx.send("No user IDs available to export.", delete_after=10)
                    else:
                         file = io.BytesIO(file_content.encode('utf-8'))
                         await ctx.send(content="📁 Raw User ID export:", file=discord.File(file, filename=f"{title.replace(' ','_').lower()}_ids.txt"))
                         logger.debug(f"User {user.id} exported raw IDs for paginator {message.id}")
                else:
                    logger.warning(f"Original data not found for paginator {message.id} during raw ID export.")
                    await ctx.send("Error: Could not retrieve data for export.", delete_after=10)


            elif emoji in ["⬅️", "➡️"] and len(pages) > 1: # Navigation
                if emoji == "➡️":
                    current_page = (current_page + 1) % len(pages) # Wrap around next
                elif emoji == "⬅️":
                    current_page = (current_page - 1 + len(pages)) % len(pages) # Wrap around previous

                try:
                    await message.edit(embed=pages[current_page])
                    logger.debug(f"Paginator {message.id} navigated to page {current_page + 1} by user {user.id}")
                except discord.HTTPException as e:
                    logger.warning(f"Failed to edit paginator {message.id} to page {current_page + 1}: {e}")


            # Remove the user's reaction
            try:
                 await message.remove_reaction(reaction, user)
            except discord.Forbidden:
                 logger.warning(f"Failed to remove reaction {emoji} by {user.id} from paginator {message.id} (Missing Permissions).")
            except discord.HTTPException:
                 pass # Ignore other errors like reaction already gone

    except asyncio.TimeoutError:
        logger.debug(f"Paginator {message.id} timed out for user {ctx.author.id}")
        try:
            await message.clear_reactions() # Clear reactions on timeout
        except discord.Forbidden:
             logger.warning(f"Failed to clear reactions on timed-out paginator {message.id} (Missing Permissions).")
        except discord.HTTPException:
            pass # Ignore if message deleted etc.
    finally:
        # Clean up tracking info regardless of how the pagination ends
        if ctx.author.id in active_paginators and active_paginators[ctx.author.id] == message.id:
            del active_paginators[ctx.author.id]
            logger.debug(f"Removed active paginator tracking for user {ctx.author.id}")
        if message.id in original_ban_data:
            del original_ban_data[message.id]
            logger.debug(f"Removed original ban data for message {message.id}")


# --- Ban List Display Commands (Using Paginator) ---

async def display_ban_list(ctx, fetch_all=False, global_list=False):
    """Helper function to display ban lists using the paginator."""
    global active_paginators, original_ban_data # Ensure globals are accessible

    # Check for existing active paginator for the user
    if ctx.author.id in active_paginators:
        try:
            # Try fetching the message to see if it still exists
            existing_msg_id = active_paginators[ctx.author.id]
            if existing_msg_id: # Check if ID is not None/zero
                 msg = await ctx.channel.fetch_message(existing_msg_id)
                 await ctx.send("ℹ️ You already have an active ban list paginator open. Please close (`❌`) or let it time out before starting a new one.", delete_after=15)
                 logger.info(f"User {ctx.author.id} tried to open new banlist while one ({existing_msg_id}) was active.")
                 return # Prevent opening a new one
            else:
                 # If ID was None, remove the entry
                 del active_paginators[ctx.author.id]
        except discord.NotFound:
            # Message doesn't exist, safe to remove tracking and continue
            logger.debug(f"Previous active paginator for {ctx.author.id} not found, removing tracking.")
            if ctx.author.id in active_paginators: # Double check before deleting
                 del active_paginators[ctx.author.id]
            # Also clean up potential orphan data
            stale_message_id = active_paginators.get(ctx.author.id)
            if stale_message_id and stale_message_id in original_ban_data:
                 del original_ban_data[stale_message_id]

        except discord.Forbidden:
            await ctx.send("⚠️ Bot lacks permission to check for existing messages. Please ensure it can read message history.", delete_after=15)
            # Don't proceed, as we can't be sure if another paginator is active
            return
        except Exception as e:
             logger.error(f"Error checking for active paginator for {ctx.author.id}: {e}")
             # Proceed cautiously, might create duplicates
             pass

    ban_list_formatted = []
    user_ids = []
    title = ""
    processing_message = None # To edit/delete later

    try:
        # Indicate processing
        processing_message = await ctx.send("<a:loading:1371165596632219689> Fetching ban list...") # Use a loading emoji

        if global_list:
            title = "Global Ban List"
            bans_data = load_global_ban_list()
            if not bans_data:
                 await processing_message.edit(content="ℹ️ The global ban list is currently empty.")
                 return

            # Determine if the current server is verified for the star indicator
            verified_servers = load_verified_servers()
            is_current_server_verified = str(ctx.guild.id) in verified_servers

            for user_id, ban_data in bans_data.items():
                 # Basic check for expected structure
                 if not isinstance(ban_data, dict) or 'name' not in ban_data or 'reason' not in ban_data:
                      logger.warning(f"Skipping malformed entry in global ban list for user ID {user_id}")
                      continue

                 # Only include bans matching the reason criteria IF we are enforcing it globally
                 # Current code adds based on reason during `update_global_ban_list`,
                 # so all bans in the file should already match.
                 # If you wanted to store ALL bans globally but filter here, add the re.search check:
                 # reason = ban_data.get("reason", "")
                 # if reason and re.search(r'\b(vorth|racc)\b', reason, re.IGNORECASE):
                 user_ids.append(user_id) # Store the string ID
                 servers = ban_data.get("servers", []) # List of server IDs where banned
                 reason = ban_data.get('reason', 'No reason provided')
                 name = ban_data.get('name', 'Unknown User')

                 # Check if ban exists in the *current* server (indicator)
                 # Note: This requires fetching local bans again, could be slow for large lists.
                 # Consider removing if performance is an issue.
                 # local_ban_exists = False
                 # try:
                 #     await ctx.guild.fetch_ban(discord.Object(id=int(user_id)))
                 #     local_ban_exists = True
                 # except (discord.NotFound, ValueError):
                 #     local_ban_exists = False

                 # Indicator: :star: if ban *not* from the current server (if verified)
                 indicator = ""
                 if is_current_server_verified and str(ctx.guild.id) not in servers:
                     indicator = ":star: " # Indicates it's on global list but not from this server

                 entry = f"{indicator}**{name}** (`{user_id}`) - Reason: {reason}"
                 ban_list_formatted.append(f"{entry}\n")

        else: # Local server bans
            title = "Server Ban List (All Bans)" if fetch_all else "Server Ban List ('vorth'/'racc' Bans)"
            ban_count = 0
            limit = None # Fetch all bans by default

            async for ban_entry in ctx.guild.bans(limit=limit):
                ban_count += 1
                user_id_str = str(ban_entry.user.id)
                reason = ban_entry.reason or "No reason provided"
                name = str(ban_entry.user)

                # Filter by reason if not fetching all
                if not fetch_all:
                    if not ban_entry.reason or not re.search(r'\b(vorth|racc)\b', ban_entry.reason, re.IGNORECASE):
                        continue # Skip if reason doesn't match and we're filtering

                user_ids.append(user_id_str)
                entry = f"**{name}** (`{user_id_str}`) - Reason: {reason}"
                ban_list_formatted.append(f"{entry}\n")

            logger.info(f"Fetched {ban_count} total local bans for {ctx.guild.id}. Filtered count: {len(ban_list_formatted)}")


        # --- Check if any bans were found ---
        if not ban_list_formatted:
            await processing_message.edit(content=f"ℹ️ No bans found matching the criteria for '{title}'.")
            return

        # Delete the "Fetching..." message
        try:
            await processing_message.delete()
        except discord.HTTPException:
            pass # Ignore if already deleted or other issue

        # --- Create and Handle Paginator ---
        # Set placeholder before creating paginator to prevent race condition
        active_paginators[ctx.author.id] = None
        pages, message = await create_paginator(ctx, ban_list_formatted, user_ids, title)

        if message and pages: # If paginator created successfully
            await handle_pagination(ctx, message, pages, title)
        else: # Paginator creation failed, cleanup placeholder
             logger.error(f"Paginator creation/handling failed for '{title}' for user {ctx.author.id}.")
             if ctx.author.id in active_paginators and active_paginators[ctx.author.id] is None:
                  del active_paginators[ctx.author.id]
             # Let user know if the processing message wasn't edited
             if processing_message and processing_message.content.startswith("<a:loading"):
                  try:
                      await processing_message.edit(content=f"❌ Error creating the ban list display for '{title}'.")
                  except discord.HTTPException:
                      pass


    except discord.Forbidden:
        logger.error(f"Permission error fetching bans for {'global list' if global_list else ctx.guild.id} by {ctx.author.id}.")
        error_msg = "❌ Bot lacks permissions to fetch the ban list (Need 'View Audit Log' or 'Ban Members')."
        if processing_message: await processing_message.edit(content=error_msg)
        else: await ctx.send(error_msg)
    except Exception as e:
        logger.exception(f"Error loading ban list ('{title}'): {e}") # Log full traceback
        error_msg = f"❌ An unexpected error occurred while loading the ban list: `{e}`"
        if processing_message: await processing_message.edit(content=error_msg)
        else: await ctx.send(error_msg)
    finally:
        # Ensure cleanup even if errors occur before handle_pagination starts
        if ctx.author.id in active_paginators and active_paginators.get(ctx.author.id) is None :
             # Clean up placeholder if handle_pagination wasn't reached or failed early
              del active_paginators[ctx.author.id]
              logger.debug(f"Cleaned up placeholder active_paginator entry for {ctx.author.id}")



@bot.command(name="banlist", aliases=['bans', 'bl'])
@commands.cooldown(1, 10, commands.BucketType.user) # Cooldown per user
async def banlist(ctx):
    """Shows server bans containing 'vorth' or 'racc' in the reason."""
    await display_ban_list(ctx, fetch_all=False, global_list=False)

@bot.command(name="banlist_all", aliases=['ball', 'abl'])
@commands.cooldown(1, 15, commands.BucketType.user) # Slightly longer cooldown for all bans
async def banlist_all(ctx):
    """Shows all bans currently active in this server."""
    await display_ban_list(ctx, fetch_all=True, global_list=False)

@bot.command(name="globalbanlist", aliases=['gb', 'gbl'])
@commands.cooldown(1, 10, commands.BucketType.user)
async def globalbanlist(ctx):
    """Shows the central global ban list."""
    await display_ban_list(ctx, fetch_all=False, global_list=True) # fetch_all is ignored here

# --- Auditor Management (Owner Only) ---

@bot.command(name="auditor", aliases=["addauditor"])
@commands.is_owner()
async def add_auditor(ctx, member: discord.Member):
    """(Owner Only) Adds a user to the auditor list."""
    global auditors # Allow modification of the global list
    config = load_config() # Load current config
    auditors = config.get('auditors', []) # Get current list or default to empty

    if member.id in auditors:
        return await ctx.reply(f"ℹ️ {member.mention} is already an auditor.")

    auditors.append(member.id)
    config['auditors'] = auditors # Update the list in the config dictionary
    # Save the updated config back to the file
    try:
         with open(CONFIG_FILE, "w") as f:
              json.dump(config, f, indent=4)
         await ctx.reply(f"✅ Successfully added {member.mention} as an auditor.")
         logger.info(f"Owner {ctx.author} added auditor: {member} ({member.id})")
    except IOError as e:
         await ctx.reply(f"❌ Error saving config file: {e}")
         logger.error(f"Failed to save config file after adding auditor: {e}")
         # Optionally revert the change in the global variable if save failed
         auditors.remove(member.id)


@bot.command(name="strip", aliases=["removeauditor", 'deaudit'])
@commands.is_owner()
async def remove_auditor(ctx, member: discord.Member):
    """(Owner Only) Removes a user from the auditor list."""
    global auditors
    config = load_config()
    auditors = config.get('auditors', [])

    if member.id not in auditors:
        return await ctx.reply(f"ℹ️ {member.mention} is not currently an auditor.")

    auditors.remove(member.id)
    config['auditors'] = auditors
    # Save updated config
    try:
        with open(CONFIG_FILE, "w") as f:
             json.dump(config, f, indent=4)
        await ctx.reply(f"✅ Successfully removed {member.mention} from the auditor list.")
        logger.info(f"Owner {ctx.author} removed auditor: {member} ({member.id})")
    except IOError as e:
         await ctx.reply(f"❌ Error saving config file: {e}")
         logger.error(f"Failed to save config file after removing auditor: {e}")
         # Optionally re-add the ID if save failed
         auditors.append(member.id) # Revert change

@bot.command(name="listauditors", aliases=["auditors"])
@commands.is_owner()
async def list_auditors(ctx):
    """(Owner Only) Lists current auditors."""
    # Load directly from config to ensure it's up-to-date
    auditor_ids = load_config().get('auditors', [])
    if not auditor_ids:
         return await ctx.send("There are currently no auditors registered.")

    auditor_mentions = []
    unknown_ids = []
    for auditor_id in auditor_ids:
         user = bot.get_user(auditor_id) # Use get_user, they might not be in the current server
         if user:
              auditor_mentions.append(f"{user.mention} (`{user.id}`)")
         else:
              unknown_ids.append(f"`{auditor_id}`")

    message = "**Current Auditors:**\n" + "\n".join(auditor_mentions)
    if unknown_ids:
         message += "\n\n**Unknown Auditor IDs (User not found/cached):**\n" + "\n".join(unknown_ids)

    await ctx.send(message)


# --- Bot Execution ---
if __name__ == "__main__":
    TOKEN = config_data.get('vtoken')
    if not TOKEN or TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.critical(f"Bot token is missing or placeholder in {CONFIG_FILE}. Please add a valid token.")
    else:
        try:
            bot.run(TOKEN)
        except discord.LoginFailure:
             logger.critical("Failed to log in: Improper token provided.")
        except discord.PrivilegedIntentsRequired:
             logger.critical("Failed to log in: Privileged Intents (Server Members or Message Content or Guild Bans) are not enabled for the bot in the Developer Portal.")
        except Exception as e:
             logger.critical(f"An unexpected error occurred during bot startup: {e}")
