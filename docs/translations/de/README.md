---
description: VOOL ist ein KI-Assistent für den Desktop, der KI-Modelle lokal auf deinem Computer ausführt oder sich mit deinem eigenen Schlüssel mit Cloud-Modellen verbindet. Kostenlos und privat. Downloads für Mac (Apple Silicon und Intel) und Linux; Windows ist experimentell und wird aus dem Quellcode installiert.
translation: machine
source_sha256: dc6715130dd5dcfe7a6adc926aeff095413e59b7eecc1735d1c2ee1d88990ac5
---

# VOOL

VOOL ist ein KI-Assistent für den Desktop. Er wird als App auf deinem Computer installiert und kann
KI-Modelle lokal auf diesem Rechner ausführen oder sich mit deinem eigenen API-Schlüssel mit einem
Cloud-KI-Anbieter verbinden. Über jede Aktion, die er ausführt, führt er ein lesbares Protokoll.

Er ist kostenlos. Mit einem lokalen Modell brauchen deine Chats und Dateien keine Internetverbindung; aktuelle Fakten
wie Wetter oder Preise kommen nur von öffentlichen Webdiensten, solange Websuchen eingeschaltet ist. Es gibt
Downloads für Macs (Apple Silicon und Intel) und Linux x86_64; Windows ist experimentell und
wird aus dem Quellcode installiert.

Diese Doku zeigt, wie du VOOL installierst und ein Modell verbindest, und erklärt, wie VOOL mit
deinen Dateien, deinem Gedächtnis in VOOL und deinen Schlüsseln umgeht.

## So schneidet VOOL ab

Bei 150 noch nie verwendeten LoCoMo-Gedächtnisfragen, bei denen für jeden Agenten dasselbe Modell antwortete
und derselbe GPT-4o-Bewerter urteilte, schlug das Gedächtnis von VOOL zwei andere Open-Source-Agenten:

| | VOOL | OpenClaw | Hermes |
| --- | --- | --- | --- |
| Richtige Antworten | **82,7 %** (124 / 150) | 71,3 % (107) | 62,7 % (94) |
| Tokens pro Frage | **9,6k** | 51,8k | 33,5k |
| Kosten pro Frage | **0,0033 $** | 0,0100 $ | 0,0046 $ |

Das sind 5,4-mal weniger Tokens als bei OpenClaw und 3,5-mal weniger als bei Hermes, zu einem Drittel
der Kosten pro Antwort von OpenClaw. Das Gedächtnis aus VOOL 0.7 erreichte bei denselben Fragen 123 von 150.
Jeder Lauf, der Aufbau und die Schwachstellen sind veröffentlicht: [Gedächtnis-Benchmarks](https://github.com/Parad0x-Labs/vool/tree/main/docs/benchmarks).

## Hier anfangen

| Du möchtest | Lies |
| --- | --- |
| VOOL zum Laufen bringen | [Installation](getting-started/install.md) |
| Den ersten Bildschirm verstehen | [Erster Start](getting-started/first-run.md) |
| Ein Modell anbinden | [Ein Modell verbinden](getting-started/connect-a-model.md) |
| Steuern, welche Ausgaben möglich sind | [Ausgabenlimits](guides/spending-limits.md) |
| Wissen, was deinen Rechner verlässt | [Lokal und Cloud](concepts/local-and-cloud.md) |

## Was VOOL ist

* **Eine Desktop-App.** Sie wird auf deinem Computer installiert — kein Browser-Tab, kein
  gehosteter Dienst.
* **Funktioniert offline für Chats und Dateien.** Mit einem ausgewählten lokalen Modell brauchen deine Chats und Dateien
  keine Internetverbindung. Aktuelle Fakten wie Wetter oder Preise kommen nur von öffentlichen Webdiensten, solange Websuchen eingeschaltet ist (bei einer frischen Installation ist es aus), und nie in Nur lokal.
* **Standardmäßig lokal.** Mit einem ausgewählten lokalen Modell werden Prompts und Dateien
  auf deinem Rechner verarbeitet.
* **Modellunabhängig.** Lokale Laufzeitumgebungen und Cloud-Anbieter sind beide auswählbar. Den
  Cloud-Schlüssel bringst du mit; er bleibt auf deinem Rechner.
* **Nachvollziehbar.** Jeder Werkzeugaufruf wird mit seinen Eingaben und Ergebnissen protokolliert, sodass
  sich ein Lauf im Nachhinein nachlesen lässt.

{% hint style="warning" %}
VOOL ist in der Beta für Macs mit Apple Silicon, Intel-Macs und Linux x86_64. Die Mac-Apps
sind noch nicht von Apple notariell beglaubigt, daher bittet macOS dich beim ersten Öffnen um eine
Bestätigung; die Seite [Installation](getting-started/install.md#erstes-öffnen-auf-dem-mac) zeigt, wie das geht.
Lies vor der Installation den [Release-Status](trust/release-status.md).
{% endhint %}

## Zwei Wege zu antworten, klar benannt

VOOL antwortet auf zwei Wegen, und die Kopfzeile des Chats zeigt immer, auf welchem Weg die nächste Anfrage
läuft — zum Beispiel **Auto** mit **Lokal zuerst**:

1. **Lokal** — die Arbeit bleibt auf diesem Computer.
2. **Cloud** — der ausgewählte Kontext wird direkt an den von dir ausgewählten Anbieter gesendet,
   von deinem Rechner aus, mit deinem Schlüssel.

Es gibt kein von Parad0x betriebenes Relay dazwischen. Die Details
stehen unter [Umgang mit Daten](trust/data-handling.md).

## Hilfe bekommen

* [FAQ](help/faq.md) — die häufigsten Fragen
* [Fehlerbehebung](help/troubleshooting.md) — wenn etwas nicht startet
* [Discord](https://discord.gg/V9NkjP3Fzz) — frag das Team
