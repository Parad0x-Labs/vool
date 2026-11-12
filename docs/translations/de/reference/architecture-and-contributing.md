---
description: Wie VOOL gebaut ist — die Architektur auf einer Seite, die Einrichtung für Entwickler und wie du mitwirken kannst.
translation: machine
source_sha256: 7cb91029e16c6510821e1ca26b509da8cdfb63113111619d3a31e364a2f088d3
---

# Architektur und Mitwirken

## Die Architektur auf einer Seite

VOOL ist eine Desktop-Laufzeitumgebung, die zuerst lokal arbeitet, mit einem Kern, der als Dienst läuft:

```text
┌──────────────────────────────────────────────┐
│ Native window / browser console              │
│  (served pages; the console you use)         │
├──────────────────────────────────────────────┤
│ Served runtime (local HTTP API)              │
│  routing → tool execution → answers          │
├───────────────┬──────────────┬───────────────┤
│ Local models  │ Cloud lanes  │ Wallet (opt)  │
│ (Ollama etc.) │ (your keys)  │ pilot lanes   │
├───────────────┴──────────────┴───────────────┤
│ Records: receipts, ledgers, fault registry   │
│ Local storage (SQLite under your profile)    │
└──────────────────────────────────────────────┘
```

Grundsätze, die der Code durchsetzt:

* **Standardmäßig lokal; Cloud mit deinem Schlüssel.** Cloud-Wege gibt es nur mit deinen Zugangsdaten und
  deinen Obergrenzen.
* **Eine maßgebliche Stelle pro Entscheidung.** Berechtigungen, Ausgaben und ausgehender Netzwerkverkehr
  haben jeweils einen zuständigen Vertrag; Fehler werden an der Grenze eingeordnet, der sie gehören.
* **Sagen, was fehlgeschlagen ist.** Unbekannte Ergebnisse werden als unbekannt typisiert; Ablehnungen sagen,
  was nicht passiert ist; ein angeheftetes Modell wird nie stillschweigend ersetzt.
* **Alles festgehalten.** Werkzeugaufrufe, Freigaben, Zahlungen und Updates schreiben Belege in deinem Profil.

## Einrichtung für Entwickler

```bash
git clone https://github.com/Parad0x-Labs/vool.git
cd vool
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests -q            # the suite
```

Hinweise zu den Plattformen, der vollständige Aufbau der Toolchain und die Prüfstufen vor einem Release
stehen in den technischen Dokumenten des Repositorys (`docs/` im Stammverzeichnis des Repositorys; die
tieferen Interna gehören nicht zu dieser öffentlichen Doku-Website).

## Doku: eine maßgebliche Quelle

Diese Doku wird aus **einer** Quelle erzeugt: dem Ordner `docs/` dieses Repositorys (GitBook-Aufbau,
`.gitbook.yaml` im Stammverzeichnis des Repositorys). Die öffentliche Doku-Website wird daraus gebaut. So
arbeitest du an der Doku:

```bash
python3 tools/docs/build_docs.py --src . --out docs-build --base /docs
python3 tools/docs/check_docs_build.py docs-build /docs
python3 -m http.server -d docs-build 8000   # local preview
```

Die Fehlerreferenz (`ERROR_BOOK.md`) und das Übergabemanifest für die Lokalisierung werden **aus den eigenen
Registern der Laufzeitumgebung erzeugt** — erzeuge sie neu, bearbeite sie nie von Hand:

```bash
python -m tools.generate_error_book
python -m tools.generate_localization_handoff
```

Ein gezielter Test lässt den Build fehlschlagen, wenn die erzeugten Dateien von ihren Registern abweichen.

## Mitwirken

* Kleine, geprüfte Änderungen; jede Aussage in der Doku muss zum ausgelieferten Code passen.
* Behalte feste Codes und Bezeichner bei — sie überdauern Builds.
* Erweitere nie stillschweigend eine Befugnis: Eine neue Berechtigung, ein neuer Ausgabeweg oder ein neuer
  ausgehender Netzwerkweg ist eine Änderung am Design, kein Patch.
* Beta-Einschränkungen werden dokumentiert, nicht versteckt — wenn du eine undokumentierte findest, ist das
  ein Fehler in der Doku.
