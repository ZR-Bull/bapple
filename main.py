import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
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
CONFIG_PATH = BASE_DIR / "subscriptions.json"
LOG_PATH = BASE_DIR / "discord.log"

SEARCH_PRESETS = {
    "apple": {
        "brand_name": "BUSCH LT APPLE",
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
    "ice": {
        "brand_name": "BUSCH ICE",
        "products": [
            "BUSCH ICE 24/12 OZ CAN",
            "BUSCH ICE 24/16 OZ CAN 4/6",
            "BUSCH ICE 15/25 AL CAN SHRINK",
            "BUSCH ICE 24/16 OZ CAN 6/4",
            "BUSCH ICE 24/12 OZ CAN 2/12",
        ],
    },
    "peach": {
        "brand_name": "BUSCH LT PEACH",
        "products": [
            "BUSCH LIGHT PEACH 30/12 OZ CAN DSTK",
            "BUSCH LIGHT PEACH 15/25 AL CAN SHRINK",
            "BUSCH LIGHT PEACH 1/2 BBL SV",
            "BUSCH LIGHT PEACH 24/12 OZ CAN",
            "BUSCH LIGHT PEACH 24/12 OZ CAN 2/12",
        ],
    },
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
subscriptions = {}


def load_subscriptions():
    if not CONFIG_PATH.exists():
        return {}

    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

    if not isinstance(raw, dict):
        return {}

    return {
        str(user_id): config
        for user_id, config in raw.items()
        if isinstance(config, dict)
    }


def save_subscriptions():
    CONFIG_PATH.write_text(
        json.dumps(subscriptions, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def parse_key_values(text):
    matches = list(re.finditer(r"(\w+)\s*=", text))
    values = {}

    for index, match in enumerate(matches):
        key = match.group(1).lower()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        value = text[start:end].strip().strip('"').strip("'")
        values[key] = value

    return values


def resolve_search_preset(values):
    search_name = (values.get("product") or values.get("search") or values.get("type") or "apple").strip().lower()
    if search_name == "light":
        search_name = "lite"

    preset = SEARCH_PRESETS.get(search_name)

    if not preset:
        allowed = ", ".join(sorted(SEARCH_PRESETS))
        raise ValueError(f"product must be one of: {allowed}")

    return search_name, preset["brand_name"], preset["products"]


def format_products_label(product_label, products):
    return product_label


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


def build_update_embed(config, retailers):
    brand_name = config["brand_name"]
    zip_code = config["zip_code"]
    radius = config["radius"]
    product_label = format_products_label(config["product_label"], config["products"])

    if retailers:
        embed = discord.Embed(
            title=f"Busch stock found for {zip_code}",
            description=f"{len(retailers)} retailer(s) matched your {brand_name} search.",
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
            title=f"No stock found for {zip_code}",
            description=f"The locator did not return any nearby stores for {brand_name} right now.",
            color=discord.Color.red(),
        )

    embed.add_field(name="Zip code", value=zip_code, inline=True)
    embed.add_field(name="Radius", value=f"{radius} mi", inline=True)
    embed.add_field(name="Products", value=product_label, inline=False)
    embed.set_footer(text=f"Checked at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    return embed


def format_config(config):
    product_label = format_products_label(config["product_label"], config["products"])
    next_check = config.get("next_check")
    if next_check:
        next_check_text = next_check
    else:
        next_check_text = "not scheduled"

    channel_id = config.get("channel_id")
    channel_text = f"<#{channel_id}>" if channel_id else "unknown"

    return (
        f"interval={config['interval_minutes']}m, zip={config['zip_code']}, "
        f"radius={config['radius']}mi, products={product_label}, channel={channel_text}, "
        f"next={next_check_text}"
    )


def build_config_from_values(ctx, values):
    interval_text = values.get("interval") or values.get("interval_minutes") or values.get("minutes")
    radius_text = values.get("radius")
    zip_code = values.get("zip") or values.get("zipcode") or values.get("zip_code")

    if not zip_code:
        raise ValueError("Missing zip= or zip_code=")

    interval_minutes = int(interval_text) if interval_text else 60
    if interval_minutes < 1:
        raise ValueError("interval must be at least 1 minute")

    radius = float(radius_text) if radius_text else 25.0
    if radius <= 0:
        raise ValueError("radius must be greater than 0")

    search_name, brand_name, products = resolve_search_preset(values)

    now = datetime.now(timezone.utc)
    return {
        "interval_minutes": interval_minutes,
        "zip_code": str(zip_code),
        "radius": radius,
        "search_name": search_name,
        "brand_name": brand_name,
        "products": products,
        "product_label": search_name,
        "channel_id": ctx.channel.id,
        "guild_id": ctx.guild.id if ctx.guild else None,
        "enabled": True,
        "next_check": now.isoformat(),
    }


async def send_update(target_channel, user, config, retailers):
    embed = build_update_embed(config, retailers)
    await target_channel.send(content=f"{user.mention} here is your Busch stock update.", embed=embed)


async def run_check_for_user(user_id, config):
    user = bot.get_user(int(user_id))
    if user is None:
        try:
            user = await bot.fetch_user(int(user_id))
        except discord.NotFound:
            return

    channel = bot.get_channel(config["channel_id"])
    if channel is None and config.get("channel_id"):
        try:
            channel = await bot.fetch_channel(config["channel_id"])
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            channel = None

    if channel is None:
        try:
            channel = await user.create_dm()
        except discord.Forbidden:
            return

    retailers = await asyncio.to_thread(
        fetch_retailers,
        config["brand_name"],
        config["zip_code"],
        config["radius"],
        config["products"],
    )

    await send_update(channel, user, config, retailers)


@tasks.loop(seconds=60)
async def poll_due_subscriptions():
    now = datetime.now(timezone.utc)
    due_users = []

    for user_id, config in subscriptions.items():
        if not config.get("enabled", True):
            continue

        next_check_text = config.get("next_check")
        if not next_check_text:
            due_users.append((user_id, config))
            continue

        try:
            next_check = datetime.fromisoformat(next_check_text)
        except ValueError:
            due_users.append((user_id, config))
            continue

        if next_check.tzinfo is None:
            next_check = next_check.replace(tzinfo=timezone.utc)

        if next_check <= now:
            due_users.append((user_id, config))

    if not due_users:
        return

    grouped = {}
    for user_id, config in due_users:
        signature = (
            config["zip_code"],
            float(config["radius"]),
            tuple(config["products"]),
        )
        grouped.setdefault(signature, []).append((user_id, config))

    for _, items in grouped.items():
        sample_config = items[0][1]
        try:
            retailers = await asyncio.to_thread(
                fetch_retailers,
                sample_config["brand_name"],
                sample_config["zip_code"],
                sample_config["radius"],
                sample_config["products"],
            )
        except Exception as exc:
            print(f"Stock lookup failed: {exc}")
            continue

        for user_id, config in items:
            user = bot.get_user(int(user_id))
            if user is None:
                try:
                    user = await bot.fetch_user(int(user_id))
                except discord.NotFound:
                    continue

            channel = bot.get_channel(config["channel_id"])
            if channel is None and config.get("channel_id"):
                try:
                    channel = await bot.fetch_channel(config["channel_id"])
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    channel = None

            if channel is None:
                try:
                    channel = await user.create_dm()
                except discord.Forbidden:
                    continue

            await send_update(channel, user, config, retailers)
            config["next_check"] = (
                datetime.now(timezone.utc) + timedelta(minutes=config["interval_minutes"])
            ).isoformat()

    save_subscriptions()


@poll_due_subscriptions.before_loop
async def before_poll_due_subscriptions():
    await bot.wait_until_ready()


@bot.event
async def on_ready():
    global subscriptions

    subscriptions = load_subscriptions()
    if not poll_due_subscriptions.is_running():
        poll_due_subscriptions.start()

    print(f"Ready as {bot.user}.")


@bot.group(name="busch", invoke_without_command=True)
async def busch(ctx):
    await send_help(ctx)


async def send_help(ctx):
    await ctx.reply(
        "Commands:\n"
        "- `!busch watch zip=97333 radius=25 interval=60 product=apple`\n"
        "- `!busch watch zip=97331 radius=25 interval=60 product=lite`\n"
        "- `!busch watch zip=97331 radius=25 interval=60 product=ice`\n"
        "- `!busch watch zip=97331 radius=25 interval=60 product=peach`\n"
        "- `!busch status`\n"
        "- `!busch unwatch`\n"
        "- `!busch products`\n"
        "- `!busch help`"
    )


@busch.command(name="watch")
async def busch_watch(ctx, *, args=""):
    values = parse_key_values(args)

    try:
        config = build_config_from_values(ctx, values)
    except Exception as exc:
        await ctx.reply(
            "Usage: `!busch watch zip=97333 radius=25 interval=60 product=apple`\n"
            "Allowed products: `apple`, `lite`, `ice`, `peach`\n"
            f"Error: {exc}"
        )
        return

    subscriptions[str(ctx.author.id)] = config
    save_subscriptions()

    await ctx.reply(
        "Saved your Busch tracker. I will ping this channel with updates using:\n"
        f"{format_config(config)}"
    )


@busch.command(name="status")
async def busch_status(ctx):
    config = subscriptions.get(str(ctx.author.id))
    if not config:
        await ctx.reply("You do not have a saved tracker yet. Use `!busch watch` first.")
        return

    await ctx.reply(format_config(config))


@busch.command(name="unwatch")
async def busch_unwatch(ctx):
    if str(ctx.author.id) in subscriptions:
        del subscriptions[str(ctx.author.id)]
        save_subscriptions()
        await ctx.reply("Removed your saved tracker.")
        return

    await ctx.reply("You do not have a saved tracker to remove.")


@busch.command(name="products")
async def busch_products(ctx):
    lines = ["Available presets:"]
    for name in sorted(SEARCH_PRESETS):
        lines.append(f"- {name}")
    await ctx.reply("\n".join(lines))


@busch.command(name="help")
async def busch_help(ctx):
    await send_help(ctx)


subscriptions = load_subscriptions()
bot.run(token, log_handler=handler, log_level=logging.INFO)