import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import aiohttp
import asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# Load config
with open('config.json', 'r') as f:
    config = json.load(f)

# Load balance data
def load_balances():
    try:
        with open('balances.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_balances(balances):
    with open('balances.json', 'w') as f:
        json.dump(balances, f, indent=4)

# Load blacklist
def load_blacklist():
    try:
        with open('blacklist.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_blacklist(blacklist):
    with open('blacklist.json', 'w') as f:
        json.dump(blacklist, f, indent=4)

# Load warnings
def load_warnings():
    try:
        with open('warnings.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_warnings(warnings):
    with open('warnings.json', 'w') as f:
        json.dump(warnings, f, indent=4)

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix=commands.when_mentioned_or('.', '/'), intents=intents)
balances = load_balances()
blacklisted_users = load_blacklist()
warnings_data = load_warnings()

# TikTok live status tracking
tiktok_live = False

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    print(f'Bot is in {len(bot.guilds)} guilds')
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(e)

# Balance commands
@bot.tree.command(name="addbal", description="Add balance to a user")
@app_commands.default_permissions(administrator=True)
async def addbal(interaction: discord.Interaction, user: discord.User, amount: int):
    if str(user.id) in blacklisted_users:
        await interaction.response.send_message(f"{user.mention} is blacklisted. You cannot add balance to this user.", ephemeral=True)
        return
    
    if amount <= 0:
        await interaction.response.send_message("Amount must be positive!", ephemeral=True)
        return
    
    balances[str(user.id)] = balances.get(str(user.id), 0) + amount
    save_balances(balances)
    await interaction.response.send_message(f"Added {amount} balance to {user.mention}. New balance: {balances[str(user.id)]}")

@bot.tree.command(name="removebal", description="Remove balance from a user")
@app_commands.default_permissions(administrator=True)
async def removebal(interaction: discord.Interaction, user: discord.User, amount: int):
    if str(user.id) not in balances:
        await interaction.response.send_message(f"{user.mention} has no balance!", ephemeral=True)
        return
    
    if amount <= 0:
        await interaction.response.send_message("Amount must be positive!", ephemeral=True)
        return
    
    balances[str(user.id)] = max(0, balances.get(str(user.id), 0) - amount)
    save_balances(balances)
    await interaction.response.send_message(f"Removed {amount} balance from {user.mention}. New balance: {balances[str(user.id)]}")

@bot.tree.command(name="checkbal", description="Check your or someone's balance")
async def checkbal(interaction: discord.Interaction, user: discord.User = None):
    target = user or interaction.user
    balance = balances.get(str(target.id), 0)
    await interaction.response.send_message(f"{target.mention} has {balance} balance!")

@bot.tree.command(name="leaderboardbal", description="Show balance leaderboard")
async def leaderboardbal(interaction: discord.Interaction):
    sorted_balances = sorted(balances.items(), key=lambda x: x[1], reverse=True)[:10]
    
    if not sorted_balances:
        await interaction.response.send_message("No balances found!")
        return
    
    embed = discord.Embed(title="💰 Balance Leaderboard", color=discord.Color.gold())
    for i, (user_id, bal) in enumerate(sorted_balances, 1):
        user = await bot.fetch_user(int(user_id))
        embed.add_field(name=f"{i}. {user.name}", value=f"{bal} coins", inline=False)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="blacklistbal", description="Blacklist a user from receiving balance")
@app_commands.default_permissions(administrator=True)
async def blacklistbal(interaction: discord.Interaction, user: discord.User):
    if str(user.id) in blacklisted_users:
        await interaction.response.send_message(f"{user.mention} is already blacklisted!", ephemeral=True)
        return
    
    blacklisted_users.append(str(user.id))
    save_blacklist(blacklisted_users)
    await interaction.response.send_message(f"{user.mention} has been blacklisted from receiving balance!")

@bot.tree.command(name="unblacklistbal", description="Remove user from balance blacklist")
@app_commands.default_permissions(administrator=True)
async def unblacklistbal(interaction: discord.Interaction, user: discord.User):
    if str(user.id) not in blacklisted_users:
        await interaction.response.send_message(f"{user.mention} is not blacklisted!", ephemeral=True)
        return
    
    blacklisted_users.remove(str(user.id))
    save_blacklist(blacklisted_users)
    await interaction.response.send_message(f"{user.mention} has been removed from the blacklist!")

# Moderation commands
@bot.tree.command(name="warn", description="Warn a user")
@app_commands.default_permissions(moderate_members=True)
async def warn(interaction: discord.Interaction, user: discord.User, reason: str = "No reason provided"):
    if str(user.id) not in warnings_data:
        warnings_data[str(user.id)] = []
    
    warnings_data[str(user.id)].append({
        "reason": reason,
        "moderator": str(interaction.user),
        "date": str(datetime.now())
    })
    save_warnings(warnings_data)
    
    embed = discord.Embed(title="⚠️ User Warned", color=discord.Color.orange())
    embed.add_field(name="User", value=user.mention)
    embed.add_field(name="Reason", value=reason)
    embed.add_field(name="Total Warnings", value=len(warnings_data[str(user.id)]))
    
    await interaction.response.send_message(embed=embed)
    
    try:
        await user.send(f"You have been warned in {interaction.guild.name} for: {reason}")
    except:
        pass

@bot.tree.command(name="warnings", description="Check a user's warnings")
@app_commands.default_permissions(moderate_members=True)
async def view_warnings(interaction: discord.Interaction, user: discord.User):
    user_warnings = warnings_data.get(str(user.id), [])
    
    if not user_warnings:
        await interaction.response.send_message(f"{user.mention} has no warnings.")
        return
    
    embed = discord.Embed(title=f"Warnings for {user.name}", color=discord.Color.red())
    for i, warning in enumerate(user_warnings, 1):
        embed.add_field(name=f"Warning #{i}", value=f"Reason: {warning['reason']}\nModerator: {warning['moderator']}\nDate: {warning['date']}", inline=False)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="clearwarns", description="Clear all warnings for a user")
@app_commands.default_permissions(administrator=True)
async def clearwarns(interaction: discord.Interaction, user: discord.User):
    if str(user.id) in warnings_data:
        del warnings_data[str(user.id)]
        save_warnings(warnings_data)
        await interaction.response.send_message(f"Cleared all warnings for {user.mention}")
    else:
        await interaction.response.send_message(f"{user.mention} has no warnings to clear.")

@bot.tree.command(name="timeout", description="Timeout a user")
@app_commands.default_permissions(moderate_members=True)
async def timeout(interaction: discord.Interaction, user: discord.Member, minutes: int, reason: str = "No reason provided"):
    if minutes > 40320:  # Max 28 days
        await interaction.response.send_message("Timeout cannot exceed 28 days!", ephemeral=True)
        return
    
    duration = timedelta(minutes=minutes)
    await user.timeout(duration, reason=reason)
    await interaction.response.send_message(f"⏰ {user.mention} has been timed out for {minutes} minutes. Reason: {reason}")

# Ghost ping command
@bot.tree.command(name="ghostping", description="Ghost ping a user")
async def ghostping(interaction: discord.Interaction, user: discord.User):
    await interaction.response.send_message(content=f"{user.mention}", delete_after=0.1)
    await interaction.followup.send("👻 Ghost ping sent!", ephemeral=True)

# TikTok live setup
@bot.tree.command(name="setuplive", description="Set up TikTok live notifications channel")
@app_commands.default_permissions(administrator=True)
async def setuplive(interaction: discord.Interaction, channel: discord.TextChannel, tiktok_username: str):
    config['tiktok_live_channel'] = str(channel.id)
    config['tiktok_username'] = tiktok_username
    with open('config.json', 'w') as f:
        json.dump(config, f, indent=4)
    
    embed = discord.Embed(title="✅ TikTok Live Notifications Setup", color=discord.Color.green())
    embed.add_field(name="Channel", value=channel.mention)
    embed.add_field(name="TikTok Username", value=tiktok_username)
    embed.add_field(name="Status", value="Monitoring for live streams...")
    await interaction.response.send_message(embed=embed)
    
    # Start monitoring in background
    bot.loop.create_task(check_tiktok_live(channel))

async def check_tiktok_live(channel):
    global tiktok_live
    await bot.wait_until_ready()
    
    while not bot.is_closed():
        try:
            # Note: You'll need to implement actual TikTok API check
            # This is a simulation - you'll need to use TikTok's official API or web scraping
            # For now, this is a placeholder that checks every 60 seconds
            # You can manually trigger live by running a command
            
            # Placeholder - replace with actual TikTok live check
            # We'll create a manual trigger command
            
            await asyncio.sleep(60)
        except Exception as e:
            print(f"Error checking TikTok live: {e}")
            await asyncio.sleep(60)

@bot.tree.command(name="live", description="Manually trigger live notification (for testing)")
@app_commands.default_permissions(administrator=True)
async def live(interaction: discord.Interaction, tiktok_url: str):
    channel_id = config.get('tiktok_live_channel')
    if not channel_id:
        await interaction.response.send_message("Please setup live notifications with `/setuplive` first!", ephemeral=True)
        return
    
    channel = bot.get_channel(int(channel_id))
    if channel:
        embed = discord.Embed(title="🔴 LIVE NOW!", description=f"**Raul** is now live on TikTok!\nGo watch it!", color=discord.Color.red())
        embed.add_field(name="Watch Here", value=tiktok_url)
        embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/3046/3046124.png")
        
        await channel.send("@everyone", embed=embed)
        await interaction.response.send_message("Live notification sent!", ephemeral=True)
    else:
        await interaction.response.send_message("Channel not found!", ephemeral=True)

# Regular prefix commands for compatibility
@bot.command()
async def ghostping(ctx, user: discord.User):
    await ctx.message.delete()
    await ctx.send(user.mention, delete_after=0.1)

@bot.command()
@commands.has_permissions(administrator=True)
async def setuplive(ctx, channel: discord.TextChannel, tiktok_username: str):
    config['tiktok_live_channel'] = str(channel.id)
    config['tiktok_username'] = tiktok_username
    with open('config.json', 'w') as f:
        json.dump(config, f, indent=4)
    await ctx.send(f"✅ Live notifications set up in {channel.mention} for @{tiktok_username}")

@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason=None):
    await member.ban(reason=reason)
    await ctx.send(f"Banned {member.mention}")

@bot.command()
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason=None):
    await member.kick(reason=reason)
    await ctx.send(f"Kicked {member.mention}")

@bot.command()
async def ping(ctx):
    await ctx.send(f'Pong! {round(bot.latency * 1000)}ms')

# Run bot
bot.run(os.getenv('DISCORD_TOKEN'))
