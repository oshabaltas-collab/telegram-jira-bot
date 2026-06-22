"""
Vercel serverless function — handles Telegram webhook updates.

Telegram sends a POST request here for every message.
The function is stateless: reads config from Notion on each relevant request.
"""

import hmac
import logging
import os
import sys

# Make the project root importable regardless of how Vercel resolves paths.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from flask import Flask, abort, request

from bot.jira_client import JiraClient
from bot.notion_config import load_projects, load_users
from bot.parser import parse_message, resolve_project, resolve_user

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

_HELP = (
    "Формат сообщения:\n\n"
    "<code>#задача\n"
    "Проект: CRM\n"
    "Ответственный: Оксана\n"
    "Описание задачи\n"
    "25.12.2025</code>  ← дедлайн необязателен"
)


def _send(chat_id: int, text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=10,
    )


def _check_secret(req) -> bool:
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    if not secret:
        return True
    incoming = req.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    return hmac.compare_digest(incoming, secret)


@app.route("/api/webhook", methods=["POST"])
def webhook():
    if not _check_secret(request):
        abort(403)

    update = request.get_json(force=True, silent=True) or {}

    # Support both group messages and channel posts
    message = update.get("message") or update.get("channel_post")
    if not message:
        return "ok", 200

    text = message.get("text", "")
    chat_id: int = message["chat"]["id"]

    parsed = parse_message(text)
    if parsed is None:
        return "ok", 200  # No #задача — ignore silently

    # --- Load config from Notion ---
    try:
        project_lookup = load_projects()
        user_lookup = load_users()
    except Exception as exc:
        logger.exception("Notion read failed: %s", exc)
        _send(chat_id, "❌ Не удалось прочитать конфиг из Notion. Проверьте настройки.")
        return "ok", 200

    project = resolve_project(parsed.raw_project, project_lookup)
    user = resolve_user(parsed.raw_assignee, user_lookup)
    description = parsed.description

    # --- Validate ---
    errors: list[str] = []
    if not parsed.raw_project:
        errors.append("⚠️ Не указан проект (<code>Проект: BAS</code>).")
    elif not project:
        errors.append(f'⚠️ Проект <b>"{parsed.raw_project}"</b> не найден. Проверьте Notion.')

    if not parsed.raw_assignee:
        errors.append("⚠️ Не указан ответственный (<code>Ответственный: Дарина</code>).")
    elif not user:
        errors.append(f'⚠️ Ответственный <b>"{parsed.raw_assignee}"</b> не найден. Проверьте Notion.')

    if not description:
        errors.append("⚠️ Нет описания задачи.")

    if errors:
        _send(chat_id, "\n".join(errors) + "\n\n" + _HELP)
        return "ok", 200

    # --- Create Jira issue ---
    try:
        jira = JiraClient(
            url=os.environ["JIRA_URL"],
            email=os.environ["JIRA_EMAIL"],
            api_token=os.environ["JIRA_API_TOKEN"],
        )
        issue = jira.create_issue(
            project_key=project["jira_key"],
            summary=description,
            assignee_account_id=user["jira_account_id"],
            due_date=parsed.due_date,
        )
        key = issue["key"]
        url = f'{os.environ["JIRA_URL"].rstrip("/")}/browse/{key}'
        deadline_line = ""
        if parsed.due_date:
            d, m, y = parsed.due_date[8:], parsed.due_date[5:7], parsed.due_date[:4]
            deadline_line = f"\n<b>Дедлайн:</b> {d}.{m}.{y}"
        _send(
            chat_id,
            f"✅ Задача создана\n"
            f"<b>Проект:</b> {project['name']}\n"
            f"<b>Исполнитель:</b> {user['name']}"
            f"{deadline_line}\n"
            f"<a href='{url}'>{key}</a>",
        )
        logger.info("Created %s → %s / %s (due: %s)", key, project["name"], user["name"], parsed.due_date)
    except Exception as exc:
        logger.exception("Jira error: %s", exc)
        _send(chat_id, "❌ Ошибка при создании задачи в Jira. Проверьте логи Vercel.")

    return "ok", 200
