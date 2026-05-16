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
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
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
            if decoded.isprintable():
                result['is_encrypted'] = True
                result['encryption_type'] = 'hex'
                result['decrypted_data'] = decoded
                return result
        except:
            pass
    
    # Check for Fernet encryption (starts with gAAAAA)
    if data.startswith('gAAAAA'):
        try:
            # This is Fernet encrypted - we'd need the key
            result['is_encrypted'] = True
            result['encryption_type'] = 'fernet'
            result['decrypted_data'] = "🔐 Fernet encrypted - requires decryption key"
            return result
        except:
            pass
    
    # Check for common encryption patterns
    encryption_indicators = [
        ('AES', r'[^\x20-\x7E]{10,}'),  # Non-printable chars
        ('Binary', r'[\x00-\x08\x0B\x0C\x0E-\x1F]{5,}')  # Control characters
    ]
    
    for enc_type, pattern in encryption_indicators:
        if re.search(pattern, data):
            result['is_encrypted'] = True
            result['encryption_type'] = enc_type
            result['decrypted_data'] = f"🔒 {enc_type} encrypted data (cannot auto-decrypt without key)"
            return result
    
    return result

def try_decrypt_data(data: str, encryption_key: str = None) -> str:
    """Attempt to decrypt various encryption types"""
    detection = detect_encryption(data)
    
    if not detection['is_encrypted']:
        return data
    
    if detection['encryption_type'] == 'base64':
        try:
            decoded = base64.b64decode(data).decode('utf-8', errors='ignore')
            return f"🔓 **Decrypted (Base64):**\n```\n{decoded}\n```"
        except:
            return f"⚠️ **Base64 encoded but couldn't decode:**\n```\n{data}\n```"
    
    elif detection['encryption_type'] == 'hex':
        try:
            decoded = bytes.fromhex(data).decode('utf-8', errors='ignore')
            return f"🔓 **Decrypted (Hex):**\n```\n{decoded}\n```"
        except:
            return f"⚠️ **Hex encoded but couldn't decode:**\n```\n{data}\n```"
    
    elif detection['encryption_type'] == 'fernet' and encryption_key:
        try:
            f = Fernet(encryption_key)
            decrypted = f.decrypt(data.encode()).decode()
            return f"🔓 **Decrypted (Fernet):**\n```\n{decrypted}\n```"
        except:
            return f"🔐 **Fernet encrypted (invalid key or corrupted):**\n```\n{data[:500]}\n```"
    
    else:
        return f"🔒 **Encrypted Data Detected ({detection['encryption_type']}):**\n```\n{data[:500]}\n```\n*Auto-decryption not available without key*"

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
        self.encryption_key = None

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

    async def websocket_handler(self, uri: str, headers: dict = None):
        """Handle WebSocket connection and receive logs with improved error handling"""
        retry_count = 0
        max_retries = 3
        
        while retry_count < max_retries and self.is_running:
            try:
                # Add connection options
                extra_headers = headers or {
                    'User-Agent': 'Discord-LogBot/1.0',
                    'Accept-Encoding': 'gzip, deflate',
                    'Connection': 'Upgrade',
                    'Upgrade': 'websocket'
                }
                
                async with websockets.connect(
                    uri,
                    extra_headers=extra_headers,
                    ping_interval=20,
                    ping_timeout=60,
                    close_timeout=10,
                    max_size=10_485_760  # 10MB max message size
                ) as websocket:
                    self.websocket = websocket
                    await self.send_to_all(f"✅ Connected to WebSocket: `{uri}`")
                    retry_count = 0  # Reset retry count on successful connection
                    
                    # Send initial subscription message if needed
                    subscription_msg = {
                        "type": "subscribe",
                        "channel": "logs"
                    }
                    try:
                        await websocket.send(json.dumps(subscription_msg))
                        await self.send_to_all("📡 Subscription request sent")
                    except:
                        pass
                    
                    while self.is_running:
                        try:
                            message = await asyncio.wait_for(websocket.recv(), timeout=30)
                            
                            # Try to parse as JSON
                            try:
                                data = json.loads(message)
                                # Check if it's an error message
                                if isinstance(data, dict) and 'error' in data:
                                    await self.send_to_all(f"⚠️ WebSocket Error: {data['error']}")
                                    continue
                                
                                # Format JSON nicely
                                json_str = json.dumps(data, indent=2)
                                
                                # Detect encryption in JSON values
                                if isinstance(data, dict):
                                    for key, value in data.items():
                                        if isinstance(value, str) and len(value) > 20:
                                            encrypted_check = detect_encryption(value)
                                            if encrypted_check['is_encrypted']:
                                                data[key] = f"[ENCRYPTED: {encrypted_check['encryption_type']}]"
                                
                                log_text = f"📝 **Log Received:**\n```json\n{json_str[:1500]}\n```"
                            except json.JSONDecodeError:
                                # Not JSON, check for encryption
                                decrypted_message = try_decrypt_data(message, self.encryption_key)
                                log_text = decrypted_message
                            
                            await self.send_to_all(log_text)
                            
                        except asyncio.TimeoutError:
                            # Send heartbeat/ping to keep connection alive
                            try:
                                await websocket.ping()
                            except:
                                pass
                            continue
                        except websockets.exceptions.ConnectionClosed as e:
                            await self.send_to_all(f"⚠️ WebSocket connection closed: {e}")
                            break
                        except Exception as e:
                            await self.send_to_all(f"❌ Error receiving log: {str(e)[:500]}")
                            continue
                            
            except websockets.exceptions.InvalidURI as e:
                await self.send_to_all(f"❌ Invalid WebSocket URI: {e}")
                break
            except websockets.exceptions.WebSocketException as e:
                retry_count += 1
                if retry_count < max_retries:
                    wait_time = retry_count * 5
                    await self.send_to_all(f"⚠️ Connection error: {e}\nRetrying in {wait_time} seconds... (Attempt {retry_count}/{max_retries})")
                    await asyncio.sleep(wait_time)
                else:
                    await self.send_to_all(f"❌ Failed to connect after {max_retries} attempts: {e}")
                    break
            except Exception as e:
                await self.send_to_all(f"❌ Unexpected error: {str(e)[:500]}")
                break
        
        self.is_running = False
        self.websocket = None

    async def start_logging(self, uri: str, headers: dict = None):
        """Start the logging process"""
        if self.is_running:
            return False
        
        self.current_websocket_uri = uri
        self.is_running = True
        self.websocket_task = asyncio.create_task(self.websocket_handler(uri, headers))
        return True

    async def stop_logging(self):
        """Stop the logging process"""
        self.is_running = False
        if self.websocket_task:
            self.websocket_task.cancel()
            try:
                await self.websocket_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                print(f"Error stopping websocket task: {e}")
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
        await interaction.response.send_message("This command must be used in a server!", ephemeral=True)
        return
    
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permissions!", ephemeral=True)
        return
    
    if interaction.guild_id not in guild_data:
        guild_data[interaction.guild_id] = LogManager(interaction.guild_id)
    
    manager = guild_data[interaction.guild_id]
    
    manager.channels = {
        'ch1': ch1.id,
        'ch2': ch2.id,
        'ch3': ch3.id
    }
    
    await manager.create_webhooks(interaction.guild)
    
    channel_names = f"1. {ch1.mention}\n2. {ch2.mention}\n3. {ch3.mention}"
    embed = discord.Embed(
        title="✅ Channels Configured",
        description=f"Webhooks have been created in:\n{channel_names}",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="connect", description="Connect to a WebSocket and start receiving logs")
@app_commands.describe(
    websocket="The WebSocket URL to connect to (ws:// or wss://)",
    auth_token="Optional: Authentication token for the WebSocket",
    subscription="Optional: Subscription ID or channel name"
)
async def connect(
    interaction: discord.Interaction, 
    websocket: str, 
    auth_token: str = None,
    subscription: str = None
):
    if not interaction.guild:
        await interaction.response.send_message("This command must be used in a server!", ephemeral=True)
        return
    
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permissions!", ephemeral=True)
        return
    
    if interaction.guild_id not in guild_data:
        await interaction.response.send_message("Please use `/setchannel` first to configure channels!", ephemeral=True)
        return
    
    manager = guild_data[interaction.guild_id]
    
    if not any(manager.webhooks.values()):
        await interaction.response.send_message("Webhooks not found. Please reconfigure channels with `/setchannel`!", ephemeral=True)
        return
    
    # Prepare headers for authentication
    headers = {}
    if auth_token:
        headers['Authorization'] = f'Bearer {auth_token}'
        headers['X-Auth-Token'] = auth_token
    
    if subscription:
        headers['X-Subscription-ID'] = subscription
    
    await interaction.response.send_message(f"🔄 Connecting to WebSocket: `{websocket}`...", ephemeral=True)
    
    success = await manager.start_logging(websocket, headers if headers else None)
    if success:
        await interaction.followup.send(f"✅ Started logging from WebSocket: `{websocket}`")
    else:
        await interaction.followup.send("❌ Already logging! Use `/stop` first if you want to restart.")

@bot.tree.command(name="stop", description="Stop all logging activities")
async def stop(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("This command must be used in a server!", ephemeral=True)
        return
    
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permissions!", ephemeral=True)
        return
    
    if interaction.guild_id not in guild_data:
        await interaction.response.send_message("No active logging session found!", ephemeral=True)
        return
    
    manager = guild_data[interaction.guild_id]
    
    if not manager.is_running:
        await interaction.response.send_message("No active logging session to stop!", ephemeral=True)
        return
    
    await interaction.response.send_message("🛑 Stopping logging...", ephemeral=True)
    await manager.stop_logging()
    await interaction.followup.send("✅ Logging stopped successfully!")

@bot.tree.command(name="start", description="Start logging again with the previously connected WebSocket")
async def start(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("This command must be used in a server!", ephemeral=True)
        return
    
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permissions!", ephemeral=True)
        return
    
    if interaction.guild_id not in guild_data:
        await interaction.response.send_message("No configuration found! Please use `/setchannel` first.", ephemeral=True)
        return
    
    manager = guild_data[interaction.guild_id]
    
    if not manager.current_websocket_uri:
        await interaction.response.send_message("No WebSocket connection was established before. Please use `/connect` first.", ephemeral=True)
        return
    
    if manager.is_running:
        await interaction.response.send_message("Logging is already running!", ephemeral=True)
        return
    
    await interaction.response.send_message(f"🔄 Restarting logging to `{manager.current_websocket_uri}`...", ephemeral=True)
    success = await manager.start_logging(manager.current_websocket_uri)
    if success:
        await interaction.followup.send(f"✅ Logging restarted successfully!")
    else:
        await interaction.followup.send("❌ Failed to restart logging!")

@bot.tree.command(name="status", description="Check the current logging status")
async def status(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("This command must be used in a server!", ephemeral=True)
        return
    
    if interaction.guild_id not in guild_data:
        await interaction.response.send_message("No configuration found! Use `/setchannel` to set up channels.", ephemeral=True)
        return
    
    manager = guild_data[interaction.guild_id]
    
    embed = discord.Embed(title="📊 Logging Status", color=discord.Color.blue())
    
    channels_configured = []
    for key, channel_id in manager.channels.items():
        if channel_id:
            channel = interaction.guild.get_channel(channel_id)
            if channel:
                channels_configured.append(f"✅ {key}: {channel.mention}")
            else:
                channels_configured.append(f"❌ {key}: Deleted channel")
        else:
            channels_configured.append(f"❌ {key}: Not set")
    
    embed.add_field(name="Channels", value="\n".join(channels_configured), inline=False)
    
    webhooks_status = []
    for key, webhook in manager.webhooks.items():
        if webhook:
            webhooks_status.append(f"✅ {key}: Created")
        else:
            webhooks_status.append(f"❌ {key}: Missing")
    
    embed.add_field(name="Webhooks", value="\n".join(webhooks_status), inline=False)
    
    if manager.is_running:
        embed.add_field(name="Status", value="🟢 **Connected & Logging**", inline=False)
        embed.add_field(name="WebSocket", value=f"`{manager.current_websocket_uri}`", inline=False)
    else:
        embed.add_field(name="Status", value="🔴 **Stopped**", inline=False)
        if manager.current_websocket_uri:
            embed.add_field(name="Last WebSocket", value=f"`{manager.current_websocket_uri}`", inline=False)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="setkey", description="Set encryption key for decrypting logs (Fernet keys only)")
@app_commands.describe(encryption_key="The Fernet encryption key (starts with gAAAAA)")
async def setkey(interaction: discord.Interaction, encryption_key: str):
    if not interaction.guild:
        await interaction.response.send_message("This command must be used in a server!", ephemeral=True)
        return
    
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permissions!", ephemeral=True)
        return
    
    if interaction.guild_id not in guild_data:
        guild_data[interaction.guild_id] = LogManager(interaction.guild_id)
    
    manager = guild_data[interaction.guild_id]
    manager.encryption_key = encryption_key
    
    embed = discord.Embed(
        title="🔑 Encryption Key Set",
        description="The bot will now attempt to decrypt Fernet-encrypted logs using this key.",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

# Add new dependencies
# Update requirements.txt with: cryptography==41.0.7
