---
description: So führst du KI-Modelle mit VOOL lokal auf deinem eigenen Computer aus — einen lokalen Modellserver installieren, eine Modellgröße wählen und vollständig offline arbeiten.
translation: machine
source_sha256: d93b9995b0f4fec8aca12bb90d8d6f9ff0fd9623c43d4529913dc76f055ac24c
---

# Ein lokales Modell ausführen

## Eine Laufzeitumgebung installieren

VOOL spricht mit einem lokalen Modellserver. Die macOS-App bringt einen eigenen Runner für lokale Modelle mit,
daher kannst du diesen Schritt auf einem Mac überspringen. Unter Linux und bei Installationen aus dem Quellcode
installierst du einen Server wie Ollama, startest ihn und lädst ein Modell herunter.

```bash
ollama serve
ollama pull qwen2.5-coder:7b
```

## VOOL darauf ausrichten

Öffne **Einstellungen → Modelle & Anbieter**. Laufende lokale Laufzeitumgebungen werden automatisch erkannt.
Wähle ein Modell aus, und die Modusanzeige wechselt zu Lokal.

## Einmal zertifizieren

Ein lokales Modell schreibt Antworten erst, wenn es zertifiziert ist. Öffne **Einstellungen → Modelle & Anbieter → Lokale Modelle**,
registriere das Modell und wähle dann **Certify**. Das Zertifizieren führt eine kurze Werkzeugprüfung auf deinem
Rechner aus; nichts wird heruntergeladen, und niemand wird kontaktiert. Bis ein Modell die Prüfung besteht, lässt
VOOL es keine endgültigen Antworten schreiben.

## Die passende Größe

| Modellgröße | Benötigter Arbeitsspeicher | Geeignet für |
| --- | ---: | --- |
| 3B | ~4 GB | Kurze Bearbeitungen, schnelle Fragen |
| 7–8B | ~8 GB | Allgemeine Nutzung, das meiste Programmieren |
| 14B | ~16 GB | Längeres Nachdenken |
| 32B+ | 32 GB oder mehr | Schwerere Arbeit, langsamer |

{% hint style="info" %}
Quantisierte Builds senken den Arbeitsspeicherbedarf erheblich. Ein 7B-Modell mit 4 Bit läuft bequem in etwa
5 GB.
{% endhint %}

## Wenn es langsam ist

* Wähle ein kleineres oder stärker quantisiertes Modell.
* Schließe andere Anwendungen, die viel Arbeitsspeicher brauchen.
* Kürze den Kontext — weniger Dateien in der Anfrage bedeuten weniger zu verarbeiten.
