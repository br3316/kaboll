import json
import requests
from datetime import datetime

URL = "https://esf.bolle.schule/oeffentlich/kalender/termine"

events = requests.get(URL, timeout=30).json()

lines = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//BOLLE ESF//Calendar//DE",
    "CALSCALE:GREGORIAN",
]

for event in events:

    uid = f"bolle-esf-{event['id']}@esf.bolle.schule"

    if event.get("allDay", False):

        start = datetime.strptime(
            event["start"],
            "%Y-%m-%d %H:%M:%S"
        ).strftime("%Y%m%d")

        end = datetime.strptime(
            event["end"],
            "%Y-%m-%d %H:%M:%S"
        ).strftime("%Y%m%d")

        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"SUMMARY:{event['title']}",
            f"DTSTART;VALUE=DATE:{start}",
            f"DTEND;VALUE=DATE:{end}",
            "END:VEVENT",
        ])

    else:

        start = datetime.strptime(
            event["start"],
            "%Y-%m-%d %H:%M:%S"
        ).strftime("%Y%m%dT%H%M%S")

        end = datetime.strptime(
            event["end"],
            "%Y-%m-%d %H:%M:%S"
        ).strftime("%Y%m%dT%H%M%S")

        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"SUMMARY:{event['title']}",
            f"DTSTART;TZID=Europe/Berlin:{start}",
            f"DTEND;TZID=Europe/Berlin:{end}",
            "END:VEVENT",
        ])

lines.append("END:VCALENDAR")

with open(
    "esf-bolle-kalender.ics",
    "w",
    encoding="utf-8",
    newline="\r\n"
) as f:
    f.write("\r\n".join(lines))

