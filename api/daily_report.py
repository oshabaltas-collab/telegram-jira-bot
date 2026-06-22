"""
Vercel cron function — sends daily Jira status report to Telegram at 09:00 Europe/Prague.

Vercel triggers this at 07:00 UTC and 08:00 UTC every day.
The function only sends when local Prague time is 09:xx (handles CET/CEST automatically).
"""

import logging
import os
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from flask import Flask, request

from bot.jira_report import JiraReporter
from bot.notion_config import load_report_projects

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

_TZ = ZoneInfo("Europe/Prague")
_JIRA_BASE = "https://fincortex.atlassian.net"
_MAX_PER_SECTION = 20  # cap issues per section to stay under Telegram 4096-char limit


def _send(chat_id: int, thread_id: int, text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "message_thread_id": thread_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=15,
    )


def _fmt_issue(issue: dict) -> str:
    key = issue["key"]
    summary = issue["fields"].get("summary", "—")
    url = f"{_JIRA_BASE}/browse/{key}"
    return f'• <a href="{url}">{key}</a> — {summary}'


def _section(title: str, issues: list[dict]) -> list[str]:
    lines = [title]
    for issue in issues[:_MAX_PER_SECTION]:
        lines.append(_fmt_issue(issue))
    if len(issues) > _MAX_PER_SECTION:
        lines.append(f"  … и ещё {len(issues) - _MAX_PER_SECTION}")
    return lines


def _build_project_block(proj: dict, report: dict) -> str:
    lines = [f"<b>📁 {proj['name']}</b>"]

    if report["overdue"]:
        lines += _section(
            f"🔴 <b>Просрочено ({len(report['overdue'])}):</b>",
            report["overdue"],
        )

    if report["due_today"]:
        lines += _section(
            f"📅 <b>Дедлайн сегодня ({len(report['due_today'])}):</b>",
            report["due_today"],
        )

    if not report["overdue"] and not report["due_today"]:
        lines.append("✅ Просроченных и срочных задач нет")

    lines.append(f"🔄 <b>В работе:</b> {report['in_progress_count']} задач")
    return "\n".join(lines)


@app.route("/api/daily_report", methods=["GET", "POST"])
def daily_report():
    now_prague = datetime.now(tz=_TZ)
    if now_prague.hour != 9:
        logger.info("Skipping: Prague time is %s, expected 09:xx.", now_prague.strftime("%H:%M"))
        return "skip", 200

    chat_id_env = os.environ.get("TELEGRAM_REPORT_CHAT_ID", "")
    thread_id_env = os.environ.get("TELEGRAM_REPORT_THREAD_ID", "")
    if not chat_id_env or not thread_id_env:
        logger.error("TELEGRAM_REPORT_CHAT_ID / TELEGRAM_REPORT_THREAD_ID not configured.")
        return "not configured", 500

    chat_id = int(chat_id_env)
    thread_id = int(thread_id_env)

    try:
        projects = load_report_projects()
    except Exception as exc:
        logger.exception("Notion read failed: %s", exc)
        return "notion error", 500

    if not projects:
        logger.info("No projects with 'Отчёт' enabled in Notion.")
        return "no projects", 200

    try:
        jira = JiraReporter(
            url=os.environ["JIRA_URL"],
            email=os.environ["JIRA_EMAIL"],
            api_token=os.environ["JIRA_API_TOKEN"],
        )
    except KeyError as exc:
        logger.error("Missing env var: %s", exc)
        return "config error", 500

    today_label = date.today().strftime("%d.%m.%Y")
    header = f"📊 <b>Ежедневный отчёт — {today_label}</b>"

    blocks: list[str] = []
    for proj in projects:
        try:
            report = jira.get_project_report(proj["jira_key"])
            blocks.append(_build_project_block(proj, report))
        except Exception as exc:
            logger.exception("Jira query failed for %s: %s", proj["jira_key"], exc)
            blocks.append(f"<b>📁 {proj['name']}</b>\n⚠️ Ошибка запроса к Jira")

    full_message = header + "\n\n" + "\n\n".join(blocks)

    if len(full_message) <= 4000:
        _send(chat_id, thread_id, full_message)
    else:
        # Send header once, then each project block separately
        _send(chat_id, thread_id, header)
        for block in blocks:
            _send(chat_id, thread_id, block)

    logger.info("Daily report sent: %d projects, chat=%s thread=%s", len(projects), chat_id, thread_id)
    return "ok", 200
