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

REQUEST_TIMEOUT_SECONDS = 30
MINIMUM_EXPECTED_EVENTS = 1

CRLF = "\r\n"


# ============================================================
# Hilfsfunktionen
# ============================================================

def log(message: str) -> None:
    """Gibt eine Meldung mit UTC-Zeitstempel aus."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{timestamp}] {message}")


def escape_ics_text(value: Any) -> str:
    """
    Maskiert Text entsprechend dem iCalendar-Format.

    Maskiert:
    - Backslash
    - Zeilenumbrüche
    - Semikolon
    - Komma
    """
    if value is None:
        return ""

    text = str(value)

    # Zuerst Backslashes maskieren, damit später eingefügte
    # Maskierungszeichen nicht erneut verändert werden.
    text = text.replace("\\", "\\\\")

    # Unterschiedliche Formen von Zeilenumbrüchen vereinheitlichen.
    text = text.replace("\r\n", "\\n")
    text = text.replace("\r", "\\n")
    text = text.replace("\n", "\\n")

    text = text.replace(";", "\\;")
    text = text.replace(",", "\\,")

    return text.strip()


def fold_ics_line(line: str, limit: int = 73) -> list[str]:
    """
    Faltet lange ICS-Zeilen.

    Fortsetzungszeilen beginnen entsprechend RFC 5545 mit einem Leerzeichen.
    Die Längenbegrenzung wird näherungsweise anhand der UTF-8-Bytes geprüft.
    """
    if len(line.encode(" limit:
        return [line]

    folded_lines: list[str] = []
    current = ""

    for character in line:
        candidate = current + character

        if len(candidate.encode("utf-8")) > limit:
            folded_lines.append(current)

            # ICS-Fortsetzungszeilen beginnen mit einem Leerzeichen.
            current = " " + character
        else:
            current = candidate

    if current:
        folded_lines.append(current)

    return folded_lines


def add_ics_line(lines: list[str], line: str) -> None:
    """Fügt eine ICS-Zeile einschließlich erforderlicher Faltung hinzu."""
    lines.extend(fold_ics_line(line))


def parse_bolle_datetime(value: str) -> datetime:
    """
    Liest das von BOLLE verwendete Datumsformat ein.

    Erwartetes Format:
    2026-11-19 16:00:00
    """
    if not value:
        raise ValueError("Leerer Datumswert")

    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def format_all_day_date(value: str) -> str:
    """Formatiert einen Datumswert für einen ganztägigen ICS-Termin."""
    return parse_bolle_datetime(value).strftime("%Y%m%d")


def format_local_datetime(value: str) -> str:
    """
    Formatiert einen Datumswert als lokale Uhrzeit für Europe/Berlin.

    Es findet bewusst keine UTC-Umrechnung statt, da BOLLE die Daten
    bereits als lokale Berliner Uhrzeiten liefert.
    """
    return parse_bolle_datetime(value).strftime("%Y%m%dT%H%M%S")


def build_uid(event: dict[str, Any]) -> str:
    """
    Erzeugt eine stabile UID.

    Bevorzugt wird die BOLLE-ID verwendet. Falls wider Erwarten keine ID
    vorhanden ist, wird ein stabiler Hash aus Titel, Start und Ende erzeugt.
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

    return default if value is None else value


def build_description(event: dict[str, Any]) -> str:
    """
    Erzeugt die Terminbeschreibung.

    Ein vorhandener BOLLE-Kommentar wird übernommen. Zusätzlich wird
    die Datenquelle angegeben.
    """
    comment = str(
        get_extended_property(event, "kommentar", "")
    ).strip()

    if comment:
        description = f"{comment}\n\n{SOURCE_DESCRIPTION}"
    else:
        description = SOURCE_DESCRIPTION

    return escape_ics_text(description)


def validate_event(event: Any) -> bool:
    """Prüft, ob ein JSON-Element als Termin verarbeitet werden kann."""
    if not isinstance(event, dict):
        return False

    required_fields = ("title", "start", "end")

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

    content_type = response.headers.get("Content-Type", "").lower()

    if "json" not in content_type:
        log(
            "Warnung: Der Server meldet nicht ausdrücklich "
            f"JSON als Inhaltstyp: {content_type or 'unbekannt'}"
        )

    data = response.json()

    if not isinstance(data, list):
        raise ValueError(
            "Die BOLLE-Antwort ist kein JSON-Array."
        )

    if len(data) < MINIMUM_EXPECTED_EVENTS:
        raise ValueError(
            "Die BOLLE-Antwort enthält unerwartet keine Termine. "
            "Die vorhandene ICS-Datei wird nicht überschrieben."
        )

    valid_events = [event for event in data if validate_event(event)]
    invalid_count = len(data) - len(valid_events)

    if not valid_events:
        raise ValueError(
            "Keiner der abgerufenen Datensätze ist als Termin verwendbar."
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
# ICS-Erzeugung
# ============================================================

def build_event_lines(
    event: dict[str, Any],
    generation_timestamp: str,
) -> list"""Erzeugt den VEVENT-Block für einen BOLLE-Termin."""
    lines: list[str] = []

    uid = build_uid(event)
    title = escape_ics_text(event.get("title", "Termin"))
    description = build_description(event)

    location = escape_ics_text(
        get_extended_property(event, "ort", "")
    )

    start_value = str(event["start"])
    end_value = str(event["end"])
    is_all_day = bool(event.get("allDay", False))

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
        start_datetime = format_local_datetime(start_value)
        end_datetime = format_local_datetime(end_value)

        add_ics_line(
            lines,
            f"DTSTART;TZID=Europe/Berlin:{start_datetime}",
        )
        add_ics_line(
            lines,
            f"DTEND;TZID=Europe/Berlin:{end_datetime}",
        )
        add_ics_line(
            lines,
            "X-MICROSOFT-CDO-ALLDAYEVENT:FALSE",
        )

    add_ics_line(lines, f"SUMMARY:{title}")
    add_ics_line(lines, f"DESCRIPTION:{description}")
    add_ics_line(lines, f"LOCATION:{location}")

    add_ics_line(lines, "STATUS:CONFIRMED")
    add_ics_line(lines, "TRANSP:TRANSPARENT")
    add_ics_line(lines, "CLASS:PUBLIC")
    add_ics_line(lines, "SEQUENCE:0")
    add_ics_line(lines, f"URL:{SOURCE_URL}")
    add_ics_line(lines, "END:VEVENT")

    return lines


def build_calendar(events: list[dict[str, Any]]) -> str:
    """Erzeugt den vollständigen iCalendar-Inhalt."""
    lines: list[str] = []

    generation_timestamp = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    add_ics_line(lines, "BEGIN:VCALENDAR")
    add_ics_line(lines, "VERSION:2.0")
    add_ics_line(
        lines,
        "PRODID:-//kaboll//BOLLE ESF Calendar//DE",
    )
    add_ics_line(lines, "CALSCALE:GREGORIAN")
    add_ics_line(lines, "METHOD:PUBLISH")
    add_ics_line(
        lines,
        f"X-WR-CALNAME:{escape_ics_text(CALENDAR_NAME)}",
    )
    add_ics_line(
        lines,
        f"X-WR-CALDESC:{escape_ics_text(CALENDAR_DESCRIPTION)}",
    )
    add_ics_line(lines, "X-WR-TIMEZONE:Europe/Berlin")
    add_ics_line(
        lines,
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
    )
    add_ics_line(lines, "X-PUBLISHED-TTL:PT12H")

    # Sortierung verbessert die Lesbarkeit der erzeugten Datei und
    # verhindert unnötige Änderungen an der Reihenfolge.
    sorted_events = sorted(
        events,
        key=lambda event: (
            str(event.get("start", "")),
            str(event.get("end", "")),
            str(event.get("id", "")),
        ),
    )

    for event in sorted_events:
        try:
            lines.extend(
                build_event_lines(
                    event=event,
                    generation_timestamp=generation_timestamp,
                )
            )
        except (ValueError, TypeError, KeyError) as error:
            log(
                "Warnung: Termin konnte nicht verarbeitet werden. "
                f"ID={event.get('id', 'unbekannt')}, "
                f"Titel={event.get('title', 'unbekannt')}, "
                f"Fehler={error}"
            )

    add_ics_line(lines, "END:VCALENDAR")

    # Eine ICS-Datei sollte mit einem abschließenden CRLF enden.
    return CRLF.join(lines) + CRLF


# ============================================================
# Datei sicher speichern
# ============================================================

def validate_calendar_content(content: str) -> None:
    """Führt einfache Plausibilitätsprüfungen am ICS-Inhalt durch."""
    if not content.startswith("BEGIN:VCALENDAR" + CRLF):
        raise ValueError("Der ICS-Kalenderkopf fehlt.")

    if not content.endswith("END:VCALENDAR" + CRLF):
        raise ValueError("Das Ende des ICS-Kalenders fehlt.")

    event_count = content.count("BEGIN:VEVENT")

    if event_count < MINIMUM_EXPECTED_EVENTS:
        raise ValueError(
            "Die erzeugte ICS-Datei enthält keine Termine."
        )

    if content.count("BEGIN:VEVENT") != content.count("END:VEVENT"):
        raise ValueError(
            "Die Anzahl der BEGIN:VEVENT- und "
            "END:VEVENT-Blöcke stimmt nicht überein."
        )


def write_calendar_safely(content: str) -> bool:
    """
    Schreibt die Kalenderdatei zunächst temporär.

    Rückgabewert:
    - True: Dateiinhalt wurde geändert
    - False: Datei war bereits inhaltlich identisch
    """
    validate_calendar_content(content)

    # utf-8 ohne BOM ist für ICS-Dateien gut geeignet.
    TEMP_FILE.write_bytes(content.encode("utf-8"))

    if OUTPUT_FILE.exists():
        existing_content = OUTPUT_FILE.read_bytes()

        if existing_content == TEMP_FILE.read_bytes():
            TEMP_FILE.unlink(missing_ok=True)
            log("Keine Änderungen an den Terminen festgestellt.")
            return False

    TEMP_FILE.replace(OUTPUT_FILE)

    log(
        f"ICS-Datei erfolgreich geschrieben: "
        f"{OUTPUT_FILE.resolve()}"
    )

    return True


# ============================================================
# Hauptprogramm
# ============================================================

def main() -> int:
    """Führt Abruf, Umwandlung und Speicherung aus."""
    log("Starte Aktualisierung des ESF-BOLLE-Kalenders.")

    try:
        events = download_events()
        calendar_content = build_calendar(events)
        changed = write_calendar_safely(calendar_content)

        event_count = calendar_content.count("BEGIN:VEVENT")

        if changed:
            log(
                f"Kalender wurde mit {event_count} Terminen aktualisiert."
            )
        else:
            log(
                f"Kalender enthält weiterhin {event_count} Termine."
            )

        log("Aktualisierung erfolgreich abgeschlossen.")
        return 0

    except requests.RequestException as error:
        log(f"FEHLER beim Abruf der BOLLE-Daten: {error}")
        return 1

    except ValueError as error:
        log(f"FEHLER bei der Datenverarbeitung: {error}")
        return 1

    except Exception as error:
        log(
            "UNERWARTETER FEHLER: "
            f"{type(error).__name__}: {error}"
        )
        return 1

    finally:
        # Eine möglicherweise zurückgebliebene temporäre Datei entfernen.
        TEMP_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
