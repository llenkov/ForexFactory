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

# Опитваме двата URL-а
FF_XML_URLS = [
    "https://www.forexfactory.com/ff_calendar_thisweek.xml",
    "https://nfs.faireconomy.media/ff_calendar_thisweek.xml",  # Алтернативен mirror
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
    """
    Опитва всички FF_XML_URLS и връща (xml_text, url) при успех.
    Връща (None, None) ако всички се провалят.
    """
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        for url in FF_XML_URLS:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    print(f"[DEBUG] GET {url} → {resp.status}")
                    if resp.status == 200:
                        text = await resp.text(encoding="utf-8")
                        print(f"[DEBUG] Получени {len(text)} байта от {url}")
                        return text, url
                    else:
                        print(f"[WARN] {url} върна {resp.status}")
            except Exception as e:
                print(f"[WARN] {url} грешка: {e}")
    return None, None


async def fetch_calendar() -> list[dict]:
    """Изтегля XML и връща важните събития за седмицата."""
    events = []

    xml_text, source_url = await fetch_xml()
    if not xml_text:
        print("[ERROR] Всички XML URL-и се провалиха.")
        return events

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"[ERROR] XML парсване неуспешно: {e}")
        print(f"[DEBUG] Първите 500 символа: {xml_text[:500]}")
        return events

    all_found = 0
    for ev in root.findall("event"):
        all_found += 1
        date_str   = ev.findtext("date",     "").strip()
        impact_raw = ev.findtext("impact",   "").strip().lower()
        title      = ev.findtext("title",    "Unknown Event").strip()
        country    = ev.findtext("country",  "N/A").strip()
        ev_time    = ev.findtext("time",     "").strip()
        forecast   = ev.findtext("forecast", "").strip()
        previous   = ev.findtext("previous", "").strip()
        actual     = ev.findtext("actual",   "").strip()

        if impact_raw == "high":
            impact = "red"
        elif impact_raw == "medium":
            impact = "orange"
        else:
            continue

        try:
            parsed_date = datetime.strptime(date_str, "%b %d, %Y")
        except ValueError:
            print(f"[WARN] Не мога да парсна дата: '{date_str}'")
            parsed_date = datetime.min

        event_id = f"{date_str}_{ev_time}_{country}_{title}"
        events.append({
            "id":          event_id,
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

    print(f"[DEBUG] XML съдържа {all_found} общо събития, {len(events)} важни (high/medium)")
    events.sort(key=lambda e: e["date_parsed"])
    return events


def build_embed(ev: dict) -> discord.Embed:
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


def build_day_header(date_str: str, day_events: list[dict]) -> discord.Embed:
    try:
        parsed   = datetime.strptime(date_str, "%b %d, %Y")
        day_name = parsed.strftime("%A, %d %B %Y")
    except ValueError:
        day_name = date_str

    red_count    = sum(1 for e in day_events if e["impact"] == "red")
    orange_count = sum(1 for e in day_events if e["impact"] == "orange")
    desc = f"🔴 **High Impact:** {red_count} събитие(я)\n🟠 **Medium Impact:** {orange_count} събитие(я)"
    return discord.Embed(title=f"📆 {day_name}", description=desc, color=0x5865F2,
                         timestamp=datetime.now(timezone.utc))


# ─── BOT SETUP ───────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
sent_events: set = set()


@bot.event
async def on_ready():
    global sent_events
    sent_events = load_sent()
    print(f"✅ Влязох като {bot.user}  |  Следя канал {CHANNEL_ID}")
    weekly_calendar.start()


async def post_weekly_events(channel: discord.TextChannel):
    now       = datetime.now(timezone.utc)
    week_start = now.strftime("%d %b")
    print(f"[{now.strftime('%H:%M')} UTC] Публикувам седмичния календар...")

    try:
        events = await fetch_calendar()
    except Exception as e:
        print(f"[ERROR] fetch_calendar: {e}")
        await channel.send(f"❌ Грешка при изтегляне на календара: `{e}`")
        return

    if not events:
        embed = discord.Embed(
            title="📅 Седмичен икономически календар",
            description="✅ Няма важни събития тази седмица.",
            color=0x2B2D31,
            timestamp=now,
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
        color=0x2B2D31,
        timestamp=now,
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
    print(f"  → Публикувани {len(events)} събития за седмицата.")


@tasks.loop(time=WEEKLY_POST_TIME)
async def weekly_calendar():
    if datetime.now(timezone.utc).weekday() != 0:
        return
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        print(f"[ERROR] Не намерих канал с ID {CHANNEL_ID}")
        return
    await post_weekly_events(channel)


# ─── КОМАНДИ ─────────────────────────────────────────────────────────────────────

@bot.command(name="forex")
async def forex_now(ctx):
    """!forex — показва всички важни събития за тази седмица."""
    await ctx.send("⏳ Изтеглям седмичния календар от ForexFactory...")
    await post_weekly_events(ctx.channel)


@bot.command(name="debug")
@commands.has_permissions(administrator=True)
async def debug_fetch(ctx):
    """!debug — тества връзката с ForexFactory XML и показва суровия отговор."""
    await ctx.send("🔍 Тествам връзката с ForexFactory...")
    xml_text, source_url = await fetch_xml()

    if not xml_text:
        await ctx.send("❌ **Всички URL-и се провалиха.** Провери Render логовете за подробности.")
        return

    await ctx.send(f"✅ Успешно изтеглено от: `{source_url}`\n📦 Размер: `{len(xml_text)} байта`")

    # Покажи броя на всички събития в XML-а
    try:
        root  = ET.fromstring(xml_text)
        all_events = root.findall("event")
        impacts    = [ev.findtext("impact", "").strip() for ev in all_events]
        from collections import Counter
        counts = Counter(impacts)
        summary = "\n".join(f"  `{k}`: {v}" for k, v in counts.items())
        await ctx.send(f"📊 **Намерени {len(all_events)} събития в XML:**\n{summary}")

        # Покажи първите 5 реда
        lines = []
        for ev in all_events[:5]:
            lines.append(
                f"• `{ev.findtext('date','')}` | `{ev.findtext('impact','')}` | "
                f"`{ev.findtext('country','')}` | {ev.findtext('title','')}"
            )
        await ctx.send("**Първите 5 събития:**\n" + "\n".join(lines))

    except ET.ParseError as e:
        await ctx.send(f"❌ XML парсване неуспешно: `{e}`\n```{xml_text[:500]}```")


@bot.command(name="reset")
@commands.has_permissions(administrator=True)
async def reset_sent(ctx):
    """!reset — изчиства кеша с изпратените събития."""
    global sent_events
    sent_events = set()
    save_sent(sent_events)
    await ctx.send("✅ Кешът е изчистен.")


# ─── HTTP СЪРВЪР (за Render) ──────────────────────────────────────────────────────
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
    print(f"✅ HTTP сървър стартиран на порт {port}")


# ─── START ────────────────────────────────────────────────────────────────────────
async def main():
    async with bot:
        await start_http_server()
        await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
