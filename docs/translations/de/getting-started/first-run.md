---
description: Was der KI-Assistent VOOL beim ersten Öffnen fragt und was er nicht tut.
translation: machine
source_sha256: cedf20410d47a2d277e5522bb3e55169854a91a267d71193a762e072e7f8e47e
---

# Erster Start

Beim ersten Start stellt VOOL vier kurze Fragen. Du kannst jede davon überspringen und jede Antwort
später ändern. Während du antwortest, wird nichts irgendwohin gesendet, außer der Prüfung eines
Cloud-Schlüssels bei seinem Anbieter, falls du einen hinzufügst.

## Was passiert

1. **Wo soll er denken?** Auf diesem Computer mit einem lokalen Modell oder online mit einem Schlüssel
   von einem Anbieter, den du bezahlst. Siehe [Ein Modell verbinden](connect-a-model.md).
2. **Ordner wählen.** Der Projektordner, in dem VOOL arbeitet. Siehe
   [Arbeitsbereiche](../concepts/workspaces.md).
3. **Was darf er allein?** Wie oft VOOL anhält und dich fragt, bevor er etwas ändert. Siehe
   [Werkzeuge](../concepts/tools.md).
4. **Wie soll er dich nennen?** Optional. Ein Vorname oder Spitzname reicht, und er bleibt auf
   diesem Computer.

Die Modusanzeige zeigt danach Lokal, Auto oder den Namen des Cloud-Anbieters und entspricht dem
gerade ausgewählten Modell.

**Ein lokales Modell einmal zertifizieren.** Bevor ein lokales Modell antworten kann, öffne
**Einstellungen → Modelle & Anbieter → Lokale Modelle** und drücke dafür **Certify**. Das führt eine
kurze Prüfung auf deinem Rechner aus.

## Was VOOL beim ersten Start nicht tut

* Er legt kein Konto an.
* Er verlangt keinen Cloud-Schlüssel. Ein lokales Modell reicht, um die App zu nutzen.
* Er indiziert nicht dein Laufwerk. Er liest Dateien nur, wenn eine Anfrage sie braucht; siehe
  [Arbeitsbereiche](../concepts/workspaces.md) für die Ordner, die er erreichen kann.

{% hint style="info" %}
Du kannst jederzeit mit **+ Projekt** in der Chat-Seitenleiste einen anderen Projektordner wählen.
{% endhint %}

## Weiter

Weiter geht es mit [Ein Modell verbinden](connect-a-model.md).
