import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import discord
import requests
from discord.ext import commands, tasks
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

token = os.getenv("DISCORD_TOKEN")
if not token:
    raise RuntimeError("DISCORD_TOKEN is not set in .env")

URL = "https://api.beertech.com/singularity/graphql"
STATE_PATH = BASE_DIR / "busch_state.json"
LOG_PATH = BASE_DIR / "discord.log"
DEFAULT_ZIP = os.getenv("BUSCH_ZIP", "97333")
DEFAULT_RADIUS = float(os.getenv("BUSCH_RADIUS", "25"))
CHECK_TIMES_UTC = [
    time_text.strip()
    for time_text in os.getenv("BUSCH_CHECK_TIMES_UTC", "16:00,20:00,00:00").split(",")
    if time_text.strip()
]

TRACKED_CATEGORIES = {
    "apple": {
        "brand_name": "BUSCH LT APPLE",
        "role_name": "Busch Apple Alerts",
        "emoji": "🍎",
        "products": [
            "BUSCH LIGHT APPLE 30/12 OZ CAN DSTK",
            "BUSCH LIGHT APPLE 24/12 OZ CAN 2/12",
            "BUSCH LIGHT APPLE 15/25 AL CAN SHRINK",
            "BUSCH LIGHT APPLE 24/12 OZ CAN",
            "BUSCH LIGHT APPLE 48/12 AL CAN",
            "BUSCH LIGHT APPLE 24/16 OZ CAN 4/6",
            "BUSCH LIGHT APPLE 1/2 BBL SV",
        ],
    },
    "lite": {
        "brand_name": "BUSCH LIGHT",
        "role_name": "Busch Lite Alerts",
        "emoji": "🍺",
        "products": [
            "BUSCH LIGHT 18/12 OZ NRLN",
            "BUSCH LIGHT 24/12 OZ CAN 2/12",
            "BUSCH LIGHT 15/25 AL CAN SHRINK",
            "BUSCH LIGHT 24/12 OZ CAN 4/6",
            "BUSCH LIGHT 30/12 AL CAN 2/15 SP CF",
            "BUSCH LIGHT 24/16 OZ CAN 4/6",
            "BUSCH LIGHT 18/12 OZ CAN",
            "BUSCH LIGHT 24/12 OZ NRLN 2/12",
            "BUSCH LIGHT 18/16 OZ CAN",
            "BUSCH LIGHT 24/12 OZ NRLN BOX",
            "BUSCH LIGHT 24/12 OZ CAN",
            "BUSCH LIGHT 15/16 OZ CALNR CF",
            "BUSCH LIGHT 30/12 OZ CAN DSTK",
            "BUSCH LIGHT 24/12 OZ NRLN 4/6",
            "BUSCH LIGHT 24/16 OZ CAN",
            "BUSCH LIGHT 36/12 OZ CAN DSTK",
            "BUSCH LIGHT 24/16 OZ CAN 6/4",
            "BUSCH LIGHT 24/16 OZ CALNR",
            "BUSCH LIGHT 15/25 AL CAN 5/3 SHRINK",
            "BUSCH LIGHT 1/2 BBL SV",
            "BUSCH LIGHT 24/16 OZ CAN 3/8",
        ],
    },
    "peach": {
        "brand_name": "BUSCH LT PEACH",
        "role_name": "Busch Peach Alerts",
        "emoji": "🍑",
        "products": [
            "BUSCH LIGHT PEACH 30/12 OZ CAN DSTK",
            "BUSCH LIGHT PEACH 15/25 AL CAN SHRINK",
            "BUSCH LIGHT PEACH 1/2 BBL SV",
            "BUSCH LIGHT PEACH 24/12 OZ CAN",
            "BUSCH LIGHT PEACH 24/12 OZ CAN 2/12",
        ],
    },
}

EMOJI_TO_CATEGORY = {
    config["emoji"]: key for key, config in TRACKED_CATEGORIES.items()
}

HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Origin": "https://www.busch.com",
    "Referer": "https://www.busch.com/",
}

handler = logging.FileHandler(filename=LOG_PATH, encoding="utf-8", mode="w")
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
state = {
    "role_panel_message_id": None,
    "role_panel_channel_id": None,
    "updates_channel_id": None,
    "last_check_key": None,
}


def load_state():
    if not STATE_PATH.exists():
        return

    try:
        loaded_state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return

    if not isinstance(loaded_state, dict):
        return

    for key in state:
        if key in loaded_state:
            state[key] = loaded_state[key]


def save_state():
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def build_query(brand_name, zip_code, radius, products):
    return f"""
query LocateRetailers {{
    locateRetailers(
        brandName: {json.dumps(brand_name)}
        limit: 100
        zipCode: {json.dumps(zip_code)}
        radius: {radius}
        productDescriptions: {json.dumps(products)}
    ) {{
        retailers {{
            vpid
            name
            address
            city
            state
            zipCode
            distance
        }}
    }}
}}
"""


def fetch_retailers(brand_name, zip_code, radius, products):
    payload = {
        "query": build_query(brand_name, zip_code, radius, products),
        "variables": {},
    }

    response = requests.post(URL, json=payload, headers=HEADERS, timeout=20)
    response.raise_for_status()

    data = response.json()
    return data.get("data", {}).get("locateRetailers", {}).get("retailers", [])


async def ensure_role(guild, role_name):
    role = discord.utils.get(guild.roles, name=role_name)
    if role:
        return role

    try:
        return await guild.create_role(name=role_name, mentionable=True, reason="Busch alert role setup")
    except discord.Forbidden:
        return None


def build_update_embed(category_name, config, retailers):
    brand_name = config["brand_name"]

    if retailers:
        embed = discord.Embed(
            title=f"{category_name.title()} update: stock found",
            description=f"{len(retailers)} retailer(s) matched {brand_name}.",
            color=discord.Color.green(),
        )
        top_spots = retailers[:5]
        lines = [
            f"{spot.get('name', 'Unknown')} ({spot.get('distance', '?')} mi)"
            for spot in top_spots
        ]
        embed.add_field(name="Nearby results", value="\n".join(lines), inline=False)
    else:
        embed = discord.Embed(
            title=f"{category_name.title()} update: no stock",
            description=f"The locator did not return nearby stores for {brand_name} right now.",
            color=discord.Color.red(),
        )

    embed.add_field(name="Zip code", value=DEFAULT_ZIP, inline=True)
    embed.add_field(name="Radius", value=f"{DEFAULT_RADIUS} mi", inline=True)
    embed.add_field(name="Brand", value=brand_name, inline=False)
    embed.set_footer(text=f"Checked at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    return embed


def get_updates_channel(guild):
    if state["updates_channel_id"]:
        channel = guild.get_channel(int(state["updates_channel_id"]))
        if isinstance(channel, discord.TextChannel):
            return channel

    if guild.system_channel:
        state["updates_channel_id"] = guild.system_channel.id
        save_state()
        return guild.system_channel

    for channel in guild.text_channels:
        if channel.permissions_for(guild.me).send_messages:
            state["updates_channel_id"] = channel.id
            save_state()
            return channel

    return None


async def run_all_category_checks(guild, channel):
    for category_name, config in TRACKED_CATEGORIES.items():
        try:
            retailers = await asyncio.to_thread(
                fetch_retailers,
                config["brand_name"],
                DEFAULT_ZIP,
                DEFAULT_RADIUS,
                config["products"],
            )
        except Exception as exc:
            await channel.send(f"Stock lookup failed for {category_name}: {exc}")
            continue

        role = discord.utils.get(guild.roles, name=config["role_name"])
        mention = role.mention if role else config["role_name"]
        embed = build_update_embed(category_name, config, retailers)

        await channel.send(
            content=f"{mention} {category_name.title()} stock update",
            embed=embed,
            allowed_mentions=discord.AllowedMentions(roles=True),
        )


async def update_member_role(payload, is_add):
    if payload.guild_id is None:
        return

    if state["role_panel_message_id"] is None:
        return

    if payload.message_id != int(state["role_panel_message_id"]):
        return

    emoji_text = str(payload.emoji)
    category_name = EMOJI_TO_CATEGORY.get(emoji_text)
    if not category_name:
        return

    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return

    role_name = TRACKED_CATEGORIES[category_name]["role_name"]
    role = discord.utils.get(guild.roles, name=role_name)
    if role is None:
        role = await ensure_role(guild, role_name)
        if role is None:
            return

    member = guild.get_member(payload.user_id)
    if member is None:
        try:
            member = await guild.fetch_member(payload.user_id)
        except discord.NotFound:
            return

    if member.bot:
        return

    try:
        if is_add:
            await member.add_roles(role, reason="Busch reaction role opt-in")
        else:
            await member.remove_roles(role, reason="Busch reaction role opt-out")
    except discord.Forbidden:
        pass


@tasks.loop(seconds=60)
async def run_scheduled_checks():
    now = datetime.now(timezone.utc)
    time_key = now.strftime("%H:%M")
    if time_key not in CHECK_TIMES_UTC:
        return

    run_key = now.strftime("%Y-%m-%d %H:%M")
    if state["last_check_key"] == run_key:
        return

    for guild in bot.guilds:
        channel = get_updates_channel(guild)
        if channel is None:
            continue

        await run_all_category_checks(guild, channel)

    state["last_check_key"] = run_key
    save_state()


@run_scheduled_checks.before_loop
async def before_run_scheduled_checks():
    await bot.wait_until_ready()


@bot.event
async def on_ready():
    load_state()
    if not run_scheduled_checks.is_running():
        run_scheduled_checks.start()

    print(f"Ready as {bot.user}.")


@bot.event
async def on_raw_reaction_add(payload):
    await update_member_role(payload, is_add=True)


@bot.event
async def on_raw_reaction_remove(payload):
    await update_member_role(payload, is_add=False)


@bot.group(name="busch", invoke_without_command=True)
async def busch(ctx):
    await send_help(ctx)


async def send_help(ctx):
    await ctx.reply(
        "Commands:\n"
        "- `!busch setup` creates roles + pinned reaction panel in this channel\n"
        "- `!busch channel` sets this channel as update channel\n"
        "- `!busch checknow` runs stock checks right now\n"
        "- `!busch status` shows schedule/channel/panel status\n"
        "- `!busch help`"
    )


@busch.command(name="setup")
@commands.has_permissions(manage_roles=True)
async def busch_setup(ctx):
    if ctx.guild is None:
        await ctx.reply("Run this command in a server channel.")
        return

    role_lines = []
    for category_name, config in TRACKED_CATEGORIES.items():
        role = await ensure_role(ctx.guild, config["role_name"])
        if role is None:
            role_lines.append(f"- {category_name}: could not create/find role")
        else:
            role_lines.append(f"- {config['emoji']} -> {role.mention}")

    embed = discord.Embed(
        title="Busch Alerts Role Panel",
        description=(
            "React to get alert roles. Remove your reaction to remove the role.\n\n"
            + "\n".join(role_lines)
        ),
        color=discord.Color.blurple(),
    )

    panel_message = await ctx.send(embed=embed)
    for config in TRACKED_CATEGORIES.values():
        await panel_message.add_reaction(config["emoji"])

    try:
        await panel_message.pin(reason="Busch alert role selector")
    except discord.Forbidden:
        pass

    state["role_panel_message_id"] = panel_message.id
    state["role_panel_channel_id"] = panel_message.channel.id
    state["updates_channel_id"] = ctx.channel.id
    save_state()

    await ctx.reply("Setup complete. I pinned the role panel and set this as the update channel.")


@busch.command(name="status")
async def busch_status(ctx):
    panel_id = state.get("role_panel_message_id")
    update_channel = state.get("updates_channel_id")
    checks = ", ".join(CHECK_TIMES_UTC)

    await ctx.reply(
        "Busch bot status:\n"
        f"- fixed zip: {DEFAULT_ZIP}\n"
        f"- fixed radius: {DEFAULT_RADIUS}\n"
        f"- check times UTC: {checks}\n"
        f"- update channel id: {update_channel}\n"
        f"- role panel message id: {panel_id}"
    )


@busch.command(name="channel")
@commands.has_permissions(manage_guild=True)
async def busch_channel(ctx):
    if ctx.guild is None:
        await ctx.reply("Run this command in a server channel.")
        return

    state["updates_channel_id"] = ctx.channel.id
    save_state()
    await ctx.reply("This channel is now the scheduled updates channel.")


@busch.command(name="checknow")
@commands.has_permissions(manage_guild=True)
async def busch_checknow(ctx):
    if ctx.guild is None:
        await ctx.reply("Run this command in a server channel.")
        return

    await ctx.reply("Running checks now...")
    await run_all_category_checks(ctx.guild, ctx.channel)
    await ctx.send("Done.")


@busch.command(name="help")
async def busch_help(ctx):
    await send_help(ctx)


@busch_setup.error
@busch_channel.error
@busch_checknow.error
async def busch_permission_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply("You do not have permission for that command.")
        return
    raise error


load_state()
bot.run(token, log_handler=handler, log_level=logging.INFO)