from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

SOURCE_URL = "https://esf.bolle.schule/oeffentlich/kalender/termine"
OUTPUT_FILE = Path("esf-bolle-kalender.ics")
CALENDAR_NAME = "Evangelische Schule Frohnau"
CALENDAR_DESCRIPTION = (
    "Öffentliche Termine der Evangelischen Schule Frohnau "
    "aus dem BOLLE-Kalender"
)
SOURCE_DESCRIPTION = (
    "Quelle: Öffentlicher BOLLE-Kalender der Evangelischen Schule Frohnau"
)
LOCAL_TIMEZONE = ZoneInfo("Europe/Berlin")
REQUEST_TIMEOUT_SECONDS = 30
CRLF = "\r\n"


def log(message: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{timestamp}] {message}")


def escape_ics_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\\", "\\\\")
    text = text.replace("\r\n", "\\n")
    text = text.replace("\r", "\\n")
    text = text.replace("\n", "\\n")
    text = text.replace(";", "\\;")
    text = text.replace(",", "\\,")
    return text.strip()


def fold_ics_line(line: str, limit: int = 75) -> list[str]:
    if not line:
        return [""]
    if len(line.encode("utf-8")) <= limit:
        return [line]

    folded: list[str] = []
    current = ""
    current_bytes = 0

    for character in line:
        char_bytes = len(character.encode("utf-8"))
        if current and current_bytes + char_bytes > limit:
            folded.append(current)
            current = " " + character
            current_bytes = len(current.encode("utf-8"))
        else:
            current += character
            current_bytes += char_bytes

    if current:
        folded.append(current)

    return folded


def add_ics_line(lines: list[str], line: str) -> None:
    lines.extend(fold_ics_line(line))


def parse_bolle_datetime(value: str) -> datetime:
    if not value:
        raise ValueError("Leerer Datumswert")
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def format_all_day_date(value: str) -> str:
    return parse_bolle_datetime(value).strftime("%Y%m%d")


def format_utc_datetime(value: str) -> str:
    local_datetime = parse_bolle_datetime(value).replace(tzinfo=LOCAL_TIMEZONE)
    utc_datetime = local_datetime.astimezone(timezone.utc)
    return utc_datetime.strftime("%Y%m%dT%H%M%SZ")


def build_uid(event: dict[str, Any]) -> str:
    event_id = event.get("id")
    if event_id is not None:
        return f"bolle-esf-{event_id}@esf.bolle.schule"

    fallback_value = "|".join(
        [
            str(event.get("title", "")),
            str(event.get("start", "")),
            str(event.get("end", "")),
        ]
    )
    fallback_hash = hashlib.sha256(fallback_value.encode("utf-8")).hexdigest()[:24]
    return f"bolle-esf-{fallback_hash}@esf.bolle.schule"


def get_extended_property(event: dict[str, Any], property_name: str, default: Any = "") -> Any:
    extended_props = event.get("extendedProps")
    if not isinstance(extended_props, dict):
        return default
    value = extended_props.get(property_name, default)
    if value is None:
        return default
    return value


def build_description(event: dict[str, Any]) -> str:
    comment = str(get_extended_property(event, "kommentar", "")).strip()
    description_parts = []
    if comment:
        description_parts.append(comment)
    description_parts.append(SOURCE_DESCRIPTION)
    return escape_ics_text("\n\n".join(description_parts))


def validate_event(event: Any) -> bool:
    if not isinstance(event, dict):
        return False

    for field in ("title", "start", "end"):
        if not event.get(field):
            return False

    try:
        parse_bolle_datetime(str(event["start"]))
        parse_bolle_datetime(str(event["end"]))
    except (TypeError, ValueError):
        return False

    return True


def download_events() -> list[dict[str, Any]]:
    log(f"Rufe Termine ab: {SOURCE_URL}")
    response = requests.get(
        SOURCE_URL,
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={
            "Accept": "application/json",
            "User-Agent": "kaboll-calendar-generator/1.0 (+https://github.com/br3316/kaboll)",
        },
    )
    response.raise_for_status()

    data = response.json()
    if not isinstance(data, list):
        raise ValueError("Die Antwort des BOLLE-Endpunkts ist kein JSON-Array.")
    if not data:
        raise ValueError("Die BOLLE-Antwort enthält keine Termine.")

    valid_events: list[dict[str, Any]] = []
    invalid_count = 0

    for index, event in enumerate(data, start=1):
        if not validate_event(event):
            invalid_count += 1
            title = event.get("title", "<unbekannt>") if isinstance(event, dict) else "<unbekannt>"
            log(f"Überspringe ungültigen Datensatz #{index}: {title}")
            continue
        valid_events.append(event)

    if not valid_events:
        raise ValueError("Keiner der abgerufenen Datensätze konnte verarbeitet werden.")

    log(f"{len(data)} Datensätze abgerufen.")
    log(f"{len(valid_events)} gültige Termine erkannt.")
    if invalid_count:
        log(f"Warnung: {invalid_count} ungültige Datensätze wurden übersprungen.")

    return valid_events


def event_sort_key(event: dict[str, Any]) -> tuple[datetime, datetime, tuple[int, int], str]:
    start = parse_bolle_datetime(str(event["start"]))
    end = parse_bolle_datetime(str(event["end"]))
    event_id = event.get("id")

    if event_id is None:
        return (start, end, (0, 0), "")

    try:
        return (start, end, (1, int(event_id)), "")
    except (TypeError, ValueError):
        return (start, end, (2, 0), str(event_id))


def build_event_lines(event: dict[str, Any], generation_timestamp: str) -> list[str]:
    lines: list[str] = []
    uid = build_uid(event)
    title = escape_ics_text(event.get("title", "Termin"))
    description = build_description(event)
    location = escape_ics_text(get_extended_property(event, "ort", ""))
    start_value = str(event["start"])
    end_value = str(event["end"])
    is_all_day = bool(event.get("allDay", False))

    add_ics_line(lines, "BEGIN:VEVENT")
    add_ics_line(lines, f"UID:{uid}")
    add_ics_line(lines, f"DTSTAMP:{generation_timestamp}")
    add_ics_line(lines, f"LAST-MODIFIED:{generation_timestamp}")

    if is_all_day:
        add_ics_line(lines, f"DTSTART;VALUE=DATE:{format_all_day_date(start_value)}")
        add_ics_line(lines, f"DTEND;VALUE=DATE:{format_all_day_date(end_value)}")
    else:
        add_ics_line(lines, f"DTSTART:{format_utc_datetime(start_value)}")
        add_ics_line(lines, f"DTEND:{format_utc_datetime(end_value)}")

    add_ics_line(lines, f"SUMMARY:{title}")
    add_ics_line(lines, f"DESCRIPTION:{description}")
    add_ics_line(lines, f"LOCATION:{location}")
    add_ics_line(lines, "STATUS:CONFIRMED")
    add_ics_line(lines, "TRANSP:TRANSPARENT")
    add_ics_line(lines, "CLASS:PUBLIC")
    add_ics_line(lines, "SEQUENCE:0")
    add_ics_line(lines, "END:VEVENT")
    return lines


def validate_calendar_content(content: str) -> None:
    if not content.startswith("BEGIN:VCALENDAR"):
        raise ValueError("Der Kalender beginnt nicht mit BEGIN:VCALENDAR.")
    if not content.rstrip("\r\n").endswith("END:VCALENDAR"):
        raise ValueError("Der Kalender endet nicht mit END:VCALENDAR.")
    if "BEGIN:VEVENT" not in content:
        raise ValueError("Der Kalender enthält keinen VEVENT-Eintrag.")
    if content.count("BEGIN:VEVENT") != content.count("END:VEVENT"):
        raise ValueError("Die Anzahl der VEVENT-Blöcke ist ungültig.")
    if content.count("BEGIN:VEVENT") < 1:
        raise ValueError("Der Kalender enthält keinen validen Termin.")


def build_calendar(events: list[dict[str, Any]]) -> str:
    generation_timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sorted_events = sorted(events, key=event_sort_key)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//kaboll//BOLLE ESF Calendar//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{CALENDAR_NAME}",
        f"X-WR-CALDESC:{CALENDAR_DESCRIPTION}",
        "X-WR-TIMEZONE:Europe/Berlin",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
    ]

    for event in sorted_events:
        lines.extend(build_event_lines(event, generation_timestamp))

    lines.append("END:VCALENDAR")
    calendar = CRLF.join(lines) + CRLF
    validate_calendar_content(calendar)
    return calendar


def write_calendar(content: str) -> None:
    temp_path = OUTPUT_FILE.with_name(f"{OUTPUT_FILE.name}.tmp")
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=str(OUTPUT_FILE.parent),
            prefix=f"{OUTPUT_FILE.stem}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            temp_path = Path(handle.name)

        validate_calendar_content(content)
        os.replace(temp_path, OUTPUT_FILE)
    except Exception:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise


def main() -> int:
    try:
        events = download_events()
        calendar = build_calendar(events)
        write_calendar(calendar)
        log(f"ICS-Datei erfolgreich erstellt: {OUTPUT_FILE}")
        return 0
    except Exception as exc:  # pragma: no cover - CLI error path
        log(f"Fehler beim Erzeugen der ICS-Datei: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
