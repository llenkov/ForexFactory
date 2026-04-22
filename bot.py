import discord
from discord.ext import commands, tasks
import aiohttp
from datetime import datetime, timezone, time
import asyncio
import xml.etree.ElementTree as ET
import os
import json
from collections import defaultdict

# ─── КОНФИГУРАЦИЯ ───────────────────────────────────────────────────────────────
DISCORD_TOKEN   = os.getenv("DISCORD_TOKEN")
CHANNEL_ID      = int(os.getenv("CHANNEL_ID"))
# ⚠️ Смени с твоето GitHub username и repo name!
GITHUB_USER    = "llenkov"
GITHUB_REPO    = "https://github.com/llenkov/ForexFactory/tree/main"

# Raw URL към forex_calendar.xml в твоето GitHub репо
# Пример: https://raw.githubusercontent.com/lenkov/forex-bot/main/forex_calendar.xml
CALENDAR_URL   = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/main/forex_calendar.xml"

# Бота публикува всеки понеделник в 00:45 UTC
# (след като GitHub Action е свалила XML в 00:30 UTC)
WEEKLY_POST_TIME = time(hour=0, minute=45, tzinfo=timezone.utc)
# ─────────────────────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Cache-Control": "no-cache",   # Винаги вземаме свежата версия
}

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
    if impact == "red":    return 0xFF0000
    if impact == "orange": return 0xFF8C00
    return 0x808080


def impact_emoji(impact: str) -> str:
    if impact == "red":    return "🔴"
    if impact == "orange": return "🟠"
    return "⚪"


async def fetch_calendar() -> list[dict]:
    """Сваля XML от GitHub и връща важните събития за седмицата."""
    events = []

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        try:
            async with session.get(CALENDAR_URL, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                print(f"[DEBUG] GitHub raw XML → {resp.status}")
                if resp.status != 200:
                    body = await resp.text()
                    print(f"[ERROR] Статус {resp.status}: {body[:100]}")
                    return events
                xml_text = await resp.text(encoding="utf-8")
                print(f"[DEBUG] Получени {len(xml_text)} байта")
        except Exception as e:
            print(f"[ERROR] fetch_calendar: {e}")
            return events

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"[ERROR] XML парсване: {e}\nПърви 300 символа: {xml_text[:300]}")
        return events

    all_count = 0
    for ev in root.findall("event"):
        all_count += 1
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
            print(f"[WARN] Не мога да парсна дата: '{date_str}'")
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

    print(f"[DEBUG] Общо {all_count} събития в XML, {len(events)} важни (high/medium)")
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
    print(f"📅 XML от: {CALENDAR_URL}")
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
    """Тества връзката с GitHub XML."""
    msg = await ctx.send(f"🔍 Тествам `{CALENDAR_URL}`...")
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(CALENDAR_URL, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                body = await resp.text()
                if resp.status == 200:
                    # Парсни и покажи броя
                    try:
                        root   = ET.fromstring(body)
                        all_ev = root.findall("event")
                        high   = sum(1 for e in all_ev if e.findtext("impact","").lower() == "high")
                        medium = sum(1 for e in all_ev if e.findtext("impact","").lower() == "medium")
                        await msg.edit(content=(
                            f"✅ **Връзката работи!**\n"
                            f"📦 Размер: `{len(body)} байта`\n"
                            f"📊 Общо събития: `{len(all_ev)}`\n"
                            f"🔴 High: `{high}`  |  🟠 Medium: `{medium}`"
                        ))
                    except ET.ParseError as e:
                        await msg.edit(content=f"⚠️ Статус 200 но невалиден XML: `{e}`\n```{body[:300]}```")
                else:
                    await msg.edit(content=f"❌ Статус `{resp.status}`\n```{body[:200]}```")
    except Exception as e:
        await msg.edit(content=f"❌ Грешка: `{e}`")


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
