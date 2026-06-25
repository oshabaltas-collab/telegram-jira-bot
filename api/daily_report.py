"""
Vercel cron function — sends daily Jira report at 09:00 Europe/Prague on weekdays.

Vercel triggers this at 07:00 UTC and 08:00 UTC every day.
The function skips silently on weekends and when Prague clock is not 09:xx.
"""

import logging
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from flask import Flask

from bot.jira_report import JiraReporter, build_full_report, split_message
from bot.notion_config import load_account_names, load_report_projects

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
_TZ = ZoneInfo("Europe/Prague")


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


def _send_report_to(chat_id: int, thread_id: int, header: str, blocks: list[str]) -> None:
    full = header + "\n\n" + "\n\n".join(blocks)
    if len(full) <= 4000:
        _send(chat_id, thread_id, full)
        return
    # Too long: send header, then each block (splitting any oversized block by lines)
    _send(chat_id, thread_id, header)
    for block in blocks:
        for chunk in split_message(block):
            _send(chat_id, thread_id, chunk)


def _env_int(name: str) -> int | None:
    val = os.environ.get(name, "").strip()
    return int(val) if val.lstrip("-").isdigit() and int(val) != 0 else None


def _resolve_destination(proj: dict) -> tuple[int, int] | None:
    chat = proj.get("report_chat_id") or _env_int("TELEGRAM_REPORT_CHAT_ID")
    thread = proj.get("report_thread_id") or _env_int("TELEGRAM_REPORT_THREAD_ID")
    return (chat, thread) if chat and thread else None


@app.route("/api/daily_report", methods=["GET", "POST"])
def daily_report():
    now_prague = datetime.now(tz=_TZ)

    # Weekdays only (Mon=0 … Fri=4; Sat=5, Sun=6)
    if now_prague.weekday() >= 5:
        logger.info("Weekend (%s), skipping report.", now_prague.strftime("%A"))
        return "weekend", 200

    # 09:00 Prague time (handles CET/CEST automatically via double cron)
    if now_prague.hour != 9:
        logger.info("Not 09:xx Prague time (%s), skipping.", now_prague.strftime("%H:%M"))
        return "skip", 200

    try:
        projects = load_report_projects()
    except Exception as exc:
        logger.exception("Notion read failed: %s", exc)
        return "notion error", 500

    if not projects:
        logger.info("No projects with 'Отчёт' enabled in Notion.")
        return "no projects", 200

    jira = JiraReporter(
        url=os.environ["JIRA_URL"],
        email=os.environ["JIRA_EMAIL"],
        api_token=os.environ["JIRA_API_TOKEN"],
    )

    try:
        names = load_account_names()
    except Exception:
        names = {}

    header, blocks_all = build_full_report(projects, jira, names)

    # Group projects+blocks by destination
    groups: dict[tuple[int, int], list[str]] = {}
    for proj, block in zip(projects, blocks_all):
        dest = _resolve_destination(proj)
        if dest is None:
            logger.warning("No destination for project %s — skipping.", proj["name"])
            continue
        groups.setdefault(dest, []).append(block)

    if not groups:
        logger.error("No valid destinations configured.")
        return "no destinations", 500

    for (chat_id, thread_id), blocks in groups.items():
        _send_report_to(chat_id, thread_id, header, blocks)
        logger.info("Report sent to chat=%s thread=%s (%d projects)", chat_id, thread_id, len(blocks))

    return "ok", 200
