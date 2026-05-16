import discord
from discord.ext import commands
from discord import app_commands
import websockets
import asyncio
import json
import aiohttp
from typing import Optional, Dict
import config

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

    async def create_webhooks(self, guild: discord.Guild):
        """Create webhooks in all three channels"""
        for key, channel_id in self.channels.items():
            if channel_id:
                channel = guild.get_channel(channel_id)
                if channel:
                    try:
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
            async with websockets.connect(uri) as websocket:
                self.websocket = websocket
                await self.send_to_all(f"✅ Connected to WebSocket: {uri}")
                
                while self.is_running:
                    try:
                        message = await websocket.recv()
                        # Format the log message
                        log_text = f"📝 **Log Received:**\n```\n{message}\n```"
                        await self.send_to_all(log_text)
                    except websockets.exceptions.ConnectionClosed:
                        await self.send_to_all("⚠️ WebSocket connection closed")
                        break
                    except Exception as e:
                        await self.send_to_all(f"❌ Error receiving log: {str(e)}")
                        break
        except Exception as e:
            await self.send_to_all(f"❌ Failed to connect to WebSocket: {str(e)}")

    async def start_logging(self, uri: str):
        """Start the logging process"""
        if self.is_running:
            return False
        
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
        if self.websocket:
            await self.websocket.close()
        await self.send_to_all("🛑 Logging stopped")
        return True

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
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
@app_commands.describe(websocket="The WebSocket URL to connect to")
async def connect(interaction: discord.Interaction, websocket: str):
    if not interaction.guild:
        await interaction.response.send_message("This command must be used in a server!", ephemeral=True)
        return
    
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permissions!", ephemeral=True)
        return
    
    if interaction.guild_id not in guild_data:
        await interaction.response.send_message("Please use /setchannel first to configure channels!", ephemeral=True)
        return
    
    manager = guild_data[interaction.guild_id]
    
    # Check if webhooks exist
    if not any(manager.webhooks.values()):
        await interaction.response.send_message("Webhooks not found. Please reconfigure channels with /setchannel!", ephemeral=True)
        return
    
    await interaction.response.send_message(f"🔄 Connecting to WebSocket: {websocket}...", ephemeral=True)
    
    success = await manager.start_logging(websocket)
    if success:
        await interaction.followup.send(f"✅ Started logging from WebSocket: {websocket}")
    else:
        await interaction.followup.send("❌ Already logging! Use /stop first if you want to restart.")

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
    
    await interaction.response.send_message("🛑 Stopping logging...")
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
        await interaction.response.send_message("No configuration found! Please use /setchannel first.", ephemeral=True)
        return
    
    manager = guild_data[interaction.guild_id]
    
    if not manager.websocket:
        await interaction.response.send_message("No WebSocket connection was established before. Please use /connect first.", ephemeral=True)
        return
    
    if manager.is_running:
        await interaction.response.send_message("Logging is already running!", ephemeral=True)
        return
    
    await interaction.response.send_message("🔄 Restarting logging...")
    # You'll need to store the last WebSocket URI to reconnect
    # For now, this just shows the concept
    await interaction.followup.send("⚠️ Please use /connect again with the WebSocket URI to restart logging.")

if __name__ == "__main__":
    if not config.TOKEN:
        print("Error: DISCORD_TOKEN environment variable not set!")
    else:
        bot.run(config.TOKEN)
