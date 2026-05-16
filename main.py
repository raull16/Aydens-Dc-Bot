import discord
from discord.ext import commands
from discord import app_commands
import websockets
import asyncio
import json
from typing import Dict, Optional
import os

# Disable voice to avoid audioop issues
discord.voice_client.VoiceClient = None

intents = discord.Intents.default()
intents.message_content = True
intents.webhooks = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Store data for each guild
guild_data: Dict[int, dict] = {}

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
                        # Check if webhook already exists
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
                # Truncate message if too long (Discord limit is 2000)
                if len(message) > 1900:
                    message = message[:1900] + "..."
                await webhook.send(message)
            except Exception as e:
                print(f"Error sending log: {e}")

    async def send_to_all(self, message: str):
        """Send log to all webhooks"""
        for key in self.webhooks:
            await self.send_log(key, message)

    async def websocket_handler(self, uri: str):
        """Handle WebSocket connection and receive logs"""
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=60) as websocket:
                self.websocket = websocket
                await self.send_to_all(f"✅ Connected to WebSocket: `{uri}`")
                
                while self.is_running:
                    try:
                        message = await asyncio.wait_for(websocket.recv(), timeout=30)
                        
                        # Try to parse as JSON for better formatting
                        try:
                            data = json.loads(message)
                            log_text = f"📝 **Log Received:**\n```json\n{json.dumps(data, indent=2)[:1500]}\n```"
                        except:
                            log_text = f"📝 **Log Received:**\n```\n{message[:1500]}\n```"
                        
                        await self.send_to_all(log_text)
                    except asyncio.TimeoutError:
                        # Keep connection alive
                        continue
                    except websockets.exceptions.ConnectionClosed as e:
                        await self.send_to_all(f"⚠️ WebSocket connection closed: {e}")
                        break
                    except Exception as e:
                        await self.send_to_all(f"❌ Error receiving log: {str(e)[:500]}")
                        break
        except Exception as e:
            await self.send_to_all(f"❌ Failed to connect to WebSocket: {str(e)[:500]}")
        finally:
            self.is_running = False
            self.websocket = None

    async def start_logging(self, uri: str):
        """Start the logging process"""
        if self.is_running:
            return False
        
        self.current_websocket_uri = uri
        self.is_running = True
        self.websocket_task = asyncio.create_task(self.websocket_handler(uri))
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
    # Check permissions
    if not interaction.guild:
        await interaction.response.send_message("This command must be used in a server!", ephemeral=True)
        return
    
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permissions!", ephemeral=True)
        return
    
    # Initialize guild data if not exists
    if interaction.guild_id not in guild_data:
        guild_data[interaction.guild_id] = LogManager(interaction.guild_id)
    
    manager = guild_data[interaction.guild_id]
    
    # Store channel IDs
    manager.channels = {
        'ch1': ch1.id,
        'ch2': ch2.id,
        'ch3': ch3.id
    }
    
    # Create webhooks
    await manager.create_webhooks(interaction.guild)
    
    # Send confirmation
    channel_names = f"1. {ch1.mention}\n2. {ch2.mention}\n3. {ch3.mention}"
    embed = discord.Embed(
        title="✅ Channels Configured",
        description=f"Webhooks have been created in:\n{channel_names}",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="connect", description="Connect to a WebSocket and start receiving logs")
@app_commands.describe(websocket="The WebSocket URL to connect to (ws:// or wss://)")
async def connect(interaction: discord.Interaction, websocket: str):
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
    
    # Check if webhooks exist
    if not any(manager.webhooks.values()):
        await interaction.response.send_message("Webhooks not found. Please reconfigure channels with `/setchannel`!", ephemeral=True)
        return
    
    await interaction.response.send_message(f"🔄 Connecting to WebSocket: `{websocket}`...", ephemeral=True)
    
    success = await manager.start_logging(websocket)
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
    
    # Check channels
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
    
    # Check webhooks
    webhooks_status = []
    for key, webhook in manager.webhooks.items():
        if webhook:
            webhooks_status.append(f"✅ {key}: Created")
        else:
            webhooks_status.append(f"❌ {key}: Missing")
    
    embed.add_field(name="Webhooks", value="\n".join(webhooks_status), inline=False)
    
    # Connection status
    if manager.is_running:
        embed.add_field(name="Status", value="🟢 **Connected & Logging**", inline=False)
        embed.add_field(name="WebSocket", value=f"`{manager.current_websocket_uri}`", inline=False)
    else:
        embed.add_field(name="Status", value="🔴 **Stopped**", inline=False)
        if manager.current_websocket_uri:
            embed.add_field(name="Last WebSocket", value=f"`{manager.current_websocket_uri}`", inline=False)
    
    await interaction.response.send_message(embed=embed)

if __name__ == "__main__":
    TOKEN = os.getenv('DISCORD_TOKEN')
    if not TOKEN:
        print("Error: DISCORD_TOKEN environment variable not set!")
        print("Please set it using: export DISCORD_TOKEN='your_token_here'")
    else:
        bot.run(TOKEN)
