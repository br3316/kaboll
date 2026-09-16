from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests


# ============================================================
# Konfiguration
# ============================================================

SOURCE_URL = "https://esf.bolle.schule/oeffentlich/kalender/termine"

OUTPUT_FILE = Path("esf-bolle-kalender.ics")
TEMP_FILE = Path("esf-bolle-kalender.tmp")

CALENDAR_NAME = "Evangelische Schule Frohnau"

CALENDAR_DESCRIPTION = (
    "Öffentliche Termine der Evangelischen Schule Frohnau "
    "aus dem BOLLE-Kalender"
)

SOURCE_DESCRIPTION = (
    "Quelle: Öffentlicher BOLLE-Kalender "
    "der Evangelischen Schule Frohnau"
)

LOCAL_TIMEZONE = ZoneInfo("Europe/Berlin")

REQUEST_TIMEOUT_SECONDS = 30
MINIMUM_EXPECTED_EVENTS = 1

CRLF = "\r\n"


# ============================================================
# Protokollierung
# ============================================================

def log(message: str) -> None:
    """Gibt eine Meldung mit UTC-Zeitstempel aus."""
    timestamp = datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )

    print(f"[{timestamp}] {message}")


# ============================================================
# ICS-Hilfsfunktionen
# ============================================================

def escape_ics_text(value: Any) -> str:
    """
    Maskiert Text entsprechend dem iCalendar-Format.

    Maskiert werden:
    - Backslashes
    - Zeilenumbrüche
    - Semikolons
    - Kommas
    """
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


def fold_ics_line(
    line: str,
    limit: int = 73,
) -> list"""
    Faltet lange ICS-Zeilen.

    Fortsetzungszeilen beginnen gemäß iCalendar-Standard
    mit einem Leerzeichen.
    """
    if len(line.encode("utf-8")) <= limit:
        return [line]

    folded_lines: list[str] = []
    current_line = ""

    for character in line:
        candidate = current_line + character

        if len(candidate.encode("utf-8")) > limit:
            if current_line:
                folded_lines.append(current_line)

            current_line = " " + character
        else:
            current_line = candidate

    if current_line:
        folded_lines.append(current_line)

    return folded_lines


def add_ics_line(
    lines: list[str],
    line: str,
) -> None:
    """Fügt eine ICS-Zeile einschließlich Zeilenfaltung hinzu."""
    lines.extend(fold_ics_line(line))


# ============================================================
# Datumsverarbeitung
# ============================================================

def parse_bolle_datetime(value: str) -> datetime:
    """
    Liest das von BOLLE verwendete Datumsformat ein.

    Erwartetes Format:
    2026-11-19 16:00:00
    """
    if not value:
        raise ValueError("Leerer Datumswert")

    return datetime.strptime(
        value,
        "%Y-%m-%d %H:%M:%S",
    )


def format_all_day_date(value: str) -> str:
    """Formatiert ein Datum für einen ganztägigen ICS-Termin."""
    return parse_bolle_datetime(value).strftime("%Y%m%d")


def format_utc_datetime(value: str) -> str:
    """
    Interpretiert eine von BOLLE gelieferte Uhrzeit als Berliner Ortszeit
    und wandelt sie für die ICS-Datei in UTC um.
    """
    local_datetime = parse_bolle_datetime(value).replace(
        tzinfo=LOCAL_TIMEZONE
    )

    utc_datetime = local_datetime.astimezone(timezone.utc)

    return utc_datetime.strftime("%Y%m%dT%H%M%SZ")


# ============================================================
# Verarbeitung der BOLLE-Termine
# ============================================================

def build_uid(event: dict[str, Any]) -> str:
    """
    Erzeugt eine dauerhaft stabile UID.

    Bevorzugt wird die BOLLE-ID verwendet. Sollte keine ID vorhanden
    sein, wird aus Titel, Beginn und Ende ein stabiler Hash erzeugt.
    """
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

    fallback_hash = hashlib.sha256(
        fallback_value.encode("utf-8")
    ).hexdigest()[:24]

    return f"bolle-esf-{fallback_hash}@esf.bolle.schule"


def get_extended_property(
    event: dict[str, Any],
    property_name: str,
    default: Any = "",
) -> Any:
    """Liest sicher einen Wert aus extendedProps."""
    extended_props = event.get("extendedProps")

    if not isinstance(extended_props, dict):
        return default

    value = extended_props.get(property_name, default)

    if value is None:
        return default

    return value


def build_description(event: dict[str, Any]) -> str:
    """
    Erstellt die Terminbeschreibung aus dem BOLLE-Kommentar
    und einem Quellenhinweis.
    """
    comment = str(
        get_extended_property(
            event,
            "kommentar",
            "",
        )
    ).strip()

    if comment:
        description = (
            f"{comment}\n\n"
            f"{SOURCE_DESCRIPTION}"
        )
    else:
        description = SOURCE_DESCRIPTION

    return escape_ics_text(description)


def validate_event(event: Any) -> bool:
    """Prüft, ob ein Datensatz als Termin verarbeitet werden kann."""
    if not isinstance(event, dict):
        return False

    required_fields = (
        "title",
        "start",
        "end",
    )

    for field in required_fields:
        if not event.get(field):
            return False

    return True


# ============================================================
# Datenabruf
# ============================================================

def download_events() -> list[dict[str, Any]]:
    """Ruft die aktuellen Termine vom öffentlichen BOLLE-Endpunkt ab."""
    log(f"Rufe Termine ab: {SOURCE_URL}")

    response = requests.get(
        SOURCE_URL,
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={
            "Accept": "application/json",
            "User-Agent": "kaboll-calendar-generator/1.0",
        },
    )

    response.raise_for_status()

    data = response.json()

    if not isinstance(data, list):
        raise ValueError(
            "Die Antwort des BOLLE-Endpunkts ist kein JSON-Array."
        )

    if len(data) < MINIMUM_EXPECTED_EVENTS:
        raise ValueError(
            "Die BOLLE-Antwort enthält keine Termine. "
            "Eine vorhandene ICS-Datei wird nicht überschrieben."
        )

    valid_events = [
        event
        for event in data
        if validate_event(event)
    ]

    invalid_count = len(data) - len(valid_events)

    if not valid_events:
        raise ValueError(
            "Keiner der abgerufenen Datensätze kann "
            "als Termin verarbeitet werden."
        )

    log(f"{len(data)} Datensätze abgerufen.")
    log(f"{len(valid_events)} gültige Termine erkannt.")

    if invalid_count:
        log(
            f"Warnung: {invalid_count} ungültige Datensätze "
            "werden übersprungen."
        )

    return valid_events


# ============================================================
# Einzelne Termine erzeugen
# ============================================================

def build_event_lines(
    event: dict[str, Any],
    generation_timestamp: str,
) -> list"""Erzeugt den VEVENT-Block für einen BOLLE-Termin."""
    lines: list[str] = []

    uid = build_uid(event)

    title = escape_ics_text(
        event.get("title", "Termin")
    )

    description = build_description(event)

    location = escape_ics_text(
        get_extended_property(
            event,
            "ort",
            "",
        )
    )

    start_value = str(event["start"])
    end_value = str(event["end"])

    is_all_day = bool(
        event.get("allDay", False)
    )

    add_ics_line(lines, "BEGIN:VEVENT")
    add_ics_line(lines, f"UID:{uid}")
    add_ics_line(lines, f"DTSTAMP:{generation_timestamp}")
    add_ics_line(lines, f"LAST-MODIFIED:{generation_timestamp}")

    if is_all_day:
        start_date = format_all_day_date(start_value)
        end_date = format_all_day_date(end_value)

        add_ics_line(
            lines,
            f"DTSTART;VALUE=DATE:{start_date}",
        )

        add_ics_line(
            lines,
            f"DTEND;VALUE=DATE:{end_date}",
        )

        add_ics_line(
            lines,
            "X-MICROSOFT-CDO-ALLDAYEVENT:TRUE",
        )

    else:
        start_datetime = format_utc_datetime(start_value)
        end_datetime = format_utc_datetime(end_value)

        add_ics_line(
            lines,
            f"DTSTART:{start_datetime}",
        )

        add_ics_line(
            lines,
            f"DTEND:{end_datetime}",
        )

        add_ics_line(
            lines,
            "X-MICROSOFT-CDO-ALLDAYEVENT:FALSE",
        )

    add_ics_line(
        lines,
        f"SUMMARY:{title}",
    )

    add_ics_line(
        lines,
        f"DESCRIPTION:{description}",
    )

    add_ics_line(
        lines,
        f"LOCATION:{location}",
    )

    add_ics_line(
        lines,
        "STATUS:CONFIRMED",
    )

    add_ics_line(
        lines,
        "TRANSP:TRANSPARENT",
    )

    add_ics_line(
        lines,
        "CLASS:PUBLIC",
    )

    add_ics_line(
        lines,
        "SEQUENCE:0",
    )

    add_ics_line(
        lines,
        f"URL:{SOURCE_URL}",
    )

    add_ics_line(
        lines,
        "END:VEVENT",
    )

    return lines


# ============================================================
# Vollständigen Kalender erzeugen
# =======================
