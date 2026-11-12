---
description: Wie sich VOOL selbst aktualisiert, was mit deinen Daten passiert und wie es um die Kompatibilität zwischen Versionen steht.
translation: machine
source_sha256: 3b6a950556f4669f116dfa3a911ffb5e07ad8bcfa04415d68cf5a3d1adf0fe65
---

# Updates und Migration

## So funktionieren Updates

Ab 0.7.5 aktualisiert sich VOOL selbst: Ist „Von selbst online gehen“ eingeschaltet, zeigt VOOL einen Hinweis und installiert eine neue Version mit einem Klick. Update von 0.7.4 oder 0.7.3 auf einem Mac: Schalte „Von selbst online gehen“ ein (Einstellungen > Privatsphäre & Berechtigungen) und drücke einmal auf „Nach Updates suchen“. Von 0.7.4 oder älter unter Linux oder von 0.7.2 und älter auf einem Mac: Lade 0.7.7 herunter und installiere es über deine aktuelle Version. Deine Chats und Einstellungen liegen in deinem Profil, außerhalb der App.

Ist „Von selbst online gehen“ eingeschaltet, sucht VOOL beim Start und einmal am Tag nach einer neuen
Version und zeigt einen Hinweis, ohne dass du auf „Nach Updates suchen“ drücken musst. Ist es ausgeschaltet,
ruft VOOL nichts ab, und in der Kopfzeile steht **Update-Suche aus**.

Ein signiertes Update-Paket durchläuft einen geprüften Ablauf mit automatischem Rollback:

1. **Herunterladen** — geprüft anhand eines signierten Manifests.
2. **Vorbereitete Installation** — die neue Version wird neben der alten installiert.
3. **Migration** — deine Daten werden auf die neue Version des Datenspeichers migriert. Schlägt die Migration
   fehl, werden deine Daten auf die vorherige Version zurückgesetzt, und die App startet wieder mit ihr:
   *„Deine Daten wurden auf die vorherige Version zurückgesetzt.“*
4. **Zustandsprüfung** — die neue Version muss beweisen, dass sie funktioniert, bevor die alte außer Dienst
   gestellt wird.
5. **Wechsel** — atomar; die vorherige Version wird als Ziel für ein Rollback aufbewahrt.

Unter Linux aktualisiert sich die Tarball-Installation auf dieselbe Weise. Die neue Version wird neben der
laufenden installiert, und `current` wechselt in einem Schritt zu ihr. Ist die neue Version nicht
funktionsfähig, wechselt VOOL von selbst zur vorherigen zurück. Ein manuelles **Roll back** wird auf dem Mac
und für Installationen aus dem Quellcode angeboten, für die Linux-Tarball-Installation aber noch nicht.

Schlägt eine Stufe fehl, bleibt die App, wie sie war — die Fehlermeldung nennt die Stufe
(`verification_failed`, `insufficient_disk_space`, …), und jede Stufe hat ihren eigenen Satz dazu, wie es
weitergeht.

## Datenkompatibilität

* Dein Profil, deine Chats, Belege und Einstellungen liegen außerhalb des App-Pakets, daher ersetzt ein
  Austausch der App nie deine Daten.
* Der lokale Speicher verweigert das Öffnen nach einem **Downgrade**: Eine Datenbank, die ein neueres VOOL
  geschrieben hat, wird von einem älteren nicht stillschweigend gelesen — es sagt das und verweigert, weil
  Raten bei einem unbekannten Speicheraufbau den Speicher beschädigen würde. Installiere die neuere Version
  erneut, um neue Daten zu lesen.
* Durch Updates installierte Dateien und der Update-Zustand selbst werden mit Belegen in deinem Profil
  festgehalten, sodass sich „welche Version hat meine Daten angefasst“ im Nachhinein beantworten lässt.

## Beta-Kanal

Die aktuelle Release-Linie ist eine **Beta** (siehe [Release-Status](../trust/release-status.md)).
Updates sind mit dem fest hinterlegten Schlüssel des Herausgebers signiert und werden vor der Installation
geprüft. Die macOS-App selbst ist ad-hoc signiert und nicht von Apple notariell beglaubigt, daher bittet macOS
dich womöglich, den ersten Start einer neuen Version zu bestätigen.
