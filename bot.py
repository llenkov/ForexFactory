import discord
from discord.ext import commands, tasks
import aiohttp
from datetime import datetime, timezone, time
import asyncio
import xml.etree.ElementTree as ET
import os
import json
from collections import defaultdict, Counter

# ─── КОНФИГУРАЦИЯ ───────────────────────────────────────────────────────────────
DISCORD_TOKEN   = os.getenv("DISCORD_TOKEN")
CHANNEL_ID      = int(os.getenv("CHANNEL_ID"))
WEEKLY_POST_TIME = time(hour=0, minute=1, tzinfo=timezone.utc)
# ─────────────────────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.forexfactory.com/",
}

FF_XML_URLS = [
    "https://nfs.faireconomy.media/ff_calendar_thisweek.xml",
    "https://www.forexfactory.com/ff_calendar_thisweek.xml",
]

SENT_FILE = "sent_events.json"


def load_sent() -> set:
    if os.path.exists(SENT_FILE):
        with open(SENT_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_sent(sent: set):
    with open(SENT_FILE, "w") as f:
        json.dump(list(sent), f)


def impact_color(impact: str) -> int:
    if impact == "red":
        return 0xFF0000
    if impact == "orange":
        return 0xFF8C00
    return 0x808080


def impact_emoji(impact: str) -> str:
    if impact == "red":
        return "🔴"
    if impact == "orange":
        return "🟠"
    return "⚪"


async def fetch_xml() -> tuple[str | None, str | None]:
    """Опитва всички URL-и и връща (xml_text, url) при успех."""
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        for url in FF_XML_URLS:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    print(f"[DEBUG] GET {url} → {resp.status}")
                    if resp.status == 200:
                        text = await resp.text(encoding="utf-8")
                        print(f"[DEBUG] Получени {len(text)} байта")
                        return text, url
                    else:
                        body = await resp.text()
                        print(f"[WARN] {url} → {resp.status}: {body[:100]}")
            except Exception as e:
                print(f"[WARN] {url} грешка: {e}")
    return None, None


async def fetch_calendar() -> list[dict]:
    events = []
    xml_text, _ = await fetch_xml()
    if not xml_text:
        return events

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"[ERROR] XML парсване: {e}")
        return events

    for ev in root.findall("event"):
        impact_raw = ev.findtext("impact", "").strip().lower()
        if impact_raw == "high":
            impact = "red"
        elif impact_raw == "medium":
            impact = "orange"
        else:
            continue

        date_str = ev.findtext("date",     "").strip()
        title    = ev.findtext("title",    "Unknown").strip()
        country  = ev.findtext("country",  "N/A").strip()
        ev_time  = ev.findtext("time",     "").strip()
        forecast = ev.findtext("forecast", "").strip()
        previous = ev.findtext("previous", "").strip()
        actual   = ev.findtext("actual",   "").strip()

        try:
            parsed_date = datetime.strptime(date_str, "%b %d, %Y")
        except ValueError:
            parsed_date = datetime.min

        events.append({
            "id":          f"{date_str}_{ev_time}_{country}_{title}",
            "date":        date_str,
            "date_parsed": parsed_date,
            "time":        ev_time,
            "currency":    country,
            "event":       title,
            "impact":      impact,
            "forecast":    forecast,
            "previous":    previous,
            "actual":      actual,
        })

    events.sort(key=lambda e: e["date_parsed"])
    return events


def build_embed(ev: dict) -> discord.Embed:
    embed = discord.Embed(
        title=f"{impact_emoji(ev['impact'])} {ev['currency']} — {ev['event']}",
        color=impact_color(ev["impact"]),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="📅 Дата",     value=ev["date"]     or "—", inline=True)
    embed.add_field(name="🕐 Час (ET)", value=ev["time"]     or "—", inline=True)
    embed.add_field(name="💱 Валута",   value=ev["currency"] or "—", inline=True)
    if ev["forecast"]:
        embed.add_field(name="📊 Прогноза", value=ev["forecast"], inline=True)
    if ev["previous"]:
        embed.add_field(name="📉 Предишно", value=ev["previous"], inline=True)
    if ev["actual"]:
        embed.add_field(name="✅ Реално",   value=ev["actual"],   inline=True)
    embed.set_footer(text="ForexFactory Economic Calendar • forexfactory.com")
    return embed


def build_day_header(date_str: str, day_events: list[dict]) -> discord.Embed:
    try:
        day_name = datetime.strptime(date_str, "%b %d, %Y").strftime("%A, %d %B %Y")
    except ValueError:
        day_name = date_str
    red    = sum(1 for e in day_events if e["impact"] == "red")
    orange = sum(1 for e in day_events if e["impact"] == "orange")
    return discord.Embed(
        title=f"📆 {day_name}",
        description=f"🔴 **High:** {red}  |  🟠 **Medium:** {orange}",
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc),
    )


# ─── BOT ─────────────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
sent_events: set = set()


@bot.event
async def on_ready():
    global sent_events
    sent_events = load_sent()
    print(f"✅ {bot.user} | Канал {CHANNEL_ID}")
    weekly_calendar.start()


async def post_weekly_events(channel: discord.TextChannel):
    now        = datetime.now(timezone.utc)
    week_start = now.strftime("%d %b")

    try:
        events = await fetch_calendar()
    except Exception as e:
        await channel.send(f"❌ Грешка: `{e}`")
        return

    if not events:
        embed = discord.Embed(
            title="📅 Седмичен икономически календар",
            description="✅ Няма важни събития тази седмица.",
            color=0x2B2D31, timestamp=now,
        )
        embed.set_footer(text="ForexFactory Economic Calendar • forexfactory.com")
        await channel.send(embed=embed)
        return

    by_day: dict[str, list[dict]] = defaultdict(list)
    for ev in events:
        by_day[ev["date"]].append(ev)

    red_total    = sum(1 for e in events if e["impact"] == "red")
    orange_total = sum(1 for e in events if e["impact"] == "orange")

    header = discord.Embed(
        title="📅 Седмичен икономически календар",
        description=(
            f"**Седмица от {week_start}**\n\n"
            f"🔴 **High Impact общо:** {red_total} събитие(я)\n"
            f"🟠 **Medium Impact общо:** {orange_total} събитие(я)\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=0x2B2D31, timestamp=now,
    )
    header.set_footer(text="ForexFactory Economic Calendar • forexfactory.com")
    await channel.send(embed=header)
    await asyncio.sleep(0.5)

    for date_str, day_events in by_day.items():
        await channel.send(embed=build_day_header(date_str, day_events))
        await asyncio.sleep(0.5)
        for ev in day_events:
            await channel.send(embed=build_embed(ev))
            sent_events.add(ev["id"])
            await asyncio.sleep(0.8)

    save_sent(sent_events)
    print(f"→ Публикувани {len(events)} събития.")


@tasks.loop(time=WEEKLY_POST_TIME)
async def weekly_calendar():
    if datetime.now(timezone.utc).weekday() != 0:
        return
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await post_weekly_events(channel)


# ─── КОМАНДИ ─────────────────────────────────────────────────────────────────────

@bot.command(name="forex")
async def forex_now(ctx):
    """Показва важните събития за тази седмица."""
    await ctx.send("⏳ Изтеглям седмичния календар от ForexFactory...")
    await post_weekly_events(ctx.channel)


@bot.command(name="debug")
async def debug_fetch(ctx):
    """Тества връзката с ForexFactory XML."""
    msg = await ctx.send("🔍 Тествам връзката...")

    results = []
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        for url in FF_XML_URLS:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    body = await resp.text()
                    results.append(f"**{resp.status}** `{url}`\n> `{body[:80]}`")
            except Exception as e:
                results.append(f"**ERR** `{url}`\n> `{e}`")

    await msg.edit(content="📡 **Резултати от връзката:**\n\n" + "\n\n".join(results))


@bot.command(name="reset")
@commands.has_permissions(administrator=True)
async def reset_sent(ctx):
    """Изчиства кеша (само администратори)."""
    global sent_events
    sent_events = set()
    save_sent(sent_events)
    await ctx.send("✅ Кешът е изчистен.")


# ─── HTTP СЪРВЪР (Render) ─────────────────────────────────────────────────────────
from aiohttp import web as aio_web

async def health(request):
    return aio_web.Response(text="OK")

async def start_http_server():
    app = aio_web.Application()
    app.router.add_get("/", health)
    runner = aio_web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    await aio_web.TCPSite(runner, "0.0.0.0", port).start()
    print(f"✅ HTTP сървър на порт {port}")


async def main():
    async with bot:
        await start_http_server()
        await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
