"""Tiny translation table (English source strings, German translation)."""

from __future__ import annotations

import locale
import os

_DE = {
    "Overview": "Übersicht",
    "Performance": "Leistung",
    "DPI": "DPI",
    "Buttons": "Tasten",
    "App": "App",
    "Connection": "Verbindung",
    "USB cable": "USB-Kabel",
    "2.4 GHz receiver": "2,4-GHz-Empfänger",
    "Firmware": "Firmware",
    "Battery": "Akku",
    "charging": "lädt",
    "Polling rate": "Polling-Rate",
    "Debounce time": "Entprellzeit",
    "Lift-off distance": "Lift-off-Distanz",
    "Motion sync": "Motion Sync",
    "Number of stages": "Anzahl Stufen",
    "Active stage": "Aktive Stufe",
    "Stage {n}": "Stufe {n}",
    "Apply": "Übernehmen",
    "Reset": "Zurücksetzen",
    "Refresh": "Aktualisieren",
    "Left button": "Linke Taste",
    "Right button": "Rechte Taste",
    "Middle button (wheel)": "Mittlere Taste (Rad)",
    "Back (side)": "Zurück (Seite)",
    "Forward (side)": "Vor (Seite)",
    "DPI button (bottom)": "DPI-Taste (unten)",
    "left_click": "Linksklick",
    "right_click": "Rechtsklick",
    "middle_click": "Mittelklick",
    "back": "Zurück",
    "forward": "Vor",
    "dpi_cycle": "DPI durchschalten",
    "dpi_up": "DPI +",
    "dpi_down": "DPI −",
    "disabled": "Deaktiviert",
    "unknown": "unbekannt",
    "Searching for the mouse …": "Suche Maus …",
    "No mouse found. Plug in the receiver or the cable.": "Keine Maus gefunden. Empfänger oder Kabel anstecken.",
    "Saved.": "Gespeichert.",
    "Writing is disabled: {why}": "Schreiben gesperrt: {why}",
    "At least one button must stay a left click.": "Mindestens eine Taste muss Linksklick bleiben.",
    "Start in the system tray when I log in": "Beim Anmelden im Systemabschnitt starten",
    "Warn when the battery drops below": "Warnen, wenn der Akku fällt unter",
    "Close to tray": "Beim Schließen im Systemabschnitt weiterlaufen",
    "Open": "Öffnen",
    "Quit": "Beenden",
    "Battery low": "Akku schwach",
    "The mouse battery is at {p} %.": "Der Maus-Akku steht bei {p} %.",
    "Unofficial tool. Settings are stored in the mouse itself.": "Inoffizielles Werkzeug. Die Einstellungen werden in der Maus gespeichert.",
    "Higher is smoother; 1000 Hz is the maximum of this mouse.": "Höher ist flüssiger; 1000 Hz ist das Maximum dieser Maus.",
    "Raise it if a click is sometimes registered twice.": "Erhöhen, falls ein Klick manchmal doppelt ankommt.",
    "Height at which the sensor stops tracking.": "Höhe, ab der der Sensor nicht mehr reagiert.",
    "Syncs sensor reads with USB polling: steadier, about 1 ms more latency.": "Gleicht Sensor und USB-Abfrage ab: gleichmäßiger, ca. 1 ms mehr Verzögerung.",
    "The bottom button cycles through the active stages.": "Die untere Taste schaltet durch die aktiven Stufen.",
    "Mouse": "Maus",
    "the mouse did not answer – move it or click to wake it up": "Die Maus antwortet nicht – kurz bewegen oder klicken, um sie zu wecken.",
    "the mouse is offline – move it or click to wake it up": "Die Maus ist nicht verbunden – kurz bewegen oder klicken.",
    "mouse disconnected": "Maus getrennt.",
    "no supported mouse found": "Keine unterstützte Maus gefunden.",
    "the mouse is already in use by another compx-mouse-tool": "Die Maus wird schon von einem anderen Compx-Mouse-Tool verwendet.",
    "Backups": "Sicherungen",
    "A backup of the settings is saved before the first change of each session in": "Vor der ersten Änderung jeder Sitzung wird eine Sicherung gespeichert unter",
}


_EN = {
    "left_click": "Left click",
    "right_click": "Right click",
    "middle_click": "Middle click",
    "back": "Back",
    "forward": "Forward",
    "dpi_cycle": "Cycle DPI",
    "dpi_up": "DPI +",
    "dpi_down": "DPI −",
    "disabled": "Disabled",
}


def _lang() -> str:
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if value:
            return value[:2]
    return (locale.getlocale()[0] or "en")[:2]


_TABLE = _DE if _lang() == "de" else _EN


def tr(text: str, **kwargs) -> str:
    return _TABLE.get(text, text).format(**kwargs) if kwargs else _TABLE.get(text, text)
