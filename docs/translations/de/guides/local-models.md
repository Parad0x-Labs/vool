---
description: So führst du KI-Modelle mit VOOL lokal auf deinem eigenen Computer aus — einen lokalen Modellserver installieren, eine Modellgröße wählen und vollständig offline arbeiten.
translation: machine
source_sha256: a19d4790729d13ed5e1b134bcb67ed3e7a80609561be4227ab9480492100c8a6
---

# Ein lokales Modell ausführen

## Eine Laufzeitumgebung installieren

VOOL nutzt standardmäßig Ollama, um lokale Modelle auszuführen; andere lokale Laufzeitumgebungen mit einem
OpenAI-kompatiblen Endpunkt funktionieren auch. Ollama ist ein eigenes Programm, das separat
von [ollama.com](https://ollama.com) installiert wird. Die macOS-App bringt einen eigenen Runner für lokale Modelle mit,
daher kannst du diesen Schritt auf einem Mac überspringen. Unter Linux und bei Installationen aus dem Quellcode
installierst du Ollama, startest es und lädst ein Modell herunter.

```bash
ollama serve
ollama pull qwen3:4b
```

VOOL wählt das lokale Modell standardmäßig für dich aus — ein Qwen-Modell, passend zur Größe deines Computers. Auf
vielen Computern ist das `qwen3:4b`; auf Computern mit 32 GB Arbeitsspeicher oder mehr ist das
Standardmodell ein größeres Qwen-Modell.

## VOOL darauf ausrichten

Öffne **Einstellungen → Modelle & Anbieter**. Laufende lokale Laufzeitumgebungen werden automatisch erkannt.
Wähle ein Modell aus, und die Modusanzeige wechselt zu Lokal.

## Einmal bereit machen

Ein lokales Modell schreibt Antworten erst, nachdem VOOL es geprüft hat. Öffne
**Einstellungen → Modelle & Anbieter → Lokale Modelle** und drücke **Dieses Modell verwenden** neben dem Modell.
VOOL prüft still auf deinem Computer, ob das Modell die Werkzeuge von VOOL gut genug nutzen kann, um für dich
zu antworten. Die Prüfung kann ein bis zwei Minuten dauern; nichts wird heruntergeladen, und niemand wird kontaktiert. Wenn
sie bestanden ist, zeigt das Modell **Bereit**, und VOOL antwortet damit. Bis dahin lässt VOOL es keine
endgültigen Antworten schreiben.

* **Passt nicht gut:** Das Modell kann die Werkzeuge von VOOL nicht gut genug nutzen. VOOL zeigt dir, was du
  stattdessen tun kannst: das Modell verwenden oder herunterladen, das es für diesen Computer ausgewählt hat, oder ein Cloud-Modell verwenden.
* **Prüfung nicht beendet:** Sorge dafür, dass Ollama geöffnet ist, und drücke dann **Erneut versuchen**. Ein langsamer Computer
  braucht vielleicht einen zweiten Versuch.

Ist beim Start von VOOL kein lokales Modell bereit, prüft es bis zu drei der Modelle auf diesem Computer,
zuerst das von dir ausgewählte, und nutzt das erste, das besteht. Ein Modell, das für diesen Computer zu groß ist,
überspringt es. Ein bereites Modell kommt immer vor einem, das nicht bereit ist.

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
