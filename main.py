import discord
from discord.ext import commands
from discord import app_commands
import websockets
import asyncio
import json
from typing import Dict, Optional
import os
import base64
import hashlib
import re

# Disable voice to avoid audioop issues
discord.voice_client.VoiceClient = None

intents = discord.Intents.default()
intents.message_content = True
intents.webhooks = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Store data for each guild
guild_data: Dict[int, dict] = {}

def detect_encryption(data: str) -> dict:
    """Detect if data is encrypted and what type of encryption"""
    result = {
        'is_encrypted': False,
        'encryption_type': None,
        'decrypted_data': None
    }
    
    # Check for base64 encoded data
    base64_pattern = r'^[A-Za-z0-9+/]+=*$'
    if re.match(base64_pattern, data.strip()) and len(data) > 20:
        try:
            decoded = base64.b64decode(data)
            # Check if decoded data looks like JSON or text
            try:
                decoded_str = decoded.decode('utf-8')
                # Verify it's mostly printable
                if decoded_str.isprintable() or '{' in decoded_str or '[' in decoded_str:
                    result['is_encrypted'] = True
                    result['encryption_type'] = 'base64'
                    result['decrypted_data'] = decoded_str
                    return result
            except:
                pass
        except:
            pass
    
    # Check for hex encoded data
    hex_pattern = r'^[0-9a-fA-F]+$'
    if re.match(hex_pattern, data.strip()) and len(data) % 2 == 0 and len(data) > 20:
        try:
            decoded = bytes.fromhex(data).decode('utf-8', errors='ignore')
            if decoded.isprintable() and len(decoded) > 5:
                result['is_encrypted'] = True
                result['encryption_type'] = 'hex'
                result['decrypted_data'] = decoded
                return result
        except:
            pass
    
    # Check for URL encoded data
    if '%' in data and len(data) > 20:
        try:
            from urllib.parse import unquote
            decoded = unquote(data)
            if decoded != data and decoded.isprintable():
                result['is_encrypted'] = True
                result['encryption_type'] = 'url_encoded'
                result['decrypted_data'] = decoded
                return result
        except:
            pass
    
    # Check for common encryption patterns
    non_printable_count = sum(1 for c in data if not c.isprintable() and c not in '\n\r\t')
    if non_printable_count > len(data) * 0.3:  # More than 30% non-printable
        result['is_encrypted'] = True
        result['encryption_type'] = 'binary_encrypted'
        result['decrypted_data'] = "Binary/encrypted data (showing hex):\n" + data[:200].encode().hex()
        return result
    
    return result

def try_decrypt_data(data: str) -> str:
    """Attempt to decrypt various encryption types"""
    detection = detect_encryption(data)
    
    if not detection['is_encrypted']:
        return data
    
    if detection['encryption_type'] == 'base64':
        try:
            decoded = base64.b64decode(data).decode('utf-8', errors='ignore')
            # Try to pretty print if it's JSON
            try:
                json_data = json.loads(decoded)
                decoded = json.dumps(json_data, indent=2)
            except:
                pass
            return f"🔓 **Decrypted (Base64):**\n```\n{decoded[:1500]}\n```"
        except:
            return f"⚠️ **Base64 encoded but couldn't decode:**\n```\n{data[:500]}\n```"
    
    elif detection['encryption_type'] == 'hex':
        try:
            decoded = bytes.fromhex(data).decode('utf-8', errors='ignore')
            return f"🔓 **Decrypted (Hex):**\n```\n{decoded[:1500]}\n```"
        except:
            return f"⚠️ **Hex encoded but couldn't decode:**\n```\n{data[:500]}\n```"
    
    elif detection['encryption_type'] == 'url_encoded':
        try:
            from urllib.parse import unquote
            decoded = unquote(data)
            return f"🔓 **Decrypted (URL Decoded):**\n```\n{decoded[:1500]}\n```"
        except:
            return f"⚠️ **URL encoded but couldn't decode:**\n```\n{data[:500]}\n```"
    
    else:
        return f"🔒 **Encrypted Data Detected ({detection['encryption_type']}):**\n```\n{data[:500]}\n```\n*Auto-decryption not available*"

class LogManager:
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        self.channels = {
            'ch1': None,
            'ch2': None,
            'ch3': None
        }
        self.webhooks = {
            'ch1': None,
            'ch2': None,
            'ch3': None
        }
        self.websocket = None
        self.websocket_task = None
        self.is_running = False
        self.current_websocket_uri = None

    async def create_webhooks(self, guild: discord.Guild):
        """Create webhooks in all three channels"""
        for key, channel_id in self.channels.items():
            if channel_id:
                channel = guild.get_channel(channel_id)
                if channel:
                    try:
                        existing_webhooks = await channel.webhooks()
                        webhook = next((w for w in existing_webhooks if w.name == f"LogBot-{key}"), None)
                        if not webhook:
                            webhook = await channel.create_webhook(name=f"LogBot-{key}")
                        self.webhooks[key] = webhook
                    except discord.Forbidden:
                        print(f"No permission to create webhook in {channel.name}")
                    except Exception as e:
                        print(f"Error creating webhook: {e}")

    async def send_log(self, key: str, message: str):
        """Send log to specific webhook"""
        webhook = self.webhooks.get(key)
        if webhook:
            try:
                if len(message) > 1900:
                    message = message[:1900] + "..."
                await webhook.send(message)
            except Exception as e:
                print(f"Error sending log: {e}")

    async def send_to_all(self, message: str):
        """Send log to all webhooks"""
        for key in self.webhooks:
            await self.send_log(key, message)

    async def websocket_handler(self, uri: str, auth_token: str = None):
        """Handle WebSocket connection and receive logs"""
        retry_count = 0
        max_retries = 3
        
        while retry_count < max_retries and self.is_running:
            try:
                # Prepare connection parameters
                kwargs = {
                    'ping_interval': 20,
                    'ping_timeout': 60,
                    'close_timeout': 10,
                    'max_size': 10_485_760
                }
                
                # Add authentication if provided
                if auth_token:
                    kwargs['extra_headers'] = {
                        'Authorization': f'Bearer {auth_token}',
                        'User-Agent': 'Discord-LogBot/1.0'
                    }
                
                async with websockets.connect(uri, **kwargs) as websocket:
                    self.websocket = websocket
                    await self.send_to_all(f"✅ Connected to WebSocket: `{uri}`")
                    retry_count = 0
                    
                    # Send initial subscription message
                    try:
                        subscribe_msg = json.dumps({"type": "subscribe", "channel": "logs"})
                        await websocket.send(subscribe_msg)
                    except:
                        pass
                    
                    while self.is_running:
                        try:
                            message = await asyncio.wait_for(websocket.recv(), timeout=30)
                            
                            # Try to parse as JSON
                            try:
                                data = json.loads(message)
                                # Check for error messages
                                if isinstance(data, dict):
                                    if 'code' in data and data.get('code') == 1008:
                                        await self.send_to_all("⚠️ Subscription error: Please check your authentication or subscription ID")
                                        continue
                                    if 'error' in data:
                                        await self.send_to_all(f"⚠️ WebSocket Error: {data['error']}")
                                        continue
                                
                                # Try to detect encryption in string values
                                if isinstance(data, dict):
                                    for k, v in data.items():
                                        if isinstance(v, str) and len(v) > 20:
                                            encrypted_check = detect_encryption(v)
                                            if encrypted_check['is_encrypted']:
                                                data[k] = f"[ENCRYPTED: {encrypted_check['encryption_type']}]"
                                
                                json_str = json.dumps(data, indent=2)
                                log_text = f"📝 **Log Received:**\n```json\n{json_str[:1500]}\n```"
                            except json.JSONDecodeError:
                                # Not JSON, try to decrypt
                                log_text = try_decrypt_data(message)
                            
                            await self.send_to_all(log_text)
                            
                        except asyncio.TimeoutError:
                            # Send ping to keep connection alive
                            try:
                                await websocket.send(json.dumps({"type": "ping"}))
                            except:
                                pass
                            continue
                        except websockets.exceptions.ConnectionClosed as e:
                            await self.send_to_all(f"⚠️ WebSocket connection closed: {e}")
                            break
                        except Exception as e:
                            await self.send_to_all(f"❌ Error: {str(e)[:500]}")
                            continue
                            
            except websockets.exceptions.InvalidURI:
                await self.send_to_all(f"❌ Invalid WebSocket URI: {uri}")
                break
            except websockets.exceptions.WebSocketException as e:
                retry_count += 1
                if retry_count < max_retries:
                    wait_time = retry_count * 5
                    await self.send_to_all(f"⚠️ Connection error: Retrying in {wait_time}s... ({retry_count}/{max_retries})")
                    await asyncio.sleep(wait_time)
                else:
                    await self.send_to_all(f"❌ Failed to connect: {e}")
                    break
            except Exception as e:
                await self.send_to_all(f"❌ Unexpected error: {str(e)[:500]}")
                break
        
        self.is_running = False
        self.websocket = None

    async def start_logging(self, uri: str, auth_token: str = None):
        """Start the logging process"""
        if self.is_running:
            return False
        
        self.current_websocket_uri = uri
        self.is_running = True
        self.websocket_task = asyncio.create_task(self.websocket_handler(uri, auth_token))
        return True

    async def stop_logging(self):
        """Stop the logging process"""
        self.is_running = False
        if self.websocket_task:
            self.websocket_task.cancel()
            try:
                await self.websocket_task
            except:
                pass
        if self.websocket:
            await self.websocket.close()
        await self.send_to_all("🛑 **Logging stopped**")
        return True

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    print(f'Bot is in {len(bot.guilds)} guild(s)')
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Error syncing commands: {e}")

@bot.tree.command(name="setchannel", description="Set the three channels for logging")
@app_commands.describe(
    ch1="First channel for logs",
    ch2="Second channel for logs", 
    ch3="Third channel for logs"
)
async def setchannel(interaction: discord.Interaction, ch1: discord.TextChannel, ch2: discord.TextChannel, ch3: discord.TextChannel):
    if not interaction.guild:
        await interaction.response.send_message("Use this in a server!", ephemeral=True)
        return
    
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need admin perms!", ephemeral=True)
        return
    
    if interaction.guild_id not in guild_data:
        guild_data[interaction.guild_id] = LogManager(interaction.guild_id)
    
    manager = guild_data[interaction.guild_id]
    manager.channels = {'ch1': ch1.id, 'ch2': ch2.id, 'ch3': ch3.id}
    await manager.create_webhooks(interaction.guild)
    
    embed = discord.Embed(
        title="✅ Channels Configured",
        description=f"1. {ch1.mention}\n2. {ch2.mention}\n3. {ch3.mention}",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="connect", description="Connect to a WebSocket")
@app_commands.describe(
    websocket="WebSocket URL (ws:// or wss://)",
    auth_token="Optional: Authentication token"
)
async def connect(interaction: discord.Interaction, websocket: str, auth_token: str = None):
    if not interaction.guild:
        await interaction.response.send_message("Use this in a server!", ephemeral=True)
        return
    
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need admin perms!", ephemeral=True)
        return
    
    if interaction.guild_id not in guild_data:
        await interaction.response.send_message("Use /setchannel first!", ephemeral=True)
        return
    
    manager = guild_data[interaction.guild_id]
    
    await interaction.response.send_message(f"Connecting to `{websocket}`...", ephemeral=True)
    success = await manager.start_logging(websocket, auth_token)
    
    if success:
        await interaction.followup.send(f"✅ Connected and logging!")
    else:
        await interaction.followup.send("❌ Already logging! Use /stop first.")

@bot.tree.command(name="stop", description="Stop logging")
async def stop(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Use this in a server!", ephemeral=True)
        return
    
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need admin perms!", ephemeral=True)
        return
    
    if interaction.guild_id not in guild_data:
        await interaction.response.send_message("No active session!", ephemeral=True)
        return
    
    manager = guild_data[interaction.guild_id]
    
    if not manager.is_running:
        await interaction.response.send_message("Not currently logging!", ephemeral=True)
        return
    
    await interaction.response.send_message("Stopping...", ephemeral=True)
    await manager.stop_logging()
    await interaction.followup.send("✅ Stopped!")

@bot.tree.command(name="start", description="Restart logging")
async def start(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Use this in a server!", ephemeral=True)
        return
    
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need admin perms!", ephemeral=True)
        return
    
    if interaction.guild_id not in guild_data:
        await interaction.response.send_message("No config found! Use /setchannel first.", ephemeral=True)
        return
    
    manager = guild_data[interaction.guild_id]
    
    if not manager.current_websocket_uri:
        await interaction.response.send_message("No previous connection! Use /connect first.", ephemeral=True)
        return
    
    if manager.is_running:
        await interaction.response.send_message("Already running!", ephemeral=True)
        return
    
    await interaction.response.send_message("Restarting...", ephemeral=True)
    await manager.start_logging(manager.current_websocket_uri)
    await interaction.followup.send("✅ Restarted!")

@bot.tree.command(name="status", description="Check status")
async def status(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Use this in a server!", ephemeral=True)
        return
    
    if interaction.guild_id not in guild_data:
        await interaction.response.send_message("No configuration!", ephemeral=True)
        return
    
    manager = guild_data[interaction.guild_id]
    
    embed = discord.Embed(title="Status", color=discord.Color.blue())
    
    # Channels status
    channels_text = []
    for key, cid in manager.channels.items():
        if cid:
            ch = interaction.guild.get_channel(cid)
            channels_text.append(f"✅ {key}: {ch.mention if ch else 'Deleted'}")
        else:
            channels_text.append(f"❌ {key}: Not set")
    embed.add_field(name="Channels", value="\n".join(channels_text), inline=False)
    
    # Connection status
    if manager.is_running:
        embed.add_field(name="Status", value="🟢 **RUNNING**", inline=False)
        embed.add_field(name="WebSocket", value=f"`{manager.current_websocket_uri}`", inline=False)
    else:
        embed.add_field(name="Status", value="🔴 **STOPPED**", inline=False)
    
    await interaction.response.send_message(embed=embed)

if __name__ == "__main__":
    TOKEN = os.getenv('DISCORD_TOKEN')
    if not TOKEN:
        print("Error: DISCORD_TOKEN not set!")
    else:
        bot.run(TOKEN)
