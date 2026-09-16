# ESF BOLLE-Kalender

Dieses Repository erzeugt einen öffentlich nutzbaren Termin-Kalender für die Evangelische Schule Frohnau (ESF) aus dem öffentlichen BOLLE-Kalender.

Die aktuelle Kalenderdatei wird als ICS-Datei im Repository-Root gespeichert und ist über GitHub Pages erreichbar:

https://br3316.github.io/kaboll/esf-bolle-kalender.ics

Google Kalender kann den Link über „Weitere Kalender > Per URL“ abonnieren.

## Inhalte

- `create_ics.py`: lädt die BOLLE-Termindaten, validiert sie und erzeugt eine gültige ICS-Datei.
- `.github/workflows/update-calendar.yml`: ruft den Kalender einmal täglich ab und aktualisiert die ICS-Datei.
- `esf-bolle-kalender.ics`: das automatisch erzeugte Kalender-Feed.

## Lokal ausführen

```bash
python -m pip install requests tzdata
python create_ics.py
```

## GitHub Pages

Aktiviere in den Repository-Einstellungen unter „Pages“ die Veröffentlichung aus dem Hauptbranch (Root). Die Datei `esf-bolle-kalender.ics` wird dann direkt über den obigen Pfad ausgeliefert.

## Abonnement in Google Kalender

1. In Google Kalender auf „Weitere Kalender“ klicken.
2. „Per URL“ auswählen.
3. Den Link `https://br3316.github.io/kaboll/esf-bolle-kalender.ics` einfügen.
