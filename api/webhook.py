"""
Vercel serverless function — handles Telegram webhook updates.

Telegram sends a POST request here for every message.
The function is stateless: reads config from Notion on each relevant request.
"""

import hmac
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

# Make the project root importable regardless of how Vercel resolves paths.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from flask import Flask, abort, request

from bot.jira_client import JiraClient
from bot.jira_report import (
    JiraReporter,
    build_full_report,
    build_person_block,
    build_project_block,
    split_message,
)
from bot.notion_config import (
    load_account_names,
    load_projects,
    load_report_projects,
    load_users,
)
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
    "25.12.2025</code>  ← дедлайн необязателен (без него — +5 дней автоматически)\n\n"
    "<b>#задача</b> — статус «К выполнению»\n"
    "<b>#бэклог</b> — статус «Backlog»"
)


def _default_due_date(msg_timestamp: int) -> str:
    """Returns ISO date = message date + 5 days."""
    base = datetime.fromtimestamp(msg_timestamp, tz=timezone.utc) if msg_timestamp else datetime.now(tz=timezone.utc)
    return (base + timedelta(days=5)).strftime("%Y-%m-%d")


def _send(
    chat_id: int,
    text: str,
    reply_to_id: int | None = None,
    thread_id: int | None = None,
) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if thread_id:
        payload["message_thread_id"] = thread_id
    if reply_to_id:
        payload["reply_parameters"] = {"message_id": reply_to_id}
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json=payload,
        timeout=10,
    )


def _react(chat_id: int, message_id: int, emoji: str = "✅") -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    requests.post(
        f"https://api.telegram.org/bot{token}/setMessageReaction",
        json={
            "chat_id": chat_id,
            "message_id": message_id,
            "reaction": [{"type": "emoji", "emoji": emoji}],
        },
        timeout=10,
    )


def _check_secret(req) -> bool:
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    if not secret:
        return True
    incoming = req.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    return hmac.compare_digest(incoming, secret)


def _jira_reporter() -> JiraReporter:
    return JiraReporter(
        url=os.environ["JIRA_URL"],
        email=os.environ["JIRA_EMAIL"],
        api_token=os.environ["JIRA_API_TOKEN"],
    )


def _send_report(chat_id: int, message_id: int, thread_id: int | None,
                 header: str, blocks: list[str]) -> None:
    """Send a report; first message replies to the command, rest stay in-thread."""
    parts = ([header] if header else []) + blocks
    full = "\n\n".join(parts)
    if len(full) <= 4000:
        _send(chat_id, full, reply_to_id=message_id, thread_id=thread_id)
        return
    first = True
    for block in parts:
        for chunk in split_message(block):
            _send(chat_id, chunk, reply_to_id=message_id if first else None, thread_id=thread_id)
            first = False


def _handle_report_all(chat_id: int, message_id: int, thread_id: int | None):
    """#репорт_проекты — full report across all enabled projects."""
    try:
        projects = load_report_projects()
    except Exception as exc:
        logger.exception("Notion read failed: %s", exc)
        _send(chat_id, "❌ Не удалось прочитать конфиг из Notion.", reply_to_id=message_id)
        return "ok", 200

    if not projects:
        _send(chat_id, "⚠️ Нет проектов с включённым отчётом в Notion (поставьте галочку «Отчёт»).", reply_to_id=message_id)
        return "ok", 200

    try:
        names = _safe_names()
        header, blocks = build_full_report(projects, _jira_reporter(), names)
    except Exception as exc:
        logger.exception("Report generation failed: %s", exc)
        _send(chat_id, "❌ Ошибка при формировании отчёта. Проверьте логи Vercel.", reply_to_id=message_id)
        return "ok", 200

    _send_report(chat_id, message_id, thread_id, header, blocks)
    return "ok", 200


def _handle_report_query(arg: str, chat_id: int, message_id: int, thread_id: int | None):
    """#репорт <проект|человек> — single project or single person report."""
    try:
        projects = load_projects()
        users = load_users()
        names = _safe_names()
    except Exception as exc:
        logger.exception("Notion read failed: %s", exc)
        _send(chat_id, "❌ Не удалось прочитать конфиг из Notion.", reply_to_id=message_id)
        return "ok", 200

    project = resolve_project(arg, projects)
    user = resolve_user(arg, users)

    try:
        jira = _jira_reporter()
        if project:
            report = jira.get_project_report(project["jira_key"])
            block = build_project_block(project, report, names)
        elif user:
            report = jira.get_person_report(user["jira_account_id"])
            block = build_person_block(user["name"], report, names)
        else:
            _send(chat_id, f'⚠️ Не нашёл проект или человека «{arg}». '
                           f'Проверьте название (см. справку).', reply_to_id=message_id)
            return "ok", 200
    except Exception as exc:
        logger.exception("Report query failed: %s", exc)
        _send(chat_id, "❌ Ошибка при формировании отчёта. Проверьте логи Vercel.", reply_to_id=message_id)
        return "ok", 200

    _send_report(chat_id, message_id, thread_id, "", [block])
    return "ok", 200


def _safe_names() -> dict:
    try:
        return load_account_names()
    except Exception:
        return {}


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
    message_id: int = message["message_id"]

    # Helper: reply with chat/thread IDs so admin can configure env vars
    if text.strip() == "/threadid":
        tid = message.get("message_thread_id", "—")
        _send(chat_id, f"<b>Chat ID:</b> <code>{chat_id}</code>\n<b>Thread ID:</b> <code>{tid}</code>", reply_to_id=message_id)
        return "ok", 200

    # On-demand reports
    low = text.lower()
    thread_id = message.get("message_thread_id")
    if "#репорт_проекты" in low:
        return _handle_report_all(chat_id, message_id, thread_id)
    if "#репорт" in low:
        # Everything after the #репорт token on its line is the project/person name
        idx = low.find("#репорт")
        rest = text[idx + len("#репорт"):].lstrip(" _").splitlines()[0].strip()
        if rest:
            return _handle_report_query(rest, chat_id, message_id, thread_id)
        _send(chat_id, "Укажите проект или человека: <code>#репорт Максти</code> или <code>#репорт Лиза</code>.\n"
                       "Полный отчёт по всем проектам — <code>#репорт_проекты</code>.", reply_to_id=message_id)
        return "ok", 200

    parsed = parse_message(text)
    if parsed is None:
        return "ok", 200  # No #задача/#бэклог — ignore silently

    # Use explicit deadline or fall back to message date + 5 days
    due_date = parsed.due_date or _default_due_date(message.get("date", 0))
    due_auto = parsed.due_date is None

    # --- Load config from Notion ---
    try:
        project_lookup = load_projects()
        user_lookup = load_users()
    except Exception as exc:
        logger.exception("Notion read failed: %s", exc)
        _send(chat_id, "❌ Не удалось прочитать конфиг из Notion. Проверьте настройки.", reply_to_id=message_id)
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
        _send(chat_id, "\n".join(errors) + "\n\n" + _HELP, reply_to_id=message_id)
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
            due_date=due_date,
            issue_type=project.get("issue_type", "Задание"),
        )
        key = issue["key"]

        # Move to backlog if requested
        if parsed.tag == "бэклог":
            try:
                jira.transition_to_backlog(key)
            except Exception as exc:
                logger.warning("Backlog transition failed for %s: %s", key, exc)

        url = f'{os.environ["JIRA_URL"].rstrip("/")}/browse/{key}'
        d, m, y = due_date[8:], due_date[5:7], due_date[:4]
        due_label = f"{d}.{m}.{y}" + (" (авто +5 дней)" if due_auto else "")
        status_label = "Backlog" if parsed.tag == "бэклог" else "К выполнению"

        _react(chat_id, message_id, "✅")
        _send(
            chat_id,
            f"✅ Задача создана\n"
            f"<b>Проект:</b> {project['name']}\n"
            f"<b>Исполнитель:</b> {user['name']}\n"
            f"<b>Статус:</b> {status_label}\n"
            f"<b>Дедлайн:</b> {due_label}\n"
            f"<a href='{url}'>{key}</a>",
            reply_to_id=message_id,
        )
        logger.info("Created %s → %s / %s (due: %s, auto=%s, tag=%s)", key, project["name"], user["name"], due_date, due_auto, parsed.tag)
    except Exception as exc:
        logger.exception("Jira error: %s", exc)
        _send(chat_id, "❌ Ошибка при создании задачи в Jira. Проверьте логи Vercel.", reply_to_id=message_id)

    return "ok", 200
