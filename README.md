# Telegram → Jira Bot

Бот слушает сообщения с тегом `#задача` в Telegram-группах и создаёт задачи в Jira.
Конфиг (проекты и пользователи) хранится в Notion — редактируется без кода.
Хостинг — Vercel (бесплатно в рамках Pro-плана команды).

---

## Формат сообщения

```
#задача
Проект: BAS
Ответственный: Дарина
Сделать экран авторизации с валидацией email
```

Метки `Проект:` и `Ответственный:` обязательны.
Всё остальное — описание задачи.

---

## Архитектура

```
Telegram → webhook POST → Vercel Function
                               ↓
                         Notion (конфиг)
                               ↓
                         Jira REST API
                               ↓
                         Telegram ответ
```

Vercel получает сообщение → читает проекты/юзеров из Notion → создаёт задачу в Jira → отвечает в чат.

---

## Шаг 1 — Telegram Bot

1. Написать [@BotFather](https://t.me/BotFather) → `/newbot`
2. Сохранить `TELEGRAM_BOT_TOKEN`
3. Добавить бота в группу
4. Через BotFather: `/mybots` → выбрать бота → **Bot Settings → Group Privacy → Turn off**
   (иначе бот не видит обычные сообщения, только команды)

---

## Шаг 2 — Notion Admin Panel

### Создать интеграцию Notion
1. Открыть https://www.notion.so/my-integrations
2. **New integration** → дать имя (напр. `jira-bot`)
3. Скопировать `Internal Integration Secret` → это `NOTION_TOKEN`

### Создать базу данных **Проекты**

| Колонка | Тип | Пример |
|---------|-----|--------|
| Name | Title | BAS |
| Jira Key | Text | BAS |
| Aliases | Text | BAS, bas, БАС, BAS Digital, bas digital |

Заполнить строки:

| Name | Jira Key | Aliases |
|------|----------|---------|
| BAS | BAS | BAS, bas, БАС, BAS Digital, bas digital |
| Kairos | KAIROS | Kairos, kairos, Кайрос |
| Maxtiana | MAX | Maxtiana, maxtiana, Макстиана |

### Создать базу данных **Пользователи**

| Колонка | Тип | Пример |
|---------|-----|--------|
| Name | Title | Дарина |
| Jira Account ID | Text | 557058:xxxx... |
| Aliases | Text | Дарина, Daria, Дарья |

> **Как найти Jira Account ID:**
> Открыть в браузере (нужна авторизация):
> `https://yourcompany.atlassian.net/rest/api/3/user/search?query=email@company.com`
> Скопировать поле `accountId`.

### Подключить интеграцию к базам
В каждой базе: **⋯ → Connections → добавить вашу интеграцию** (`jira-bot`).

### Получить ID баз данных
Открыть базу в браузере. URL выглядит так:
```
https://www.notion.so/yourworkspace/xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx?v=...
```
32-символьный HEX перед `?v=` — это `DATABASE_ID`.

---

## Шаг 3 — Jira API Token

1. Открыть https://id.atlassian.com/manage-profile/security/api-tokens
2. **Create API token** → скопировать

---

## Шаг 4 — Деплой на Vercel

### Залить код в GitHub

```bash
git init
git add .
git commit -m "telegram jira bot"
git remote add origin https://github.com/your-org/telegram-jira-bot.git
git push -u origin main
```

### Создать проект на Vercel
1. Открыть https://vercel.com/basdigital → **Add New Project**
2. Импортировать репозиторий из GitHub
3. Framework Preset: **Other**
4. Добавить все переменные окружения из `.env.example`
5. **Deploy**

### Получить URL деплоя
После деплоя URL будет вида: `https://telegram-jira-bot.vercel.app`

### Зарегистрировать webhook в Telegram

```bash
curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://telegram-jira-bot.vercel.app/api/webhook&secret_token=<TELEGRAM_WEBHOOK_SECRET>"
```

Ответ должен быть: `{"ok":true,"result":true,...}`

---

## Переменные окружения (Vercel)

| Переменная | Где взять |
|-----------|-----------|
| `TELEGRAM_BOT_TOKEN` | BotFather |
| `TELEGRAM_WEBHOOK_SECRET` | Придумать любую строку |
| `JIRA_URL` | `https://yourcompany.atlassian.net` |
| `JIRA_EMAIL` | Email аккаунта Jira |
| `JIRA_API_TOKEN` | id.atlassian.com |
| `NOTION_TOKEN` | notion.so/my-integrations |
| `NOTION_PROJECTS_DB_ID` | ID базы "Проекты" |
| `NOTION_USERS_DB_ID` | ID базы "Пользователи" |

---

## Добавить новый проект или юзера

Просто открыть Notion и добавить строку в базу — никакого кода или деплоя не нужно.

---

## Структура файлов

```
telegram-jira-bot/
├── api/
│   └── webhook.py         # Vercel serverless function
├── bot/
│   ├── notion_config.py   # Читает конфиг из Notion
│   ├── parser.py          # Парсит текст Telegram-сообщения
│   └── jira_client.py     # Создаёт задачи в Jira
├── vercel.json
├── requirements.txt
└── .env.example
```
