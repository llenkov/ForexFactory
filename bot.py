import discord
from discord.ext import commands, tasks
import aiohttp
from bs4 import BeautifulSoup
from datetime import datetime, timezone, time
import asyncio
import re
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
    "Referer": "https://www.forexfactory.com/",
}

FF_URL = "https://www.forexfactory.com/calendar"

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
        return 0xFF0000   # Червено
    if impact == "orange":
        return 0xFF8C00   # Оранжево
    return 0x808080


def impact_emoji(impact: str) -> str:
    if impact == "red":
        return "🔴"
    if impact == "orange":
        return "🟠"
    return "⚪"


# ─── ПОПРАВКА: Филтър по днешна дата ─────────────────────────────────────────────
def _is_today(event_date: str) -> bool:
    """
    Проверява дали събитието е за днес (UTC).
    ForexFactory показва дати като "Wed Apr 22" или "Apr 22".
    Редове без дата (event_date == "") са продължение на текущия ден — приемаме ги.
    """
    if not event_date:
        return True  # Наследена дата от предишен ред — оставяме fetch_calendar да я обработи

    now = datetime.now(timezone.utc)

    # Опитваме различни формати, които ForexFactory използва
    for fmt in ("%a %b %d", "%b %d", "%A %b %d"):
        try:
            parsed = datetime.strptime(event_date.strip(), fmt)
            return parsed.month == now.month and parsed.day == now.day
        except ValueError:
            continue

    # Ако не можем да парснем — включваме събитието (по-добре повече, отколкото нищо)
    print(f"[WARN] Не мога да парсна дата: '{event_date}'")
    return True
# ─────────────────────────────────────────────────────────────────────────────────


async def fetch_calendar() -> list[dict]:
    """Изтегля ForexFactory и връща списък с важни събития."""
    events = []
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        async with session.get(FF_URL, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                print(f"[ERROR] ForexFactory върна статус {resp.status}")
                return events
            html = await resp.text()

    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="calendar__table")
    if not table:
        print("[WARN] Не намерих таблицата с календара.")
        return events

    current_date = ""
    current_time = ""

    for row in table.find_all("tr", class_=re.compile("calendar__row")):
        # Дата
        date_cell = row.find("td", class_="calendar__date")
        if date_cell and date_cell.get_text(strip=True):
            current_date = date_cell.get_text(strip=True)

        # Час
        time_cell = row.find("td", class_="calendar__time")
        if time_cell and time_cell.get_text(strip=True):
            current_time = time_cell.get_text(strip=True)

        # Важност (impact)
        impact_cell = row.find("td", class_="calendar__impact")
        if not impact_cell:
            continue

        impact_span = impact_cell.find("span")
        if not impact_span:
            continue

        impact_class = " ".join(impact_span.get("class", []))
        if "high" in impact_class:
            impact = "red"
        elif "medium" in impact_class:
            impact = "orange"
        else:
            continue   # Пропускаме ниска важност

        # Валута
        currency_cell = row.find("td", class_="calendar__currency")
        currency = currency_cell.get_text(strip=True) if currency_cell else "N/A"

        # Събитие
        event_cell = row.find("td", class_="calendar__event")
        event_name = event_cell.get_text(strip=True) if event_cell else "Unknown Event"

        # Прогноза / Предишно / Реално
        forecast_cell = row.find("td", class_="calendar__forecast")
        previous_cell = row.find("td", class_="calendar__previous")
        actual_cell   = row.find("td", class_="calendar__actual")

        forecast = forecast_cell.get_text(strip=True) if forecast_cell else ""
        previous = previous_cell.get_text(strip=True) if previous_cell else ""
        actual   = actual_cell.get_text(strip=True)   if actual_cell   else ""

        event_id = f"{current_date}_{current_time}_{currency}_{event_name}"

        events.append({
            "id":       event_id,
            "date":     current_date,
            "time":     current_time,
            "currency": currency,
            "event":    event_name,
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
    embed.add_field(name="📅 Дата",    value=ev["date"]     or "—", inline=True)
    embed.add_field(name="🕐 Час (ET)", value=ev["time"]    or "—", inline=True)
    embed.add_field(name="💱 Валута",  value=ev["currency"] or "—", inline=True)

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
    today = datetime.now(timezone.utc).strftime("%A, %d %B %Y")
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M')} UTC] Публикувам дневния календар...")

    try:
        all_events = await fetch_calendar()
    except Exception as e:
        print(f"[ERROR] fetch_calendar: {e}")
        await channel.send(f"❌ Грешка при изтегляне на календара: {e}")
        return

    # ─── ПОПРАВКА: Филтрираме само днешните събития ───────────────────────────
    events = [ev for ev in all_events if _is_today(ev["date"])]
    print(f"  → Общо намерени: {len(all_events)}, за днес: {len(events)}")
    # ──────────────────────────────────────────────────────────────────────────

    red_events    = [ev for ev in events if ev["impact"] == "red"]
    orange_events = [ev for ev in events if ev["impact"] == "orange"]

    # Хедър съобщение
    if not events:
        header = discord.Embed(
            title="📅 Икономически календар",
            description=f"**{today}**\n\n✅ Няма важни събития за днес.",
            color=0x2B2D31,
            timestamp=datetime.now(timezone.utc),
        )
        header.set_footer(text="ForexFactory Economic Calendar • forexfactory.com")
        await channel.send(embed=header)
        return

    header = discord.Embed(
        title="📅 Икономически календар",
        description=(
            f"**{today}**\n\n"
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
