import requests
import time
import json
import os
import discord
from discord.ext import commands
import logging
from dotenv import load_dotenv
from pathlib import Path


load_dotenv()

webhook_url = os.getenv('WEBHOOK_URL')
token = os.getenv('DISCORD_TOKEN')

URL = "https://api.beertech.com/singularity/graphql"

handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f"We are ready to go in, {bot.user.name} ")

@bot.event
async def on_member_join(member):
    await member.send(f"Welcome to the server")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    
    if "shit" in message.content.lower():
        await message.delete()
    
    await bot.process_commands(message)


bot.run(token, log_handler=handler, log_level=logging.DEBUG)