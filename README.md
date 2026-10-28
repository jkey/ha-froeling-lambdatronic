# Fröling Lambdatronic für Home Assistant

[English version below](#english)

Home-Assistant-Integration für Fröling-Pelletkessel mit der Regelung **Lambdatronic 3200**
(entwickelt und getestet mit einem **PE1 Pellet**). Sie verbindet sich auf drei Arten:

- **Modbus (lokal)** über ein Modbus-TCP-Gateway an COM2 – schnell und unabhängig vom Internet,
- **Fröling Connect (Cloud)** über das Konto der Fröling-App,
- **beides (Hybrid)** – Modbus für alle Werte, die es kennt; Fröling Connect ergänzt den Rest.

Geräte und Entitäten heißen bei allen drei Arten gleich. Die Integration nutzt die Bibliothek
[froeling-lambdatronic](https://pypi.org/project/froeling-lambdatronic/).

## Unterstützte Geräte

- Fröling-Kessel mit Lambdatronic 3200. Für Modbus: Schnittstelle COM2 (Doku Fröling B1200522,
  „Kommunikationsprotokoll ModBus Lambdatronic 3200“) und ein RS232-zu-Ethernet-Gateway im Modus
  „Modbus TCP Gateway“, z. B. Waveshare RS232/485-TO-ETH. Für Connect: eine in Fröling Connect
  registrierte Anlage.
- Getestet ist bisher nur ein PE1 Pellet; andere Kessel mit Lambdatronic 3200 sollten
  funktionieren, sind aber unerprobt. Rückmeldungen dazu gern als Issue. Pelletlager-Werte
  ergeben nur bei Pelletkesseln Sinn.
- Bis zu 18 Heizkreise, 8 Boiler, 4 Puffer und eine Zirkulationspumpe; erkannt wird, was
  belegt ist.

## Was ist mit welcher Verbindung verfügbar?

| Funktion | Modbus | Connect | Hybrid |
|---|---|---|---|
| Messwerte (Temperaturen, Zustände, Zähler) | ✔ | ✔ (Takt ≥ 1 min) | ✔ (Modbus) |
| Kesselstellgröße, Einschub, Saugzugdrehzahl, Anlagen-Soll errechnet | ✔ | – | ✔ |
| Saugzug-Ansteuerung, Pufferladezustand, Heizkreispumpe | – | ✔ | ✔ (Pufferladezustand, Pumpe) |
| Einstellungen (Heizkurve, Absenkung, Heizgrenzen, Frostschutz, Boiler, Puffer, Zirkulation) | ✔ | ✔ | ✔ (Modbus) |
| Raumsoll in `climate` (nur mit Raumfühler) | ✔ | – | ✔ |
| Anlagen-Betriebsart umschalten, Schalter „Kessel“ | – | ✔ | ✔ |
| Boiler-Betriebsart (Automatik/Extraladen) | – | ✔ | ✔ |
| 2. Pelletbefüllung einstellen | – | ✔ | ✔ |
| Zeitpläne lesen und schreiben (Aktionen) | – | ✔ | ✔ |
| Asche- und Pelletzähler zurücksetzen | – | ✔ | ✔ |
| Störungstext, Zeitpunkt und Verlauf | Text | ✔ | ✔ |
| Systeminformationen (Kesseltyp, Herstellnummer, Versionen) am Kessel-Gerät | – | ✔ | ✔ |

Schreiben geht über Modbus, wenn der Wert dort schreibbar ist, sonst über Fröling Connect.
Was über Connect geschrieben wird, braucht „Fernschalten über connect möglich“. Die erlaubten
Bereiche richten sich nach dem Schreibweg (z. B. Kessel-Soll per Modbus 70–90 °C, mit nur Connect
40–90 °C).

## Voraussetzungen

**Modbus** – am Kessel im Servicemenü (Code −7) → MODBUS-Einstellungen:

| Einstellung | Wert |
|---|---|
| COM 2 als MODBUS Schnittstelle | JA |
| MODBUS Adresse | 2 (Vorgabe; in der Integration als Slave-ID eintragen) |
| MODBUS Protokoll | 1 (RTU) |
| MODBUS-Protokoll 2014 verwenden | **JA** (sonst ist kein Schreiben möglich) |

Gateway: seriell 57600 Baud, 8N1, keine Flusssteuerung, Modus „Modbus TCP ↔ RTU“, Port 502.
Verbindung mit einem **geraden** Kabel (kein Nullmodem) an COM2 der Steuerungsplatine.

**Fröling Connect** – Zugangsdaten der Fröling-App. Zum Ändern von Einstellungen über Connect
muss am Kessel bzw. in der App „Fernschalten über connect möglich“ eingeschaltet sein; sonst
sind die betroffenen Entitäten nicht verfügbar und eine Reparaturmeldung erklärt den Weg.

## Installation

1. HACS → Integrationen → Menü → **Benutzerdefinierte Repositories** →
   `https://github.com/jkey/ha-froeling-lambdatronic`, Kategorie **Integration**.
2. „Fröling Lambdatronic“ installieren und Home Assistant neu starten.
3. Einstellungen → Geräte & Dienste → **Integration hinzufügen** → „Fröling Lambdatronic“.

**Wichtig:** Das Gateway verträgt nur einen Modbus-Client gleichzeitig. Andere Abfragen
(z. B. ein YAML-`modbus:`-Eintrag auf dasselbe Gateway) vor dem Einrichten entfernen.

## Einrichtung

Zuerst die Verbindungsart wählen: „Modbus (lokal)“, „Fröling Connect (Cloud)“ oder „Modbus und
Fröling Connect“.

| Schritt | Feld | Bedeutung |
|---|---|---|
| Modbus | Host | IP-Adresse oder Hostname des Gateways |
| | Erweitert → Port | TCP-Port, meist 502 |
| | Erweitert → Slave-ID | MODBUS Adresse am Kessel, meist 2 |
| Fröling Connect | E-Mail, Passwort | Zugangsdaten der Fröling-App |
| Anlage | Anlage | Nur wenn das Konto mehrere Anlagen hat (Name und Kesseltyp) |
| Unterschiedliche Komponenten | – | Nur bei „beides“, wenn Modbus und Connect nicht dieselben Komponenten melden; eingerichtet wird alles Gefundene |

Mit Modbus liest die Integration vor dem Anlegen den Anlagenzustand und erkennt die Ausstattung,
mit Connect die Komponentenliste der Anlage. Der Eintrag heißt „Fröling {Kesseltyp}“ (mit
Connect) bzw. „Fröling“; umbenennen lässt er sich in Home Assistant. Eine Connect-Anlage kann nur
in einem Eintrag vorkommen.

Komponenten werden nicht ausgewählt: Was nicht in Home Assistant erscheinen soll, über
**Gerät → Deaktivieren** ausblenden. Mit nur Connect fragt die Integration deaktivierte Geräte
nicht mehr ab.

### Optionen

| Feld | Verbindung | Bedeutung |
|---|---|---|
| Werte-Takt | Modbus | Messwerte, 10–300 s (Vorgabe 30 s) |
| Parameter-Takt | Modbus | Einstellungen, 1–60 min (Vorgabe 5 min); nach jeder Änderung zusätzlich sofort |
| Fröling-Connect-Takt | Connect | 1–60 min (Vorgabe 2 min); kurze Takte können die Drosselung von Fröling Connect auslösen |

### Neu konfigurieren

Menü mit „Modbus-Verbindung ändern oder hinzufügen“, „Fröling Connect hinzufügen oder Anmeldung
ändern“ (gleiches Konto ohne Passwort = gespeichertes Passwort behalten) und – nur wenn beide
eingerichtet sind – „Fröling Connect entfernen“ bzw. „Modbus entfernen“. Danach wird die
Ausstattung neu erkannt: Neue Komponenten bekommen ein Gerät, weggefallene verlieren es.

Lehnt Fröling Connect die Anmeldung ab, fragt Home Assistant nach neuen Zugangsdaten. Im
Hybrid-Betrieb läuft Modbus so lange weiter.

### Aktualisierung von 0.1.0

Bestehende Einträge werden beim ersten Start übernommen (Modbus bzw. Modbus und Connect). In den
Optionen abgewählte Komponenten erscheinen als deaktivierte Geräte. Aus „Kesselleistung“ wird
„Kesselstellgröße“ mit derselben Entity-ID. Diese Entitäten entfallen; Automationen, Skripte und
Dashboards, die sie nutzen, bitte anpassen:

| Entfällt | Ersatz |
|---|---|
| `sensor.*_pellet_counter_kg` (Pellet-kg-Zähler) | „Pelletverbrauch-Zähler“ (kg); die Statistik des alten Sensors bleibt verwaist zurück |
| `select.*_hc{n}_operating_mode` (Betriebsart der Heizkreise) | HVAC-Modus und Presets der `climate`-Entität des Heizkreises |
| `number.*_dhw{n}_setpoint` (Solltemperatur der Boiler) | `water_heater`-Entität des Boilers |
| `sensor.*_dhw{n}_temperature` (Temperatur der Boiler) | aktuelle Temperatur (`current_temperature`) der `water_heater`-Entität des Boilers; die Statistik des alten Sensors bleibt verwaist zurück |
| `sensor.*_system_state` (Anlagenzustand) | Auswahl „Anlagen-Betriebsart“; sie zeigt nur die drei wählbaren Betriebsarten (Automatik/Brauchwasser/Dauerlast), andere Zustände erscheinen als unbekannt; der Verlauf des alten Sensors bleibt verwaist zurück |

## Geräte und Entitäten

Das Kessel-Gerät mit Untergeräten für Heizkreise, Boiler, Puffer und Zirkulationspumpe. Die
Untergeräte heißen wie in Fröling Connect (z. B. „Wohnbereich“), ohne Connect nach dem
Fröling-Schema („Heizkreis 01“, „Boiler 01“, „Puffer 01“, „Zirkulationspumpe“); eigene Namen in
Home Assistant bleiben erhalten. Mit Connect trägt das Kessel-Gerät Kesseltyp, Herstellnummer und
Softwarestände.

| Gerät | Entitäten |
|---|---|
| Kessel | Kesseltemperatur, Kessel-Soll errechnet, Abgastemperatur, Restsauerstoff, Saugzugdrehzahl, Saugzug-Ansteuerung, Einschub, Kesselstellgröße, Außentemperatur, Füllstand Pelletbehälter, Pelletverbrauch gesamt und Pelletverbrauch-Zähler (kg), Kesselzustand, Störung (Text; Attribute `error_code`, `more_errors`, mit Connect `since` und `history`), Rücklauftemperatur (deaktiviert) |
| Kessel (binär) | Störung aktiv, Kesselanforderung, Fröling Connect (Verbindung, Diagnose) |
| Kessel (Diagnose) | Betriebsstunden, Stunden im Heizen, Brennerstarts, Heizstunden bis Asche entleeren, Stunden seit Wartung, Stunden in Teillast, Verbunden seit und Letztes Signal (beide deaktiviert), Taste „Parameter neu lesen“ |
| Kessel (Einstellungen) | Kessel-Solltemperatur, Pelletlager Restbestand und Mindestbestand (t), Anlagen-Betriebsart (Automatik/Brauchwasser/Dauerlast), Schalter „Kessel“ und „Automatische Austragung“, Uhrzeiten 1. und 2. Pelletbefüllung, Tasten „Asche-Zähler zurücksetzen“ und „Pellet-Teilzähler zurücksetzen“ |
| Heizkreis | `climate`: Aus/Automatik/Heizen (= Extraheizen) und Presets Automatik, Extraheizen, Absenken, Dauerabsenken, Party; Ist = Raumtemperatur mit Raumfühler, sonst Vorlauf; Raumsoll nur mit Raumfühler; Heizen/Leerlauf aus der Heizkreispumpe (Connect). Dazu Vorlauf Ist, Vorlauf Soll, Vorlauf laut Heizkurve (Attribut `curve`, nicht aufgezeichnet), Raumtemperatur (deaktiviert), Pumpe (binär) |
| Heizkreis (Einstellungen) | Heizkurve Vorlauf bei plus/minus 10 °C, Absenkung, Heizgrenze Heiz- und Absenkbetrieb, Frostschutz, Pumpe aus unter Vorlauf-Soll, Überhitzungsschutz ab Puffer oben |
| Boiler | `water_heater`: Temperatur oben, Solltemperatur, mit Connect Betriebsart Automatik/Extraladen; Pumpe; Einstellung „Nachladen unter“ |
| Puffer | Temperatur oben und unten, Pumpe, Ladezustand, Zündung unter Puffertemperatur (Diagnose, deaktiviert), Einstellung „Heizkreisfreigabe ab“ |
| Zirkulationspumpe | Pumpe (%), Rücklauftemperatur (mit Modbus nur mit Fühler; mit nur Connect deaktiviert, weil die Cloud ohne Fühler 0 meldet), Strömungsschalter (binär), Einstellung „Pumpe aus über Rücklauftemperatur“ |

## Aktionen

**`froeling_lambdatronic.get_schedule`** liest die Einschaltphasen eines Heizkreises (`climate`)
oder Boilers (`water_heater`) und liefert sie als Antwort, z. B.
`{"monday": [{"from": "04:30", "to": "22:00"}], …, "unreadable_days": []}`. Tage, deren Phasen
Fröling Connect nicht lesbar liefert, fehlen in der Antwort und stehen in `unreadable_days`
(z. B. `["tuesday"]`); wer den Zeitplan liest, ändert und zurückschreibt, muss sie mitgeben.

**`froeling_lambdatronic.set_schedule`** schreibt die Phasen einzelner Tage (`monday` …
`sunday`), mindestens eines Tages. Je Tag bis zu vier Phasen `{from, to}`; `to` darf `24:00`
sein (nur in YAML). Nicht angegebene Tage bleiben, eine leere Liste leert den Tag. Da Fröling
Connect immer die ganze Woche schreibt, verweigert die Aktion das Schreiben, solange ein nicht
angegebener Tag unlesbare Phasen hat (er würde sonst gelöscht); die Fehlermeldung nennt die
Tage. Beide Aktionen brauchen Fröling Connect.

```yaml
# Am Wochenende länger heizen
triggers:
  - trigger: time
    at: "18:00:00"
conditions:
  - condition: time
    weekday: fri
actions:
  - action: froeling_lambdatronic.set_schedule
    target:
      entity_id: climate.froling_pe1_pellet_20_wohnbereich
    data:
      saturday:
        - from: "06:00"
          to: "23:00"
      sunday:
        - from: "06:00"
          to: "23:00"
```

## Aktualisierung der Daten

- Modbus: Messwerte alle 30 s, Einstellungen alle 5 min und sofort nach jeder Änderung (beides
  einstellbar), nacheinander über eine dauerhafte Verbindung.
- Fröling Connect: alle 2 min (einstellbar) – im Hybrid-Betrieb nur die Übersicht (eine Anfrage),
  sonst zusätzlich alle Komponenten. Systeminformationen und Verbindungsstatus stündlich. Der
  Kessel meldet sich etwa minütlich bei Connect.
- Kein Umschalten: Fällt im Hybrid-Betrieb Modbus aus, werden die Modbus-Entitäten „nicht
  verfügbar“, die Connect-Entitäten bleiben – und umgekehrt. Ausfall und Rückkehr stehen je
  einmal im Log. Drosselt Fröling Connect (HTTP 429), wartet die Integration die verlangte Zeit.

## Anwendungsfälle und Beispiele

Störung als Push-Nachricht:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.froling_pe1_pellet_20_storung_aktiv
    to: "on"
actions:
  - action: notify.mobile_app_handy
    data:
      title: Kessel-Störung
      message: "{{ states('sensor.froling_pe1_pellet_20_storung') }}"
```

Im Sommer auf Brauchwasser umschalten (braucht Fröling Connect):

```yaml
triggers:
  - trigger: numeric_state
    entity_id: sensor.froling_pe1_pellet_20_aussentemperatur
    above: 18
    for: { hours: 24 }
actions:
  - action: select.select_option
    target:
      entity_id: select.froling_pe1_pellet_20_anlagen_betriebsart
    data:
      option: domestic_hot_water
```

Pellets nachbestellen, wenn der Lagerbestand knapp wird: Automation auf
`number.froling_pe1_pellet_20_pelletlager_restbestand` unter einem Grenzwert.

Die Entitäts-IDs hängen vom Namen des Eintrags, den Gerätenamen und der Sprache ab; die Beispiele
gehen von „Fröling PE1 Pellet 20“, einem Heizkreis „Wohnbereich“ und Deutsch aus.

## Bekannte Grenzen

- **Fröling Connect ist eine inoffizielle Cloud-API** und kann sich ändern; dann sind nur die
  Connect-Werte betroffen. Der Hauptweg bleibt lokal (`iot_class: local_polling`).
- Die **Anlagen-Betriebsart** und der Schalter „Kessel“ lassen sich nur über Connect schalten;
  per Modbus ist die Betriebsart nur lesbar.
- **Ein Client pro Gateway:** Fragen zwei Programme gleichzeitig, vertauscht das Gateway Antworten.
- Das **Raumsoll** in `climate` gibt es nur mit Raumfühler und Modbus. Es zeigt bei
  Absenken/Dauerabsenken das Absenk-Soll; in Automatik entscheidet der Zeitplan aus Fröling
  Connect (in einer Phase Heiz-Soll, sonst Absenk-Soll). Ohne Zeitplan aus Connect zeigt und
  schreibt `climate` in Automatik das Heiz-Soll; das Absenk-Soll ist über das Preset „Absenken“
  erreichbar.
- Störungen lassen sich nur anzeigen, nicht quittieren. Mit nur Connect gilt als Störung, was der
  Kesselzustand meldet; Warnungen stehen im Verlauf.
- Zeitpläne von Puffer und Zirkulationspumpe, Expertenmodus, Statistik und Benachrichtigungen
  von Connect werden nicht genutzt; ebenso die Fernsteuerung per Modbus (Register 48001 ff.).
- Nicht angeschlossene Fühler liefern Platzhalterwerte und werden als „unbekannt“ gezeigt.
- Störungstexte kommen aus der Fröling-Doku bzw. aus Connect und sind deutsch.

## Fehlersuche

| Meldung | Ursache und Abhilfe |
|---|---|
| „Keine Verbindung zum Gateway“ | IP/Port prüfen; Gateway erreichbar (Webseite des Gateways)? |
| „Kessel antwortet nicht“ | Slave-ID, gerades Kabel an COM2, 57600 8N1, „COM 2 als MODBUS Schnittstelle = JA“ |
| „Kessel antwortet unerwartet“ | Falsche Slave-ID oder Gateway nicht im Modbus-TCP-Modus |
| „Fröling Connect hat die Anmeldung abgelehnt“ | Zugangsdaten in der Fröling-App prüfen |
| Reparatur „Schreiben per Modbus ausgeschaltet“ | „MODBUS-Protokoll 2014 verwenden = JA“ setzen |
| Reparatur „Fernschalten über Fröling Connect ausgeschaltet“ | In der Connect-App oder am Kessel einschalten |
| Werte springen oder Zeitüberschreitungen | Zweiten Modbus-Client auf dem Gateway abschalten |

Diagnosedaten (Gerät → Diagnose herunterladen) enthalten Verbindungsart, Quelle je Wert,
Rohregister, Connect-Werte und Systeminformationen. Zugangsdaten, Anlagen-ID und Herstellnummer
sind geschwärzt; Adresse, Name und Kopplungscode aus Connect werden gar nicht übernommen.

## Entfernen

Einstellungen → Geräte & Dienste → Fröling Lambdatronic → Menü → **Löschen**. Danach in HACS
deinstallieren und Home Assistant neu starten. Am Kessel muss nichts zurückgestellt werden.

---

<a id="english"></a>

# Fröling Lambdatronic for Home Assistant

Home Assistant integration for Fröling pellet boilers with **Lambdatronic 3200** control
(developed and tested with a **PE1 Pellet**). It connects in three ways – **Modbus** (local, via a
Modbus TCP gateway on COM2), **Fröling Connect** (cloud account of the Fröling app) or **both**
(Modbus for every value it has, Fröling Connect for the rest) – with the same devices and
entities.

## Supported devices

Lambdatronic 3200 boilers; for Modbus with COM2 (Fröling document B1200522) behind an
RS232-to-Ethernet gateway in "Modbus TCP gateway" mode (e.g. Waveshare RS232/485-TO-ETH), for
Connect registered in Fröling Connect. Up to 18 heating circuits, 8 DHW tanks, 4 buffers and a
circulation pump; installed components are detected. Tested so far only with a PE1 pellet
boiler (reports welcome as issues).

## What is available with which connection?

| Function | Modbus | Connect | Both |
|---|---|---|---|
| Current values (temperatures, states, counters) | ✔ | ✔ (interval ≥ 1 min) | ✔ (Modbus) |
| Boiler control output, feed rate, induced draught speed, calculated setpoint | ✔ | – | ✔ |
| Induced draught control, buffer charge, heating circuit pump | – | ✔ | ✔ (charge, pump) |
| Settings (heating curve, setback, heating limits, frost protection, DHW, buffer, circulation) | ✔ | ✔ | ✔ (Modbus) |
| Room setpoint in `climate` (room sensor only) | ✔ | – | ✔ |
| System operating mode, boiler switch | – | ✔ | ✔ |
| DHW operating mode (automatic/extra charge) | – | ✔ | ✔ |
| Setting the 2nd pellet filling | – | ✔ | ✔ |
| Schedules (actions) | – | ✔ | ✔ |
| Resetting ash and pellet counters | – | ✔ | ✔ |
| Fault text, time and history | text | ✔ | ✔ |
| System information (boiler type, serial number, versions) | – | ✔ | ✔ |

Values are written via Modbus if writable there, otherwise via Fröling Connect, which needs
"Fernschalten über connect möglich" (remote control) enabled. Limits follow the write path
(e.g. boiler setpoint 70–90 °C via Modbus, 40–90 °C with Connect only).

## Prerequisites

Modbus: service menu (code −7) → MODBUS settings: *COM 2 als MODBUS Schnittstelle = JA*,
*MODBUS Adresse* (default 2, the slave ID), *MODBUS Protokoll = 1 (RTU)*,
**MODBUS-Protokoll 2014 verwenden = JA** (required for writing). Gateway: 57600 baud, 8N1, no
flow control, Modbus TCP ↔ RTU, port 502, straight cable (no null modem) to COM2. Fröling
Connect: the credentials of the Fröling app; enable remote control to change settings.

## Installation

HACS → Integrations → menu → **Custom repositories** →
`https://github.com/jkey/ha-froeling-lambdatronic`, category **Integration**; install, restart
Home Assistant, then add "Fröling Lambdatronic" under Settings → Devices & services.
The gateway supports only one Modbus client at a time: remove other pollers first.

## Configuration

Choose the connection type. Modbus asks for the host; port (502) and slave ID (2) are in the
collapsed "Advanced" section. Connect asks for email and password and, for accounts with several
facilities, the facility (name and boiler type). With both, a confirmation step lists components
reported by only one of them; all components found are set up. The entry is named "Fröling
{boiler type}" (with Connect) or "Fröling". A Connect facility can only be used by one entry.
Components are not selected any more: hide unwanted ones with **Device → Disable** (Connect
only: disabled devices are no longer polled).

Options: value interval (Modbus, 10–300 s, default 30 s), parameter interval (Modbus, 1–60 min,
default 5 min), Fröling Connect interval (1–60 min, default 2 min). Reconfigure offers changing
or adding the Modbus connection, adding Fröling Connect or changing its login (same account
without password keeps the stored one) and, with both, removing one of them; the equipment is
detected again. If Fröling Connect rejects the login, Home Assistant asks for new credentials.

Upgrading from 0.1.0: entries are migrated on the first start; components deselected in the
options become disabled devices; "Boiler output" becomes "Boiler control output" with the same
entity ID. These entities are removed, so adapt automations, scripts and dashboards using them:
`sensor.*_pellet_counter_kg` (replaced by the pellet counter in kg; the old statistics remain
orphaned), `select.*_hc{n}_operating_mode` (replaced by the HVAC mode and presets of the heating
circuit's `climate` entity), `number.*_dhw{n}_setpoint` (replaced by the DHW tank's
`water_heater` entity), `sensor.*_dhw{n}_temperature` (DHW tank temperature, replaced
by the current temperature of the tank's `water_heater` entity; the old statistics remain
orphaned) and `sensor.*_system_state` (replaced by the "System operating mode" select, which shows
only the three selectable modes automatic/domestic hot water/continuous load, other states show
as unknown; the old history remains orphaned).

## Entities

A boiler device with child devices for heating circuits, DHW tanks, buffers and the circulation
pump, named as in Fröling Connect or by the Fröling scheme ("Heating circuit 01", "DHW tank 01",
"Buffer 01", "Circulation pump"); names set in Home Assistant stay. Highlights: `climate` per
heating circuit (off/auto/heat, presets automatic, extra heating, setback, permanent setback,
party; room setpoint only with a room sensor), `water_heater` per DHW tank (setpoint, with
Connect automatic/extra charge), sensors for temperatures, states, counters and the fault (with
Connect attributes `since` and `history`), numbers for all customer settings, switches for the
boiler (Connect) and the automatic feed, times for the pellet fillings, buttons to read the
parameters again and to reset the ash and pellet counters (Connect), diagnostics such as hours
since maintenance, hours in partial load, connected since and last signal.

## Actions

`froeling_lambdatronic.get_schedule` returns the weekly switch-on phases of a heating circuit
(`climate`) or DHW tank (`water_heater`), e.g.
`{"monday": [{"from": "04:30", "to": "22:00"}], …, "unreadable_days": []}`. Days whose phases
Fröling Connect returns unreadable are missing in the response and listed in `unreadable_days`
(e.g. `["tuesday"]`); a read-edit-write cycle must give them again.
`froeling_lambdatronic.set_schedule` replaces the phases of the given days (`monday` …
`sunday`, at least one day, up to four `{from, to}` per day, `to` may be `24:00` in YAML); days
not given stay, an empty list clears a day. Fröling Connect always writes the whole week, so the
action refuses to write while a day not given has unreadable phases (it would be erased); the
error names those days. Both need Fröling Connect; see the German example above.

## Data updates

Modbus: values every 30 s, parameters every 5 min and right after each change. Fröling Connect:
every 2 min (overview only next to Modbus, all components otherwise); system information hourly.
No failover: if one source fails, only its entities become unavailable; failure and recovery are
logged once. Rate limits (HTTP 429) are respected.

## Use cases

Push notification on a fault, switching to domestic hot water in summer, longer heating at the
weekend, reordering pellets when the store runs low – see the German examples above.

## Known limitations

Fröling Connect is an unofficial cloud API. The system operating mode and the boiler switch need
Connect. One Modbus client per gateway. The room setpoint needs a room sensor and Modbus; in
automatic the Connect schedule decides between heating and setback setpoint. Without a schedule
from Connect the climate entity shows and writes the heating setpoint in automatic; the setback
setpoint is reachable via the setback preset. Faults can be shown but not acknowledged. Buffer
and circulation schedules, expert mode, statistics and Modbus remote control (48001 ff.) are not
used. Fault texts are German.

## Troubleshooting

"Cannot connect": check IP and port. "Boiler does not answer": slave ID, cable, serial settings.
"Fröling Connect rejected the login": check the credentials in the app. Repair "Modbus writing
disabled": set *MODBUS-Protokoll 2014 verwenden = JA*. Repair "remote control disabled": enable
it in the Connect app or at the boiler. Diagnostics contain the connection type, the source of
each value, raw registers, Connect values and system information; credentials, facility ID and
serial number are redacted.

## Removal

Settings → Devices & services → Fröling Lambdatronic → menu → **Delete**, then uninstall in
HACS and restart Home Assistant.

## License

MIT
