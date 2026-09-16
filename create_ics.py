from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


# ============================================================
# Konfiguration
# ============================================================

SOURCE_URL = (
    "https://esf.bolle.schule/oeffentlich/kalender/termine"
)

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

    # Backslashes zuerst maskieren.
    text = text.replace("\\", "\\\\")

    # Zeilenumbrüche vereinheitlichen.
    text = text.replace("\r\n", "\\n")
    text = text.replace("\r", "\\n")
    text = text.replace("\n", "\\n")

    # Sonderzeichen maskieren.
    text = text.replace(";", "\\;")
    text = text.replace(",", "\\,")

    return text.strip()


def fold_ics_line(
    line: str,
    limit: int = 73,
) -> list[str]:
    """
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

            # Fortsetzungszeilen beginnen mit einem Leerzeichen.
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
    """Fügt eine ICS-Zeile mit erforderlicher Zeilenfaltung hinzu."""
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


def format_local_datetime(value: str) -> str:
    """
    Formatiert einen Zeitpunkt als lokale Uhrzeit für Europe/Berlin.

    BOLLE liefert die Uhrzeiten bereits als lokale Berliner Zeit.
    Deshalb erfolgt hier keine Umrechnung nach UTC.
    """
    return parse_bolle_datetime(value).strftime(
        "%Y%m%dT%H%M%S"
    )


# ============================================================
# Verarbeitung der BOLLE-Termine
# ============================================================

def build_uid(event: dict[str, Any]) -> str:
    """
    Erzeugt eine stabile UID für einen Termin.

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

    return (
        f"bolle-esf-{fallback_hash}"
        "@esf.bolle.schule"
    )


def get_extended_property(
    event: dict[str, Any],
    property_name: str,
    default: Any = "",
) -> Any:
    """Liest sicher einen Wert aus extendedProps."""
    extended_props = event.get("extendedProps")

    if not isinstance(extended_props, dict):
        return default

    value = extended_props.get(
        property_name,
        default,
    )

    if value is None:
        return default

    return value


def build_description(
    event: dict[str, Any],
) -> str:
    """
    Erstellt die Terminbeschreibung.

    Ein vorhandener BOLLE-Kommentar wird übernommen.
    Zusätzlich wird ein Quellenhinweis ergänzt.
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


def 
