import discord
from discord.ext import commands
from discord import app_commands
import re
import aiohttp
import json
import os
import base64
import urllib.parse
from typing import List, Set, Dict
from dotenv import load_dotenv

load_dotenv()

with open('config.json', 'r') as f:
    config = json.load(f)

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=config['prefix'], intents=intents)

# Comprehensive patterns for obfuscated and non-obfuscated scripts
PATTERNS = {
    'WebSockets (ws/wss)': [
        r'ws://[^\s"\'<>(){}\[\]]+',
        r'wss://[^\s"\'<>(){}\[\]]+',
        r'WebSocket\(["\']([^"\']+)["\']\)',
        r'new\s+WebSocket\(["\']([^"\']+)["\']\)',
        r'connect\(["\']wss?://[^"\']+["\']',
        r'"wss?://[^"]+"',
        r"'wss?://[^']+'",
    ],
    'HTTP/HTTPS URLs': [
        r'https?://[^\s"\'<>(){}\[\]]+',
        r'game:HttpGet\(["\']([^"\']+)["\']\)',
        r'HttpGetAsync\(["\']([^"\']+)["\']\)',
        r'HttpGet\(["\']([^"\']+)["\']\)',
        r'HttpPost\(["\']([^"\']+)["\']',
        r'syn\.request\({[^}]*Url\s*=\s*["\']([^"\']+)["\']',
        r'request\({[^}]*url\s*=\s*["\']([^"\']+)["\']',
    ],
    'Discord Webhooks': [
        r'https?://discord(?:app)?\.com/api/webhooks/\d+/[a-zA-Z0-9_-]+',
        r'webhook(?:\.discord)?\.com/api/webhooks/\d+/[a-zA-Z0-9_-]+',
        r'"webhook":\s*"([^"]+)"',
        r"'webhook':\s*'([^']+)'",
    ],
    'IP Addresses': [
        r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d{1,5})?\b',
    ],
    'API Keys': [
        r'api[_-]?key["\s:]+[A-Za-z0-9_\-]{10,}',
        r'apikey["\s:]+[A-Za-z0-9_\-]{10,}',
        r'["\']api_key["\']\s*:\s*["\'][A-Za-z0-9_\-]+["\']',
        r'["\']key["\']\s*:\s*["\'][A-Za-z0-9_\-]{20,}["\']',
    ],
    'Tokens': [
        r'token["\s:]+[A-Za-z0-9_\-]{20,}',
        r'["\']token["\']\s*:\s*["\'][A-Za-z0-9_\-]{20,}["\']',
        r'bearer\s+[A-Za-z0-9_\-\.]+',
        r'Bot\s+[A-Za-z0-9_\-\.]{20,}',
    ],
    'Loadstring URLs': [
        r'loadstring\(game:HttpGet\(["\']([^"\']+)["\']\)\)',
        r'loadstring\(([^)]+)\)',
    ],
    'Hidden/Encoded URLs': [
        r'string\.char\([^)]+\)',  # string.char obfuscation
        r'\.\.\s*["\'][^"\']+["\']',  # String concatenation
        r'base64\.decode\(["\']([^"\']+)["\']\)',
        r'HttpService:Base64Decode\(["\']([^"\']+)["\']\)',
    ],
    'Luarmor/Obfuscation Services': [
        r'luarmor\.xyz/[^\s"\']+',
        r'raw\.githubusercontent\.com/[^\s"\']+',
        r'pastebin\.com/[^\s"\']+',
        r'github\.com/[^\s"\']+/blob/[^\s"\']+',
    ],
}

async def fetch_script_content(url: str) -> str:
    """Fetch content from a URL with proper headers"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=15, headers=headers) as response:
                if response.status == 200:
                    return await response.text()
                return None
    except Exception as e:
        return None

def decode_string_char(content: str) -> str:
    """Decode string.char obfuscation"""
    # Match patterns like string.char(104,101,108,108,111)
    char_pattern = r'string\.char\(([^)]+)\)'
    matches = re.findall(char_pattern, content)
    
    for match in matches:
        try:
            numbers = [int(x.strip()) for x in match.split(',') if x.strip().isdigit()]
            if numbers:
                decoded = ''.join(chr(n) for n in numbers)
                content = content.replace(f'string.char({match})', f'"{decoded}"')
        except:
            pass
    return content

def decode_base64(content: str) -> str:
    """Decode base64 encoded strings"""
    b64_pattern = r'(?:HttpService:Base64Decode|base64\.decode)\(["\']([^"\']+)["\']\)'
    matches = re.findall(b64_pattern, content)
    
    for match in matches:
        try:
            decoded = base64.b64decode(match).decode('utf-8', errors='ignore')
            content = content.replace(match, decoded)
        except:
            pass
    return content

def deobfuscate_string(content: str) -> str:
    """Attempt to deobfuscate common patterns"""
    # Remove extra whitespace and newlines
    content = ' '.join(content.split())
    
    # Decode string.char
    content = decode_string_char(content)
    
    # Decode base64
    content = decode_base64(content)
    
    # Look for concatenated strings
    concat_pattern = r'(["\'])([^"\']+)\1\s*\.\.\s*(["\'])([^"\']+)\3'
    content = re.sub(concat_pattern, lambda m: f'"{m.group(2)}{m.group(4)}"', content)
    
    return content

def extract_all_urls(text: str) -> Dict[str, List[str]]:
    """Extract all URLs and sensitive info from text"""
    findings = {}
    text = deobfuscate_string(text)
    
    for category, patterns in PATTERNS.items():
        all_matches = set()
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE | re.DOTALL)
            for match in matches:
                if isinstance(match, tuple):
                    for m in match:
                        if m and len(m) > 5:
                            all_matches.add(str(m).strip())
                elif match and len(str(match)) > 5:
                    all_matches.add(str(match).strip())
        
        if all_matches:
            findings[category] = list(all_matches)
    
    return findings

async def deep_analyze(script: str, depth: int = 0, max_depth: int = 3, visited: set = None) -> Dict:
    """Recursively analyze scripts and fetch nested loadstrings"""
    if visited is None:
        visited = set()
    
    results = {}
    
    # Extract loadstring URLs
    loadstring_pattern = r'loadstring\(game:HttpGet\(["\']([^"\']+)["\']\)\)'
    loadstring_urls = re.findall(loadstring_pattern, script)
    
    # Also look for hidden loadstrings
    hidden_pattern = r'HttpGet\(["\']([^"\']+)["\']\)'
    urls = re.findall(hidden_pattern, script)
    loadstring_urls.extend(urls)
    
    # Analyze current script
    current_findings = extract_all_urls(script)
    if current_findings:
        results['current_script'] = current_findings
    
    # Fetch and analyze nested scripts
    if depth < max_depth:
        for url in set(loadstring_urls):
            if url not in visited and url.startswith('http'):
                visited.add(url)
                fetched = await fetch_script_content(url)
                if fetched:
                    nested_results = await deep_analyze(fetched, depth + 1, max_depth, visited)
                    if nested_results:
                        results[f'nested_script_{depth}_{url[:50]}'] = nested_results
    
    return results

@bot.tree.command(name="httpspy", description="Deep analyze scripts for websockets, webhooks, APIs, and more")
async def httpspy(interaction: discord.Interaction, script: str):
    """Main command to spy on scripts and extract everything"""
    
    await interaction.response.defer(thinking=True)
    
    # Start deep analysis
    try:
        analysis_results = await deep_analyze(script, max_depth=3)
        
        # Also check for direct websocket connections in the input
        direct_websockets = re.findall(r'wss?://[^\s"\'<>(){}\[\]]+', script, re.IGNORECASE)
        
        # Prepare response
        embed = discord.Embed(
            title="🔍 HTTP/WebSocket Spy Results",
            color=discord.Color.green()
        )
        
        total_items = 0
        has_websockets = False
        
        for source, findings in analysis_results.items():
            for category, items in findings.items():
                if items:
                    total_items += len(items)
                    
                    # Highlight websockets
                    if 'WebSocket' in category or 'wss://' in str(items) or 'ws://' in str(items):
                        has_websockets = True
                        category = f"🟢 **{category}**"
                    
                    # Truncate long items
                    display_items = []
                    for item in items[:10]:  # Show first 10
                        if len(str(item)) > 150:
                            display_items.append(str(item)[:150] + "...")
                        else:
                            display_items.append(str(item))
                    
                    items_text = '```\n' + '\n'.join(display_items) + '\n```'
                    if len(items) > 10:
                        items_text += f"\n*... and {len(items) - 10} more*"
                    
                    embed.add_field(
                        name=f"📌 {category} ({len(items)})",
                        value=items_text,
                        inline=False
                    )
        
        # Add direct websocket findings
        if direct_websockets:
            has_websockets = True
            embed.add_field(
                name="🟢 **DIRECT WEBSOCKET DETECTED**",
                value=f"```\n{chr(10).join(direct_websockets[:5])}\n```",
                inline=False
            )
            total_items += len(direct_websockets)
        
        if total_items == 0:
            embed.description = "No URLs, websockets, or sensitive information found in the script."
            embed.color = discord.Color.orange()
        else:
            embed.description = f"**Found {total_items} potential connections/URLs**"
            if has_websockets:
                embed.description += "\n⚠️ **WEBSOCKET CONNECTIONS DETECTED** ⚠️"
            
        embed.set_footer(text="⚠️ These URLs/websockets were extracted from the script. Always verify before executing!")
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        error_embed = discord.Embed(
            title="❌ Error",
            description=f"Failed to analyze script: {str(e)}",
            color=discord.Color.red()
        )
        await interaction.followup.send(embed=error_embed)

@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"Pong! {round(bot.latency * 1000)}ms", ephemeral=True)

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    print(f'Bot is in {len(bot.guilds)} guilds')
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="for websockets | /httpspy"))
    
    # Sync commands
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

bot.run(os.getenv('DISCORD_TOKEN'))
