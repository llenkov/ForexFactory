# 📈 ForexFactory Discord Bot

Бот, който автоматично публикува икономически събития с **🔴 висока** и **🟠 средна** важност от ForexFactory.

---

## ⚙️ Инсталация

### 1. Изисквания
- Python 3.10+
- Discord акаунт + Bot Token

### 2. Инсталирай зависимостите
```bash
pip install -r requirements.txt
```

### 3. Създай Discord бот
1. Отиди на https://discord.com/developers/applications
2. Кликни **New Application** → дай име
3. В секция **Bot** → **Add Bot** → копирай **Token**
4. В **OAuth2 → URL Generator** отметни:
   - Scopes: `bot`
   - Bot Permissions: `Send Messages`, `Embed Links`, `Read Message History`
5. Отвори генерирания линк и добави бота в сървъра си

### 4. Конфигурирай bot.py
Отвори `bot.py` и смени:
```python
DISCORD_TOKEN = "YOUR_DISCORD_BOT_TOKEN"   # ← Твоят токен
CHANNEL_ID    = 123456789012345678          # ← ID на канала
```

**Как да намериш Channel ID:**
Discord → десен клик на канала → **Copy Channel ID**
(Трябва да имаш активиран Developer Mode: Settings → Advanced → Developer Mode)

### 5. Стартирай
```bash
python bot.py
```

---

## 🤖 Команди

| Команда | Описание |
|---------|----------|
| `!forex` | Показва всички важни събития за днес веднага |
| `!reset` | Изчиства кеша (само за администратори) |

---

## 🔄 Как работи

- Ботът проверява ForexFactory **на всеки 30 минути**
- Публикува само **🔴 High Impact** (червена папка) и **🟠 Medium Impact** (оранжева папка)
- Пази кеш (`sent_events.json`), за да не дублира съобщения
- Показва: дата, час (ET), валута, прогноза, предишна стойност, реална стойност

---

## 📋 Примерен Embed

```
🔴 USD — Non-Farm Payrolls
📅 Дата: Fri May 2        🕐 Час (ET): 8:30am     💱 Валута: USD
📊 Прогноза: 185K         📉 Предишно: 228K
```

---

## ⚠️ Забележки

- ForexFactory не предоставя официално API — ботът използва web scraping
- При промяна на структурата на сайта може да се наложи актуализация на кода
- Часовете са в **Eastern Time (ET)** — ForexFactory стандарт
- За да работи на сървър 24/7, може да използваш **Railway**, **Heroku** или VPS
