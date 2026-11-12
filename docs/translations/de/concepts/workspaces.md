---
description: Ein Arbeitsbereich ist der Projektordner, in dem VOOL arbeitet. Außerhalb davon kann VOOL die sichtbaren Ordner in deinem persönlichen Ordner lesen und Dateien nur in „Schreibtisch“, „Downloads“ und „Dokumente“ ändern.
translation: machine
source_sha256: 33b648a0a55f99b1c96365553942911862672eafa55e94bd45f18ce566d5fcea
---

# Arbeitsbereiche

Ein Arbeitsbereich ist der Projektordner, in dem ein Chat arbeitet. VOOL liest und ändert dort Dateien
und fragt vorher nach, wann immer dein Betriebsmodus das verlangt (siehe [Werkzeuge](tools.md)).

## Hinzufügen und entfernen

Wähle einen Ordner mit **Projektordner wählen…** im Chat oder mit **+ Projekt** in der
Chat-Seitenleiste. Wenn du ein Projekt löschst, vergisst VOOL, was er darin gelernt hat, und verschiebt
dessen Chats zurück nach „Allgemein“. Deine Dateien bleiben unberührt.

## Deine anderen Ordner

Außerhalb des Arbeitsbereichs erreichen die Dateiwerkzeuge von VOOL auch deinen persönlichen Ordner, mit festen Grenzen:

| Ordner | Was VOOL tun kann |
| --- | --- |
| Schreibtisch, Downloads, Dokumente | Lesen und Dateien ändern, mit Nachfrage, wie es dein Betriebsmodus vorsieht |
| Bilder, Filme (unter Linux „Videos“), Musik | Nur lesen: auflisten, finden, öffnen und Größen ermitteln, nie ändern |
| Deine anderen sichtbaren Ordner im persönlichen Ordner, etwa `~/Projects` | Nur lesen |
| Versteckte Ordner (etwa `~/.ssh`), App- und Systemdaten (`Library`, `Applications`, `AppData`), der eigene Datenordner von VOOL | Nie |

VOOL sieht in diesen Ordnern nur nach, wenn eine Anfrage es braucht, zum Beispiel „finde meine Steuer-PDF“ oder
„welches Video in Filme ist das größte“. Er indiziert dein Laufwerk nicht im Hintergrund.

## Tipps zum Eingrenzen

* Richte einen Arbeitsbereich auf ein Projekt aus, nicht auf deinen persönlichen Ordner.
* Bewahre Dateien mit Zugangsdaten, Schlüsselmaterial und Passwortspeicher in einem Passwortmanager oder einem
  versteckten Ordner auf, nicht lose in „Schreibtisch“, „Downloads“, „Dokumente“ oder einem Projektordner.
* Nutze für jede Arbeit ein eigenes Projekt, damit Kontext aus der einen nicht in eine andere gelangt.

{% hint style="warning" %}
Alles, was VOOL liest, im Projektordner oder in deinem persönlichen Ordner, kann Teil dessen sein, was
an deinen Anbieter gesendet wird, wenn ein Cloud-Modell ausgewählt ist. Grenze den Zugriff bewusst ein.
{% endhint %}
