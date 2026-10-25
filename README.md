# Fröling Lambdatronic für Home Assistant

[English version below](#english)

Home-Assistant-Integration für Fröling-Pelletkessel mit der Regelung **Lambdatronic 3200**
(entwickelt und getestet mit einem **PE1 Pellet**). Sie liest alle wichtigen Messwerte lokal per
**Modbus TCP**, stellt Kundenparameter (Heizkurve, Absenkung, Heizgrenzen, Warmwasser,
Kessel-Solltemperatur, Heizkreis-Betriebsart) zum Ändern bereit und schaltet optional über
**Fröling Connect** die Anlagen-Betriebsart um.

Die Integration nutzt die Bibliothek
[froeling-lambdatronic](https://pypi.org/project/froeling-lambdatronic/) ohne fremde
Modbus-Bibliothek.

## Unterstützte Geräte

- Fröling-Kessel mit Lambdatronic 3200 und Modbus-Schnittstelle an COM2 (Doku Fröling
  B1200522, „Kommunikationsprotokoll ModBus Lambdatronic 3200“). Getestet: PE1 Pellet.
- Ein RS232-zu-Ethernet-Gateway im Modus „Modbus TCP Gateway“, z. B. Waveshare
  RS232/485-TO-ETH.
- Bis zu 18 Heizkreise, 8 Warmwasserspeicher und 4 Pufferspeicher; erkannt wird, was belegt ist.

## Voraussetzungen am Kessel

Servicemenü (Code −7) → MODBUS-Einstellungen:

| Einstellung | Wert |
|---|---|
| COM 2 als MODBUS Schnittstelle | JA |
| MODBUS Adresse | 2 (Vorgabe; in der Integration als Slave-ID eintragen) |
| MODBUS Protokoll | 1 (RTU) |
| MODBUS-Protokoll 2014 verwenden | **JA** (sonst ist kein Schreiben möglich) |

Gateway: seriell 57600 Baud, 8N1, keine Flusssteuerung, Modus „Modbus TCP ↔ RTU“, Port 502.
Verbindung mit einem **geraden** Kabel (kein Nullmodem) an COM2 der Steuerungsplatine.

Für die Anlagen-Betriebsart über Fröling Connect muss am Kessel bzw. in der Connect-App
„Fernschalten über connect möglich“ eingeschaltet sein.

## Installation

1. HACS → Integrationen → Menü → **Benutzerdefinierte Repositories** →
   `https://github.com/jkey/ha-froeling-lambdatronic`, Kategorie **Integration**.
2. „Fröling Lambdatronic“ installieren und Home Assistant neu starten.
3. Einstellungen → Geräte & Dienste → **Integration hinzufügen** → „Fröling Lambdatronic“.

**Wichtig:** Das Gateway verträgt nur einen Modbus-Client gleichzeitig. Andere Abfragen
(z. B. ein YAML-`modbus:`-Eintrag auf dasselbe Gateway) vor dem Einrichten entfernen.

## Einrichtung

| Schritt | Feld | Bedeutung |
|---|---|---|
| Gateway | Host | IP-Adresse oder Hostname des Gateways |
| | Port | TCP-Port, meist 502 |
| | Slave-ID | MODBUS Adresse am Kessel, meist 2 |
| | Name | Name des Kessel-Geräts, Vorgabe „Fröling PE1“ |
| Ausstattung | Heizkreise, Warmwasserspeicher, Pufferspeicher | Erkannte Komponenten; Abgewähltes erscheint nicht |
| Fröling Connect | Benutzername, Passwort | Optional; leer lassen für reinen Modbus-Betrieb |
| Anlage | Anlage | Nur wenn das Connect-Konto mehrere Anlagen hat |

Vor dem Anlegen liest die Integration den Anlagenzustand (Register 34001) und erkennt die
Ausstattung. Mit Connect werden Anmeldung und „Fernschalten“ geprüft.

### Optionen

| Feld | Bedeutung |
|---|---|
| Heizkreise / Warmwasserspeicher / Pufferspeicher | Komponenten an- oder abwählen; Geräte abgewählter Komponenten werden entfernt |
| Werte-Takt | Abfrage der Messwerte, 10–300 s (Vorgabe 30 s) |
| Parameter-Takt | Abfrage der Einstellungen, 1–60 min (Vorgabe 5 min); nach jeder Änderung zusätzlich sofort |

### Neu konfigurieren

Über „Neu konfigurieren“ lassen sich Host, Port, Slave-ID und die Fröling-Connect-Anmeldung
ändern. Benutzername leer = Connect entfernen; Passwort leer = gespeichertes Passwort behalten.

## Geräte und Entitäten

Das Gerät „Fröling PE1“ (Name wählbar) mit Untergeräten „Heizkreis N“, „Warmwasser N“ und
„Puffer N“. Untergeräte lassen sich umbenennen (z. B. „Radiatoren EG/UG“); die Entitätsnamen
folgen.

| Gerät | Entitäten |
|---|---|
| Kessel | Kesseltemperatur, Kessel-Soll errechnet, Abgastemperatur, Restsauerstoff, Saugzugdrehzahl, Einschub, Kesselleistung, Außentemperatur, Füllstand Pelletbehälter, Pelletverbrauch gesamt (kg), Pellet-kg-Zähler, Anlagenzustand, Kesselzustand, Störung (Text mit Attributen `fehlercode` und `weitere`), Rücklauftemperatur (deaktiviert) |
| Kessel (Diagnose) | Betriebsstunden, Stunden im Heizen, Brennerstarts, Heizstunden bis Asche entleeren, Taste „Parameter neu lesen“ |
| Kessel (binär) | Störung aktiv (Problem), Kesselanforderung |
| Kessel (Einstellungen) | Kessel-Solltemperatur (70–90 °C), Pelletlager Restbestand und Mindestbestand (t) |
| Kessel (Auswahl) | Anlagen-Betriebsart Automatik/Brauchwasser/Dauerlast – nur mit Fröling Connect |
| Heizkreis | Vorlauf Ist, Vorlauf Soll, Vorlauf laut Heizkurve (mit Attribut `kurve`), Raumtemperatur (deaktiviert), Betriebsart (Aus/Automatik/Extraheizen/Absenken/Dauerabsenken/Partybetrieb) |
| Heizkreis (Einstellungen) | Heizkurve Vorlauf bei plus 10 °C und bei minus 10 °C, Absenkung, Heizgrenze Heizbetrieb und Absenkbetrieb |
| Warmwasser | Temperatur, Pumpe; Einstellungen Solltemperatur und Nachladen unter |
| Puffer | Temperatur oben, Temperatur unten, Pumpe |

## Aktualisierung der Daten

- Messwerte alle 30 s (einstellbar), Einstellungen alle 5 min (einstellbar) und sofort nach
  jeder Änderung. Alle Abfragen laufen nacheinander über eine einzige dauerhafte Verbindung.
- Fröling Connect wird alle 5 min nach Betriebsart und „Fernschalten“ gefragt. Die angezeigte
  Anlagen-Betriebsart kommt immer per Modbus vom Kessel.
- Fällt das Gateway aus, werden alle Modbus-Entitäten „nicht verfügbar“; Ausfall und Rückkehr
  stehen je einmal im Log.

## Anwendungsfälle und Beispiele

Störung als Push-Nachricht:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.froling_pe1_storung_aktiv
    to: "on"
actions:
  - action: notify.mobile_app_handy
    data:
      title: Kessel-Störung
      message: "{{ states('sensor.froling_pe1_storung') }}"
```

Im Sommer auf Brauchwasser umschalten (benötigt Fröling Connect):

```yaml
triggers:
  - trigger: numeric_state
    entity_id: sensor.froling_pe1_aussentemperatur
    above: 18
    for: { hours: 24 }
actions:
  - action: select.select_option
    target:
      entity_id: select.froling_pe1_anlagen_betriebsart
    data:
      option: domestic_hot_water
```

Pellets nachbestellen, wenn der Lagerbestand knapp wird: Automation auf
`number.froling_pe1_pelletlager_restbestand` unter einem Grenzwert.

Die Entitäts-IDs hängen vom gewählten Gerätenamen und der Sprache ab; die Beispiele gehen von
„Fröling PE1“ und Deutsch aus.

## Bekannte Grenzen

- Die **Anlagen-Betriebsart** ist per Modbus nur lesbar; Umschalten geht nur über Fröling
  Connect (inoffizielle Cloud-API, kann sich ändern).
- **Ein Client pro Gateway:** Fragen zwei Programme gleichzeitig, vertauscht das Gateway Antworten.
- Nicht angeschlossene Fühler liefern Platzhalterwerte und werden als „unbekannt“ gezeigt;
  ein Rücklauffühler fehlt oft (Wert 0), daher ist die Rücklauftemperatur deaktiviert.
- Die Fernsteuerung des Kessels per Modbus (Register 48001 ff.) wird nicht genutzt.
- Störungstexte kommen aus der Fröling-Doku und sind deutsch.

## Fehlersuche

| Meldung | Ursache und Abhilfe |
|---|---|
| „Keine Verbindung zum Gateway“ | IP/Port prüfen; Gateway erreichbar (Webseite des Gateways)? |
| „Kessel antwortet nicht“ | Slave-ID, gerades Kabel an COM2, 57600 8N1, „COM 2 als MODBUS Schnittstelle = JA“ |
| „Kessel antwortet unerwartet“ | Falsche Slave-ID oder Gateway nicht im Modbus-TCP-Modus |
| Reparatur „Schreiben per Modbus ausgeschaltet“ | „MODBUS-Protokoll 2014 verwenden = JA“ setzen |
| Reparatur „Fernschalten über Fröling Connect ausgeschaltet“ | In Connect-App oder am Kessel einschalten |
| Werte springen oder Zeitüberschreitungen | Zweiten Modbus-Client auf dem Gateway abschalten |

Diagnosedaten (Gerät → Diagnose herunterladen) enthalten die Rohregister und letzten Fehler;
Zugangsdaten sind geschwärzt.

## Entfernen

Einstellungen → Geräte & Dienste → Fröling Lambdatronic → Menü → **Löschen**. Danach in HACS
deinstallieren und Home Assistant neu starten. Am Kessel muss nichts zurückgestellt werden.

---

<a id="english"></a>

# Fröling Lambdatronic for Home Assistant

Home Assistant integration for Fröling pellet boilers with **Lambdatronic 3200** control
(developed and tested with a **PE1 Pellet**). It reads all important values locally via
**Modbus TCP**, lets you change customer parameters (heating curve, setback, heating limits,
hot water, boiler setpoint, heating circuit mode) and optionally switches the system operating
mode via **Fröling Connect**.

## Supported devices

Lambdatronic 3200 boilers with Modbus on COM2 (Fröling document B1200522) behind an
RS232-to-Ethernet gateway in "Modbus TCP gateway" mode (e.g. Waveshare RS232/485-TO-ETH).
Up to 18 heating circuits, 8 hot water tanks and 4 buffers; installed components are detected.

## Boiler prerequisites

Service menu (code −7) → MODBUS settings: *COM 2 als MODBUS Schnittstelle = JA*,
*MODBUS Adresse* (default 2, the slave ID), *MODBUS Protokoll = 1 (RTU)*,
**MODBUS-Protokoll 2014 verwenden = JA** (required for writing). Gateway: 57600 baud, 8N1, no
flow control, Modbus TCP ↔ RTU, port 502, straight cable (no null modem) to COM2. For the
system operating mode enable "Fernschalten über connect möglich".

## Installation

HACS → Integrations → menu → **Custom repositories** →
`https://github.com/jkey/ha-froeling-lambdatronic`, category **Integration**; install, restart
Home Assistant, then add "Fröling Lambdatronic" under Settings → Devices & services.
The gateway supports only one Modbus client at a time: remove other pollers first.

## Configuration

Setup asks for host, port (502), slave ID (2) and a device name, then shows the detected
equipment, then optional Fröling Connect credentials (leave empty to skip) and, for accounts
with several facilities, the facility. Options: enabled components, value interval (10–300 s,
default 30 s), parameter interval (1–60 min, default 5 min). Reconfigure changes host, port,
slave ID and the Connect login (empty username removes Connect, empty password keeps it).

## Entities

A boiler device with child devices "Heating circuit N", "Hot water N" and "Buffer N".
Sensors for temperatures, flue gas, oxygen, induced draught, feed, output, pellet level and
consumption, system and boiler state, fault text, counters (diagnostic); binary sensors for
fault and boiler demand; numbers for boiler setpoint, pellet stock, heating curve, setback,
heating limits and hot water settings ("Heating curve flow at plus 10 °C" and "… at minus 10 °C"); selects for the heating circuit mode and (with Connect)
the system operating mode; a button to read the parameters again.

## Data updates

Values every 30 s, parameters every 5 min and right after each change, all over one persistent
connection. Fröling Connect is polled every 5 min. If the gateway fails, Modbus entities become
unavailable; failure and recovery are logged once.

## Use cases

Push notification on a fault, switching to domestic hot water in summer, reordering pellets
when the store runs low – see the German examples above.

## Known limitations

The system operating mode can only be switched via Fröling Connect (unofficial cloud API).
One Modbus client per gateway. Unplugged probes show as unknown; return temperature is
disabled by default. Boiler remote control via Modbus (48001 ff.) is not used. Fault texts are
German.

## Troubleshooting

"Cannot connect": check IP and port. "Boiler does not answer": slave ID, cable, serial settings.
Repair "Modbus writing disabled": set *MODBUS-Protokoll 2014 verwenden = JA*. Repair "remote
control disabled": enable it in the Connect app. Download diagnostics for raw registers;
credentials are redacted.

## Removal

Settings → Devices & services → Fröling Lambdatronic → menu → **Delete**, then uninstall in
HACS and restart Home Assistant.

## License

MIT
