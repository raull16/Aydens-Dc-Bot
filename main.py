import discord
from discord.ext import commands
from discord import app_commands
import re
import aiohttp
import json
import os
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()

# Load config
with open('config.json', 'r') as f:
    config = json.load(f)

# Bot setup
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=config['prefix'], intents=intents)

# Patterns to detect various URLs and sensitive info
PATTERNS = {
    'WebSocket': r'ws://[^\s"\']+|wss://[^\s"\']+',
    'HTTP/HTTPS URL': r'https?://[^\s"\']+',
    'Raw GitHub URL': r'https?://raw\.githubusercontent\.com/[^\s"\']+',
    'GitHub URL': r'https?://github\.com/[^\s"\']+',
    'Replit URL': r'https?://replit\.com/[^\s"\']+|https?://[^\s"\']+\.repl\.co',
    'Pastebin URL': r'https?://pastebin\.com/[^\s"\']+',
    'Discord Webhook': r'https?://discord\.com/api/webhooks/[^\s"\']+|https?://discordapp\.com/api/webhooks/[^\s"\']+',
    'Luarmor URL': r'https?://luarmor\.xyz/[^\s"\']+',
    'IP Address': r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b',
    'API Key': r'api[_-]?key["\s:=]+[A-Za-z0-9_-]+|apikey["\s:=]+[A-Za-z0-9_-]+',
    'Token': r'token["\s:=]+[A-Za-z0-9_-]+',
    'Webhook': r'webhook["\s:=]+[A-Za-z0-9_-]+',
    'Loadstring URL': r'loadstring\(game:HttpGet\(["\']([^"\']+)["\']\)\)',
    'Raw Script URL': r'game:HttpGet\(["\']([^"\']+)["\']\)',
}

class HTTPSpyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=config['prefix'], intents=intents)
    
    async def setup_hook(self):
        await self.tree.sync()
        print(f"Synced commands for {self.user}")

bot = HTTPSpyBot()

async def fetch_script_content(url):
    """Fetch content from a URL"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as response:
                if response.status == 200:
                    return await response.text()
                else:
                    return None
    except Exception as e:
        return None

def extract_urls_from_loadstring(script):
    """Extract URLs from loadstring and similar patterns"""
    urls = []
    
    # Pattern for loadstring(game:HttpGet("URL"))
    loadstring_pattern = r'loadstring\(game:HttpGet\(["\']([^"\']+)["\']\)\)'
    matches = re.findall(loadstring_pattern, script)
    urls.extend(matches)
    
    # Pattern for game:HttpGet("URL")
    httpget_pattern = r'game:HttpGet\(["\']([^"\']+)["\']\)'
    matches = re.findall(httpget_pattern, script)
    urls.extend(matches)
    
    # Pattern for syn.request({Url = "URL"})
    syn_request_pattern = r'Url\s*=\s*["\']([^"\']+)["\']'
    matches = re.findall(syn_request_pattern, script)
    urls.extend(matches)
    
    return list(set(urls))

def analyze_script(content):
    """Analyze script for URLs and sensitive information"""
    findings = {}
    
    for category, pattern in PATTERNS.items():
        matches = re.findall(pattern, content, re.IGNORECASE)
        if matches:
            # Clean up matches (remove duplicates)
            unique_matches = list(set(matches))
            findings[category] = unique_matches
    
    # Special extraction for nested loadstrings
    nested_urls = extract_urls_from_loadstring(content)
    if nested_urls:
        findings['Extracted Loadstring URLs'] = nested_urls
    
    return findings

@bot.tree.command(name="httpspy", description="Analyze a script for URLs, webhooks, API keys, and sensitive information")
async def httpspy(interaction: discord.Interaction, script: str):
    """Main command to spy on HTTP requests and extract URLs from scripts"""
    
    await interaction.response.defer(thinking=True)
    
    # Check if it's a loadstring or direct script
    findings = {}
    raw_content = script
    
    # Extract URLs from the provided script
    extracted_urls = extract_urls_from_loadstring(script)
    
    # If there are URLs in loadstring, fetch and analyze them
    if extracted_urls:
        for url in extracted_urls:
            # Fetch the content from the URL
            fetched_content = await fetch_script_content(url)
            if fetched_content:
                # Analyze the fetched content
                url_findings = analyze_script(fetched_content)
                findings[f"Content from: {url}"] = url_findings
                
                # Also analyze the raw script
                raw_findings = analyze_script(script)
                findings["Original Script Analysis"] = raw_findings
            else:
                findings[f"Failed to fetch: {url}"] = {"Error": "Could not fetch content from this URL"}
    else:
        # Just analyze the provided script directly
        findings = analyze_script(script)
    
    # Prepare response embed
    if not findings or all(len(v) == 0 for v in findings.values() if isinstance(v, dict)):
        embed = discord.Embed(
            title="🔍 HTTP Spy Results",
            description="No URLs, webhooks, API keys, or sensitive information found in the script.",
            color=discord.Color.orange()
        )
        await interaction.followup.send(embed=embed)
        return
    
    # Create embed with findings
    embed = discord.Embed(
        title="🔍 HTTP Spy Results",
        description=f"Found {sum(len(v) for v in findings.values() if isinstance(v, dict))} potential items",
        color=discord.Color.green()
    )
    
    for source, data in findings.items():
        if isinstance(data, dict) and data:
            for category, items in data.items():
                if items:
                    # Truncate long URLs
                    items_text = '\n'.join([item[:100] + '...' if len(item) > 100 else item for item in items[:5]])
                    if len(items) > 5:
                        items_text += f"\n*... and {len(items) - 5} more*"
                    
                    embed.add_field(
                        name=f"📌 {category} ({len(items)})",
                        value=f"```{items_text}```",
                        inline=False
                    )
        elif isinstance(data, list) and data:
            items_text = '\n'.join([item[:100] + '...' if len(item) > 100 else item for item in data[:5]])
            if len(data) > 5:
                items_text += f"\n*... and {len(data) - 5} more*"
            embed.add_field(
                name=f"📌 {source}",
                value=f"```{items_text}```",
                inline=False
            )
    
    # Add warning about potentially malicious content
    embed.set_footer(text="⚠️ Always verify URLs before executing unknown scripts!")
    
    await interaction.followup.send(embed=embed)

# Simple ping command to check if bot is alive
@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"Pong! {round(bot.latency * 1000)}ms", ephemeral=True)

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    print(f'Bot is in {len(bot.guilds)} guilds')
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="for scripts | /httpspy"))

# Run bot
bot.run(os.getenv('DISCORD_TOKEN'))
