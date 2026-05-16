# Discord Log Bot

A Discord bot that connects to WebSocket servers and forwards logs to three configured Discord channels via webhooks.

## Commands

- `/setchannel #ch1 #ch2 #ch3` - Set three channels for logs and create webhooks
- `/connect <websocket_url>` - Connect to a WebSocket and start receiving logs
- `/stop` - Stop all logging activities
- `/start` - Start logging again (reconnects to previous WebSocket)

## Setup

1. Create a Discord Application and Bot at https://discord.com/developers/applications
2. Enable necessary intents (Message Content Intent)
3. Invite bot with required permissions: Send Messages, Create Webhooks, Manage Webhooks
4. Set environment variable `DISCORD_TOKEN` with your bot token
5. Run the bot

## Deployment on Railway

1. Push this code to GitHub
2. Create new project on Railway
3. Connect your GitHub repo
4. Add `DISCORD_TOKEN` as an environment variable
5. Deploy!

## Required Bot Permissions

- Send Messages
- Create Webhooks
- Manage Webhooks
- Read Messages/View Channels
