"""Send a report by email over SMTP (stdlib only).

Settings come from the environment so no address or password lives in the
repo: ``SMTP_USERNAME`` and ``SMTP_PASSWORD`` (for Gmail, the account's
16-character app password), plus optional ``SMTP_HOST`` (default
``smtp.gmail.com``), ``SMTP_PORT`` (default 587, STARTTLS) and ``SMTP_FROM``
(default the username).

The message carries the Markdown as its plain-text part and a small HTML
rendering of it (headings, tables, lists, links, bold/italic, ``<details>``)
so the job table reads as a table in a mail client.
"""

from __future__ import annotations

import html
import os
import re
import smtplib
from email.message import EmailMessage

DEFAULT_HOST = "smtp.gmail.com"
DEFAULT_PORT = 587

_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])_(.+?)_(?![\w*])")
_CODE = re.compile(r"`([^`]+)`")
_SEPARATOR = re.compile(r"^\|(\s*:?-+:?\s*\|)+$")
_KEEP_TAGS = re.compile(r"&lt;(/?(?:details|summary))&gt;")

_TABLE_STYLE = 'style="border-collapse:collapse;font-size:14px"'
_CELL_STYLE = 'style="border:1px solid #ccc;padding:4px 8px;text-align:left"'


class EmailConfigError(RuntimeError):
    """SMTP settings are missing or incomplete."""


def _inline(text: str) -> str:
    out = html.escape(text, quote=False)
    out = _KEEP_TAGS.sub(r"<\1>", out)
    out = _CODE.sub(r"<code>\1</code>", out)
    # The URL is already HTML-escaped with the rest of the line.
    out = _LINK.sub(lambda m: f'<a href="{m.group(2).replace(chr(34), "&quot;")}">{m.group(1)}</a>', out)
    out = _BOLD.sub(r"<b>\1</b>", out)
    return _ITALIC.sub(r"<i>\1</i>", out)


def _cells(row: str) -> list[str]:
    body = row.strip()[1:-1] if row.strip().endswith("|") else row.strip()[1:]
    # "\|" is an escaped pipe inside a cell.
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", body)]


def markdown_to_html(markdown_text: str) -> str:
    """A minimal Markdown -> HTML conversion for the report's own output."""
    lines = markdown_text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if line.startswith("|") and i + 1 < len(lines) and _SEPARATOR.match(lines[i + 1].strip()):
            header = "".join(f"<th {_CELL_STYLE}>{_inline(c)}</th>" for c in _cells(line))
            rows = []
            i += 2
            while i < len(lines) and lines[i].startswith("|"):
                rows.append("<tr>" + "".join(f"<td {_CELL_STYLE}>{_inline(c)}</td>" for c in _cells(lines[i])) + "</tr>")
                i += 1
            out.append(f"<table {_TABLE_STYLE}><tr>{header}</tr>{''.join(rows)}</table>")
            continue
        if line.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(f"<li>{_inline(lines[i][2:])}</li>")
                i += 1
            out.append(f"<ul>{''.join(items)}</ul>")
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif line.startswith(">"):
            out.append(f"<blockquote>{_inline(line.lstrip('> '))}</blockquote>")
        elif line:
            out.append(f"<p>{_inline(line)}</p>")
        i += 1
    return '<html><body style="font-family:sans-serif">' + "\n".join(out) + "</body></html>"


def build_message(to: str, subject: str, markdown_text: str, *, sender: str) -> EmailMessage:
    recipients = [a.strip() for a in re.split(r"[,;]", to or "") if a.strip()]
    if not recipients:
        raise EmailConfigError("no recipient address given")
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(markdown_text)
    msg.add_alternative(markdown_to_html(markdown_text), subtype="html")
    return msg


def send_markdown_email(to: str, subject: str, markdown_text: str, *, env=None) -> None:
    """Send ``markdown_text`` to ``to`` (comma-separated for several)."""
    env = os.environ if env is None else env
    user = env.get("SMTP_USERNAME", "").strip()
    password = env.get("SMTP_PASSWORD", "").strip()
    if not user or not password:
        raise EmailConfigError(
            "email delivery needs SMTP_USERNAME and SMTP_PASSWORD "
            "(for Gmail: the address and an app password) - add them as repository secrets")
    host = env.get("SMTP_HOST", "").strip() or DEFAULT_HOST
    port = int(env.get("SMTP_PORT", "").strip() or DEFAULT_PORT)
    msg = build_message(to, subject, markdown_text, sender=env.get("SMTP_FROM", "").strip() or user)
    with smtplib.SMTP(host, port, timeout=60) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)
