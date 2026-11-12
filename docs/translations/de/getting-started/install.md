---
description: So installierst du den KI-Assistenten VOOL unter macOS (Apple Silicon oder Intel) oder Linux aus den Release-Downloads oder aus dem Quellcode unter macOS, Windows oder Linux, mit den Systemvoraussetzungen und der Prüfung eines Downloads.
translation: machine
source_sha256: e0025d89fc56caca91923de6f1e6afe25ba6f6383a300ec3df5a7f7e3f401e55
---

# Installation

VOOL 0.7.7-beta bietet App-Downloads für Macs mit Apple Silicon, Intel-Macs und Linux x86_64 im
[Release v0.7.7-beta](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.7-beta).
Du kannst VOOL auch mit dem Bootstrap-Skript aus dem Quellcode installieren, unter macOS, Linux oder Windows.
Details zu Signierung und Updates findest du unter [Release-Status](../../../trust/release-status.md).

## Voraussetzungen

| | Minimum | Komfortabel |
| --- | ---: | ---: |
| Arbeitsspeicher | 8 GB | 16 GB oder mehr |
| Speicherplatz | 2 GB für die App | 20 GB mit lokalen Modellen |
| macOS | 14 Sonoma, Apple Silicon oder Intel ([Einschränkungen](#intel-macs)) | 14 Sonoma oder neuer, Apple Silicon |
| Windows | 10 (64 Bit) | 11 |
| Linux | glibc 2.31 | Ubuntu 22.04 oder neuer |

Lokale Modelle brauchen deutlich mehr Arbeitsspeicher als die App selbst. Hinweise zur passenden Größe
findest du unter [Ein lokales Modell ausführen](../../../guides/local-models.md).

## Die App herunterladen

| Dein Computer | Download |
| --- | --- |
| Mac mit Apple Silicon (M1 und neuer) | [`VOOL-0.7.7-beta-macos-arm64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/VOOL-0.7.7-beta-macos-arm64.dmg) |
| Mac mit Intel-Chip | [`VOOL-0.7.7-beta-macos-x86_64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/VOOL-0.7.7-beta-macos-x86_64.dmg) |
| Linux x86_64 (Ubuntu 22.04 / 24.04) | [`vool-0.7.7-beta-linux-x86_64.tar.gz`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/vool-0.7.7-beta-linux-x86_64.tar.gz) |

Um den Chip deines Macs herauszufinden, öffne das Apple-Menü und wähle **Über diesen Mac**. „Apple M…“
bedeutet Apple Silicon, „Intel“ bedeutet Intel. Die App für Apple Silicon läuft nicht auf einem
Intel-Mac und sagt das beim Öffnen.

Prüfe die SHA-256-Prüfsumme des Downloads, bevor du ihn öffnest (siehe [Einen Download prüfen](#einen-download-prüfen)).

### Erstes Öffnen auf dem Mac

Die Beta-App ist noch nicht von Apple notariell beglaubigt, daher bittet macOS dich beim ersten Öffnen
um eine Bestätigung. Das ist so vorgesehen und passiert nur einmal.

1. Öffne die DMG und ziehe **VOOL** auf den Ordner **Programme**.
2. Doppelklicke auf VOOL. macOS meldet, dass es die App nicht auf Schadsoftware überprüfen kann.
   Schließe die Meldung.
3. Öffne **Systemeinstellungen → Datenschutz & Sicherheit**. Scrolle nach unten zur Meldung über VOOL
   und klicke auf **Dennoch öffnen**. Gib zur Bestätigung dein Passwort ein.

Unter macOS 14 Sonoma kannst du stattdessen im Ordner „Programme“ bei gedrückter Taste „ctrl“ auf VOOL
klicken, **Öffnen** wählen und dann noch einmal auf **Öffnen** klicken.

### Linux

Lade das Tarball und die zugehörige `.sha256`-Datei aus dem Release herunter, prüfe es und starte dann
den mitgelieferten Installer. Weder sudo noch ein System-Python wird benötigt.

```bash
sha256sum -c vool-0.7.7-beta-linux-x86_64.tar.gz.sha256
tar -xzf vool-0.7.7-beta-linux-x86_64.tar.gz
bash vool-0.7.7-beta-linux-x86_64/install.sh
~/.local/bin/vool
```

VOOL wird nach `~/.local/share/vool` installiert, legt den Befehl `vool` in `~/.local/bin` ab und fügt
einen Eintrag im Anwendungsmenü hinzu. Die Desktop-App öffnet den Chat in deinem Browser; wenn du den
Browser schließt, läuft VOOL weiter. Um VOOL zu entfernen, führe `bash ~/.local/share/vool/current/uninstall.sh` aus.
Deine Chats und Einstellungen bleiben in deinem Profil.

Ab 0.7.5 aktualisiert sich der Linux-Download innerhalb von VOOL. Die neue Version wird neben der
alten installiert. Startet sie nicht richtig, wechselt VOOL von selbst zurück.

### Nach der Installation

Bevor ein lokales Modell antworten kann, öffne **Einstellungen → Modelle & Anbieter → Lokale Modelle** und drücke dafür einmal **Certify**. Das führt eine kurze Prüfung auf deinem Rechner aus.

Ab 0.7.5 aktualisiert sich VOOL selbst: Ist „Von selbst online gehen“ eingeschaltet, zeigt VOOL einen Hinweis und installiert eine neue Version mit einem Klick. Update von 0.7.4 oder 0.7.3 auf einem Mac: Schalte „Von selbst online gehen“ ein (Einstellungen > Privatsphäre & Berechtigungen) und drücke einmal auf „Nach Updates suchen“. Von 0.7.4 oder älter unter Linux oder von 0.7.2 und älter auf einem Mac: Lade 0.7.7 herunter und installiere es über deine aktuelle Version. Deine Chats und Einstellungen
liegen in deinem Profil, außerhalb der App. Krypto-Funktionen sind standardmäßig ausgeschaltet.

## Aus dem Quellcode installieren

Führe das Bootstrap-Skript für deine Plattform aus:

```bash
curl -fsSLo bootstrap_vool.sh https://raw.githubusercontent.com/Parad0x-Labs/vool/v0.7.7-beta/installer/bootstrap_vool.sh
bash bootstrap_vool.sh --ref v0.7.7-beta
```

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/Parad0x-Labs/vool/main/installer/bootstrap_vool.ps1 -OutFile bootstrap_vool.ps1
powershell -ExecutionPolicy Bypass -File .\bootstrap_vool.ps1
```

Die Befehle für macOS / Linux legen sowohl das Bootstrap-Skript als auch seinen Quellcode-Download auf `v0.7.7-beta` fest. Für ein anderes Release änderst du sowohl das Tag in der Skript-URL als auch `--ref`. Das Bootstrap-Skript akzeptiert einen Branch-Namen oder ein Release-Tag, keinen Commit-Hash.

Für die Installation aus dem Quellcode brauchst du Python 3.11 oder neuer. Wird keins gefunden, versucht der
Installer, eins einzurichten. Die App-Downloads bringen ihr eigenes Python mit.

Der Installer richtet die Python-Umgebung ein, prüft deine Hardware, richtet das lokale
Modell ein und startet die lokalen Dienste. Plane Zeit und Speicherplatz für den ersten Modell-Download
ein. Lokale Modelle funktionieren nach der Einrichtung offline; Web-Werkzeuge und Cloud-Modelle brauchen
eine Verbindung.

## Intel-Macs

VOOL läuft auf Intel-Macs mit macOS 14 oder neuer (zum Beispiel einem 13-Zoll-MacBook Pro von 2020),
mit dem Intel-Download oder aus dem Quellcode. Der Quellcode-Installer erkennt die Intel-CPU und
installiert die Intel-Builds aller Abhängigkeiten. Zwei Einschränkungen gelten nur für Intel:

* **Kein lokales Modelltraining.** PyTorch hat seit 2.3 keinen Build für macOS auf Intel mehr veröffentlicht,
  daher überspringt der Installer es, und das LoRA-Training meldet sich als nicht verfügbar. Chat, Gedächtnis,
  Werkzeuge und Cloud-Modelle sind nicht betroffen.
* **Lokale Modelle laufen nur auf der CPU.** Programme für lokale Modelle haben keine GPU-Beschleunigung für
  Intel-Grafik, daher sind lokale Antworten um ein Mehrfaches langsamer als auf Apple Silicon. Auf einem Mac
  mit 8 GB nutze ein Cloud-Modell für den Chat; mit 16 GB funktioniert das kleine lokale Standardmodell des
  Installers, ist aber langsam.

Auf Intel-Macs laufen lokale Modelle auf dem Prozessor und sind langsam. Eine lange Antwort aus dem Gedächtnis kann in eine Zeitüberschreitung laufen, nutze dort also vorerst ein Cloud-Modell. Die Zertifizierung lokaler Modelle berücksichtigt langsamere Prozessoren.

Intel-Macs haben einen eigenen Download, `VOOL-0.7.7-beta-macos-x86_64.dmg`. Der Download für Apple Silicon
läuft nicht auf Intel und sagt das beim Start.

## Einen Download prüfen

Jeder Download im Release hat daneben eine SHA-256-Prüfsumme. Prüfe die Datei, bevor du
sie öffnest:

```bash
shasum -a 256 <downloaded-artifact>    # macOS
sha256sum <downloaded-artifact>         # Linux
```

```powershell
Get-FileHash -Algorithm SHA256 <downloaded-artifact>
```

Vergleiche das Ergebnis mit der Prüfsumme, die mit diesem Release veröffentlicht wurde. Wenn sie sich
unterscheiden, lösche die Datei und lade sie erneut herunter.

## Weiter

Weiter geht es mit [Erster Start](first-run.md).
