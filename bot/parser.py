"""
Parses a Telegram message that contains #задача or #бэклог.

Expected format (labels are case-insensitive):

    #задача
    Проект: BAS Digital
    Ответственный: Дарина
    Описание задачи идёт здесь
    и может занимать несколько строк
    25.12.2025

#задача  → создаёт задачу со статусом «К выполнению»
#бэклог  → создаёт задачу и переводит в статус «backlog»

Recognised label variants:
  project   → Проект / Project
  assignee  → Ответственный / Исполнитель / Assignee

Deadline (optional, last line): dd.mm.yyyy or dd/mm/yyyy
"""

import re
from dataclasses import dataclass

_TAG = re.compile(r"#(задача|бэклог)", re.IGNORECASE)
_PROJECT = re.compile(r"^(проект|project)\s*:\s*(.+)$", re.IGNORECASE)
_ASSIGNEE = re.compile(r"^(ответственный|исполнитель|assignee)\s*:\s*(.+)$", re.IGNORECASE)
# Matches dd.mm.yyyy or dd/mm/yyyy as a standalone line
_DATE = re.compile(r"^(\d{2})[./](\d{2})[./](\d{4})$")


@dataclass
class ParsedTask:
    tag: str  # "задача" or "бэклог"
    raw_project: str | None
    raw_assignee: str | None
    description: str | None
    due_date: str | None  # ISO format YYYY-MM-DD, or None


def _parse_date(line: str) -> str | None:
    """Returns YYYY-MM-DD if line is a valid date, else None."""
    m = _DATE.match(line.strip())
    if not m:
        return None
    day, month, year = m.group(1), m.group(2), m.group(3)
    return f"{year}-{month}-{day}"


def parse_message(text: str) -> ParsedTask | None:
    """Returns None when neither #задача nor #бэклог tag is present."""
    tag_match = _TAG.search(text)
    if not tag_match:
        return None

    tag = tag_match.group(1).lower()
    body = _TAG.sub("", text).strip()

    raw_project = None
    raw_assignee = None
    due_date = None
    desc_lines: list[str] = []

    lines = [l.strip() for l in body.splitlines() if l.strip()]

    # Check if the last line is a date
    if lines:
        parsed_date = _parse_date(lines[-1])
        if parsed_date:
            due_date = parsed_date
            lines = lines[:-1]  # Remove date line from processing

    for line in lines:
        m = _PROJECT.match(line)
        if m:
            raw_project = m.group(2).strip()
            continue
        m = _ASSIGNEE.match(line)
        if m:
            raw_assignee = m.group(2).strip()
            continue
        desc_lines.append(line)

    return ParsedTask(
        tag=tag,
        raw_project=raw_project,
        raw_assignee=raw_assignee,
        description="\n".join(desc_lines).strip() or None,
        due_date=due_date,
    )


def resolve_project(raw: str | None, lookup: dict) -> dict | None:
    if not raw:
        return None
    return lookup.get(raw.strip().lower())


def resolve_user(raw: str | None, lookup: dict) -> dict | None:
    if not raw:
        return None
    return lookup.get(raw.strip().lower())
