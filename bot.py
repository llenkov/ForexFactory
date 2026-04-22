import discord
from discord.ext import commands, tasks
import aiohttp
from datetime import datetime, timezone, time
import asyncio
import xml.etree.ElementTree as ET
import os
import json

# ─── КОНФИГУРАЦИЯ ───────────────────────────────────────────────────────────────
DISCORD_TOKEN   = os.getenv("DISCORD_TOKEN")
CHANNEL_ID      = int(os.getenv("CHANNEL_ID"))
# Час на публикуване всеки ден (UTC)
DAILY_POST_TIME = time(hour=0, minute=1, tzinfo=timezone.utc)
# ─────────────────────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# ForexFactory официален RSS XML фийд (не се блокира)
FF_XML_URL = "https://www.forexfactory.com/ff_calendar_thisweek.xml"

# Файл, в който пазим вече изпратените събитие (за да не се дублират)
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
    """Връща Discord embed цвят според важността."""
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


async def fetch_calendar() -> list[dict]:
    """
    Изтегля ForexFactory XML фийд и връща само днешните важни събития.
    XML формат: <weeklyevents><event><title>, <country>, <date>, <time>, <impact>, <forecast>, <previous>
    """
    events = []
    today = datetime.now(timezone.utc)

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        async with session.get(FF_XML_URL, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                print(f"[ERROR] ForexFactory XML върна статус {resp.status}")
                return events
            xml_text = await resp.text(encoding="utf-8")

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"[ERROR] XML парсване неуспешно: {e}")
        return events

    for ev in root.findall("event"):
        # Дата — формат "Apr 22, 2026"
        date_str = ev.findtext("date", "").strip()
        try:
            parsed_date = datetime.strptime(date_str, "%b %d, %Y")
            if parsed_date.month != today.month or parsed_date.day != today.day:
                continue  # Не е за днес
        except ValueError:
            print(f"[WARN] Не мога да парсна дата: '{date_str}'")
            continue

        # Важност — "High" / "Medium" / "Low"
        impact_raw = ev.findtext("impact", "").strip().lower()
        if impact_raw == "high":
            impact = "red"
        elif impact_raw == "medium":
            impact = "orange"
        else:
            continue  # Пропускаме ниска важност

        title    = ev.findtext("title",    "Unknown Event").strip()
        country  = ev.findtext("country",  "N/A").strip()
        ev_time  = ev.findtext("time",     "").strip()
        forecast = ev.findtext("forecast", "").strip()
        previous = ev.findtext("previous", "").strip()
        actual   = ev.findtext("actual",   "").strip()

        event_id = f"{date_str}_{ev_time}_{country}_{title}"

        events.append({
            "id":       event_id,
            "date":     date_str,
            "time":     ev_time,
            "currency": country,
            "event":    title,
            "impact":   impact,
            "forecast": forecast,
            "previous": previous,
            "actual":   actual,
        })

    return events


def build_embed(ev: dict) -> discord.Embed:
    """Изгражда Discord Embed за дадено събитие."""
    color = impact_color(ev["impact"])
    emoji = impact_emoji(ev["impact"])

    embed = discord.Embed(
        title=f"{emoji} {ev['currency']} — {ev['event']}",
        color=color,
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


# ─── BOT SETUP ───────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
sent_events: set = set()


@bot.event
async def on_ready():
    global sent_events
    sent_events = load_sent()
    print(f"✅ Влязох като {bot.user}  |  Следя канал {CHANNEL_ID}")
    print(f"📅 Ежедневна публикация в {DAILY_POST_TIME.strftime('%H:%M')} UTC")
    daily_calendar.start()


async def post_daily_events(channel: discord.TextChannel):
    """Изтегля и публикува всички важни събития за деня."""
    today_str = datetime.now(timezone.utc).strftime("%A, %d %B %Y")
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M')} UTC] Публикувам дневния календар...")

    try:
        events = await fetch_calendar()
    except Exception as e:
        print(f"[ERROR] fetch_calendar: {e}")
        await channel.send(f"❌ Грешка при изтегляне на календара: {e}")
        return

    print(f"  → Намерени {len(events)} важни събития за днес.")

    red_events    = [ev for ev in events if ev["impact"] == "red"]
    orange_events = [ev for ev in events if ev["impact"] == "orange"]

    # Хедър съобщение
    if not events:
        header = discord.Embed(
            title="📅 Икономически календар",
            description=f"**{today_str}**\n\n✅ Няма важни събития за днес.",
            color=0x2B2D31,
            timestamp=datetime.now(timezone.utc),
        )
        header.set_footer(text="ForexFactory Economic Calendar • forexfactory.com")
        await channel.send(embed=header)
        return

    header = discord.Embed(
        title="📅 Икономически календар",
        description=(
            f"**{today_str}**\n\n"
            f"🔴 **High Impact:** {len(red_events)} събитие(я)\n"
            f"🟠 **Medium Impact:** {len(orange_events)} събитие(я)"
        ),
        color=0x2B2D31,
        timestamp=datetime.now(timezone.utc),
    )
    header.set_footer(text="ForexFactory Economic Calendar • forexfactory.com")
    await channel.send(embed=header)

    # Публикуване на всяко събитие
    for ev in events:
        embed = build_embed(ev)
        await channel.send(embed=embed)
        sent_events.add(ev["id"])
        await asyncio.sleep(0.8)

    save_sent(sent_events)
    print(f"  → Публикувани {len(events)} събития.")


@tasks.loop(time=DAILY_POST_TIME)
async def daily_calendar():
    """Стартира всеки ден в 00:01 UTC."""
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        print(f"[ERROR] Не намерих канал с ID {CHANNEL_ID}")
        return
    await post_daily_events(channel)


# ─── КОМАНДИ ─────────────────────────────────────────────────────────────────────

@bot.command(name="forex")
async def forex_now(ctx):
    """!forex — показва всички важни събития за днес веднага."""
    await ctx.send("⏳ Изтеглям календара от ForexFactory...")
    await post_daily_events(ctx.channel)


@bot.command(name="reset")
@commands.has_permissions(administrator=True)
async def reset_sent(ctx):
    """!reset — изчиства кеша с изпратените събития (само за администратори)."""
    global sent_events
    sent_events = set()
    save_sent(sent_events)
    await ctx.send("✅ Кешът с изпратените събития е изчистен.")


# ─── МИНИМАЛЕН HTTP СЪРВЪР (за Render Web Service) ───────────────────────────────
from aiohttp import web as aio_web

async def health(request):
    return aio_web.Response(text="OK")

async def start_http_server():
    app = aio_web.Application()
    app.router.add_get("/", health)
    runner = aio_web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = aio_web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"✅ HTTP health-check сървър стартиран на порт {port}")


# ─── START ────────────────────────────────────────────────────────────────────────
async def main():
    async with bot:
        await start_http_server()
        await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
