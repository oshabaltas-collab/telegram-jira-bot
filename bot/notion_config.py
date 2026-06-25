"""
Reads project and user config from two Notion databases.

Projects DB columns:
  - Name         (Title)   — canonical project name, e.g. "BAS"
  - Jira Key     (Text)    — Jira project key, e.g. "BAS"
  - Aliases      (Text)    — comma-separated, e.g. "BAS, bas, БАС, BAS Digital"

Users DB columns:
  - Name             (Title)  — canonical user name, e.g. "Дарина"
  - Jira Account ID  (Text)   — Atlassian accountId
  - Aliases          (Text)   — comma-separated, e.g. "Дарина, Daria, Дарья"
"""

import os
import requests

_NOTION_API = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
        "Notion-Version": _NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _query_db(db_id: str) -> list[dict]:
    url = f"{_NOTION_API}/databases/{db_id}/query"
    results = []
    payload: dict = {}
    while True:
        resp = requests.post(url, headers=_headers(), json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        results.extend(data["results"])
        if not data.get("has_more"):
            break
        payload["start_cursor"] = data["next_cursor"]
    return results


def _text(prop: dict) -> str:
    kind = prop.get("type")
    parts = prop.get(kind, [])
    if isinstance(parts, list):
        return "".join(p["text"]["content"] for p in parts if p.get("text"))
    return ""


def load_projects() -> dict[str, dict]:
    """Returns {alias_lower: {name, jira_key}} lookup."""
    pages = _query_db(os.environ["NOTION_PROJECTS_DB_ID"])
    lookup: dict[str, dict] = {}
    for page in pages:
        props = page["properties"]
        name = _text(props.get("Name", {}))
        jira_key = _text(props.get("Jira Key", {}))
        aliases_raw = _text(props.get("Aliases", {}))
        if not name or not jira_key:
            continue
        issue_type = _text(props.get("Issue Type", {})).strip() or "Задание"
        entry = {"name": name, "jira_key": jira_key.strip(), "issue_type": issue_type}
        for alias in aliases_raw.split(","):
            alias = alias.strip()
            if alias:
                lookup[alias.lower()] = entry
    return lookup


def load_report_projects() -> list[dict]:
    """Returns [{name, jira_key, report_chat_id, report_thread_id}]
    for projects with 'Отчёт' checkbox enabled.

    report_chat_id / report_thread_id may be None if not set in Notion;
    the caller falls back to TELEGRAM_REPORT_CHAT_ID / TELEGRAM_REPORT_THREAD_ID env vars.
    """
    pages = _query_db(os.environ["NOTION_PROJECTS_DB_ID"])
    result = []
    for page in pages:
        props = page["properties"]
        name = _text(props.get("Name", {}))
        jira_key = _text(props.get("Jira Key", {}))
        # Match the report checkbox regardless of Отчёт/Отчет (ё vs е) spelling
        include = next(
            (v.get("checkbox", False) for k, v in props.items()
             if v.get("type") == "checkbox" and k.lower().replace("ё", "е").startswith("отчет")),
            False,
        )
        if not (name and jira_key and include):
            continue
        chat_raw = _text(props.get("Report Chat ID", {})).strip()
        thread_raw = _text(props.get("Report Thread ID", {})).strip()
        result.append({
            "name": name,
            "jira_key": jira_key.strip(),
            "report_chat_id": int(chat_raw) if chat_raw.lstrip("-").isdigit() else None,
            "report_thread_id": int(thread_raw) if thread_raw.isdigit() else None,
        })
    return result


def load_users() -> dict[str, dict]:
    """Returns {alias_lower: {name, jira_account_id}} lookup."""
    pages = _query_db(os.environ["NOTION_USERS_DB_ID"])
    lookup: dict[str, dict] = {}
    for page in pages:
        props = page["properties"]
        name = _text(props.get("Name", {}))
        account_id = _text(props.get("Jira Account ID", {}))
        aliases_raw = _text(props.get("Aliases", {}))
        if not name or not account_id:
            continue
        # Strip any "?cloudId=..." query suffix that sneaks in when an accountId
        # is copied from a Jira profile URL — Jira rejects it as an invalid user.
        clean_id = account_id.strip().split("?", 1)[0]
        entry = {"name": name, "jira_account_id": clean_id}
        for alias in aliases_raw.split(","):
            alias = alias.strip()
            if alias:
                lookup[alias.lower()] = entry
    return lookup


def load_account_names() -> dict[str, str]:
    """Returns {jira_account_id: canonical Name} for showing short names in reports."""
    pages = _query_db(os.environ["NOTION_USERS_DB_ID"])
    out: dict[str, str] = {}
    for page in pages:
        props = page["properties"]
        name = _text(props.get("Name", {}))
        account_id = _text(props.get("Jira Account ID", {})).strip().split("?", 1)[0]
        if name and account_id:
            out[account_id] = name
    return out
