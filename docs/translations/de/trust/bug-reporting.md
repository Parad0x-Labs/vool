---
description: Einen Fehler sicher melden — geschwärzte Entwürfe, Freigabe der genauen Bytes und was deinen Rechner nie verlässt.
translation: machine
source_sha256: 4e64e6313c1f6a8be6784a1f35cedc87b81a5661fdb548c6821109f935db818f
---

# Fehlerberichte und Datenschutz

## Der eingebaute Fehlermelder

Nutze den Fehlermelder in der App (oder die CLI `python -m apps.vool_cli bug-report`). Er sendet nie von sich
aus etwas — der Ablauf hat bewusst drei Stufen:

1. **Entwurf** — ein Bericht wird lokal aus aktuellen Protokollen und Diagnosedaten erstellt und dabei
   geschwärzt. *„Noch wird nichts gesendet — zuerst wird ein lokaler Entwurf erstellt.“*
2. **Vorschau** — du siehst die **genauen Bytes**, die gesendet würden, mit einem Hash der Nutzdaten und einer
   Zusammenfassung der Schwärzung, die auflistet, was entfernt wurde. Felder, die du nicht senden möchtest
   (Protokolle, Flags, Notizen zum Nachstellen, Anhänge), lassen sich entfernen; jede Änderung setzt die
   Freigabe zurück.
3. **Freigeben** — Senden verlangt deine ausdrückliche Freigabe genau dieser Bytes. Ändern sich die
   Nutzdaten, bindet die Freigabe nicht mehr, und du musst erneut freigeben.

Nachdem du freigegeben hast, öffnet **Open on GitHub** in deinem Browser eine ausgefüllte Berichtsseite im
öffentlichen Repository [vool-reports](https://github.com/Parad0x-Labs/vool-reports). Lies sie dort noch
einmal und drücke auf Submit. VOOL selbst sendet nichts, und es ist kein GitHub-Token nötig.
**Copy report** und **Save a local copy** brauchen überhaupt keine Freigabe: Sie geben dir dieselben
bereinigten Bytes, und nichts verlässt deinen Rechner. **My reports** listet deine Entwürfe auf und auf
welchem Weg jeder davon hinausging.

## Eine falsche Antwort melden

Drücke unter einer beliebigen Antwort auf **Melden** oder 👎, oder markiere den falschen Teil und drücke auf
**Melden**. Der Bericht zitiert nur diesen Teil (oder die ganze Antwort). Deine Frage wird weggelassen, außer
du setzt das Häkchen im Kästchen. Der Beleg der Antwort ist enthalten: Modell, Anbieter, Tokens, Zeit und
welche Schritte liefen, nie deren Inhalt. Eine Antwort, die VOOL auf deinen Wunsch vergessen soll, kann nicht
gemeldet werden.

## Was zur Untersuchung hinzugefügt wird

Die Eckdaten deines Computers: Betriebssystemversion, Chip, Arbeitsspeicher, GPU und die Version des lokalen
Modellservers. Außerdem eine kurze Zustandsprüfung: ob der lokale Modellserver antwortet, und der freie
Speicherplatz als Bereich. Nie der Name deines Computers, dein Benutzername, Seriennummern oder
Netzwerkadressen. Du kannst alles davon in der Vorschau entfernen.

## Was die Schwärzung entfernt

Deterministisch, bevor du den Entwurf überhaupt siehst: Zugangsdaten und Auth-Header, Pfade im persönlichen
Ordner und Benutzernamen, der Name deines Computers, E-Mail-Adressen, IP-Adressen, PINs und Einmalcodes,
Karten- und Kontonummern (IBAN), Telefonnummern, Wallet-Adressen, die persönlichen Angaben in deinem Profil,
gekennzeichnete Geheimnisse und Datenblöcke mit hoher Entropie — zusätzlich abgesichert durch einen
abschließenden Durchgang, der nach Geheimnissen sucht.
VOOL kann Namen oder private Fakten, die in normalen Sätzen getippt wurden, nicht erkennen, lies also die Vorschau. Stacktraces werden anhand einer Positivliste rekonstruiert, nicht roh eingefügt.
Fehlernachweise in Berichten enthalten **nur typisierte Codes** — nie internen Kontext oder Ursachenketten.

Jeder entfernte Wert wird durch eine Markierung ersetzt. Eine E-Mail-Adresse, eine Person, ein persönlicher
Ordner, eine IP-Adresse, eine Telefonnummer, eine Wallet-Adresse, der Name deines Computers oder eine Angabe
aus deinem Profil behält im ganzen Bericht eine nummerierte Markierung, etwa `[EMAIL_1]` und `[EMAIL_2]` für
zwei verschiedene Adressen, damit der Bericht gut lesbar bleibt.
Jeder entfernte Schlüssel, jedes Passwort, jede PIN, Karten- oder Kontonummer erscheint als
`[SECRET_REMOVED]`, nie nummeriert. Die Liste, die eine Markierung mit dem echten Wert verknüpft, wird nur im
Arbeitsspeicher von VOOL gehalten, solange du an dem Bericht arbeitest, sodass eine Änderung dieselben
Markierungen behält. Sie wird nie gespeichert oder gesendet und wird vergessen, wenn du den Bericht löschst,
nach einer Stunde ohne Nutzung oder wenn VOOL neu startet.

## Was VOOL nie tut

* Keinerlei automatisches Melden nach außen.
* Keine Telemetrie-Pipeline, die hinter einer Einstellung wartet.
* Sicherheitsprobleme laufen über den vertraulichen Kanal in der
  [Sicherheitsrichtlinie](https://github.com/Parad0x-Labs/vool/security/policy) des Repositorys — nie über
  ein öffentliches Issue.

## Zusammenfassung zum Datenschutz

* Ist ein lokales Modell ausgewählt, werden Prompts und Dateien auf deinem Rechner verarbeitet.
* Cloud-Aufrufe verwenden den API-Schlüssel, den du angegeben hast und der lokal gespeichert ist; Schlüssel
  gelangen nie in Berichte.
* Die vollständigen Details zum Datenfluss stehen unter [Umgang mit Daten](../trust/data-handling.md).
