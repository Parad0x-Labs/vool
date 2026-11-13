---
translation: machine
source_sha256: 021137e6424e28cf4af2913b10198aa3e59fb7d63a002949eeea0837375436a8
---
# Add-on-Prüfungen

Der Bereich Zusatzmodule hält Durchsuchen, Prüfen und Installieren getrennt. Ein Scan beschreibt bestimmte Bytes;
er ist keine Sicherheitsgarantie, keine Empfehlung des Herausgebers, keine Laufzeitberechtigung und keine Freigabe der Wallet.

„Durchsuchen“ listet zuerst die eigenen Skills von VOOL, darunter 75 mit der App gebündelte VOOL-Skills (22 davon
nutzen VOOL-Werkzeuge, die nur eingrenzen, was VOOL ohnehin anbietet), dann die geprüften Add-ons des Katalogs. „Fertigkeiten-Codex“ listet zuerst die Skills des VOOL-Teams, vor der
Auswahl der Redaktion und vorgestellten Skills.

## Von VOOL gemachte Skills: die lokale Prüfung

Die 75 von VOOL gemachten Skills werden in der App mitgeliefert, jeder per SHA-256 im eigenen Verzeichnis von VOOL festgelegt
und bei der Veröffentlichung von VOOL geprüft. Für diese führt **Diesen Skill hinzufügen** nur die lokale Prüfung von VOOL aus: kein Schlüssel,
kein Scan-Guthaben und keine Netzwerkanfrage.

1. VOOL liest den Skill und die Lizenz aus der eigenen Kopie der App und lehnt sie ab, wenn ihr SHA-256
   nicht mit dem festgelegten Wert übereinstimmt (*Die Datei dieser VOOL-Fertigkeit stimmt nicht mit der Kopie überein, die mit VOOL kam, deshalb wurde sie nicht hinzugefügt.*).
2. Die lokale Prüfung von VOOL liest den Skill auf riskante Anweisungen hin und zeigt das Ergebnis. Ein sauberes Ergebnis
   kannst du mit **Hinzufügen und einschalten** hinzufügen; alles, was sie markiert, braucht dein gesondertes, ausdrückliches OK für
   genau diese Version. **Ablehnen** verwirft es.
3. Die Installation folgt dann demselben Lebenszyklus wie bei anderen Skills: Die exakten Dateien werden installiert,
   verifiziert und aktiviert. Der installierte Skill wird bei jedem Laden gegen seinen festgelegten SHA-256 geprüft;
   eine geänderte Datei macht ihn nicht verfügbar.

Die lokale Prüfung ist ein statisches Lesen fester Bytes, keine Garantie für Sicherheit oder Antwortqualität.
Skills anderer Hersteller nehmen nie diesen Weg.

## Öffentliche Skills

Wähle einen öffentlichen GitHub-Skill in einer festen Revision und erlaube, seinen Inhalt herunterzuladen. VOOL prüft
ihn auf diesem Gerät. Ohne aktiviertes Scanner-Plugin erklärt die Seite,
dass die Prüfungen auf diesem Gerät geblieben sind. Für diesen Weg wird kein Scanner-Schlüssel gebraucht.

Ein aktiviertes Scanner-Plugin erhält den Inhalt des öffentlichen Skills ebenfalls, wenn ein Scan angefordert wird. Das
kann Guthaben seines Dienstes verbrauchen. Lokale Chats, private Dateien im Arbeitsbereich und andere Zugangsdaten werden
nicht übermittelt. Das Speichern eines Schlüssels und das Öffnen von Zusatzmodule lösen weder einen Verbindungstest noch eine Scan-Anfrage aus. Es
gibt keine automatischen Wiederholungen von Anfragen.

Lies die Befunde und akzeptiere die Installation getrennt davon. Die Validierung eines Berichts erfordert vollständige Abdeckung
und gültige Befunde. Ein bestandener Bericht hat keine Befunde und keine Richtlinienverstöße. Eine gesondert angebotene
Risikoakzeptanz bindet die geprüfte Version; sie ändert nie den Bericht und gewährt nie mehr Zugriff.
Abgelaufene Prüfungen, veränderte bereitgestellte Bytes, Links und kollidierende Zielordner werden abgelehnt.
Belege binden die vollständige installierte Distribution. Geänderte Dateien entziehen die Verfügbarkeit zur Laufzeit.
Skill-Anleitungen werden vollständig eingefügt oder ausdrücklich ausgelassen, wenn sie nicht ins Prompt-Budget passen.

## Optionale Scanner

Ein entfernter Scanner ist optional. Ist kein Scanner-Plugin aktiviert, entscheidet die eigene Prüfung von VOOL auf diesem Gerät,
ob ein Skill installiert werden kann; frühere Versionen verlangten stattdessen einen entfernten Scan.

Um einen hinzuzufügen, installiere ein Scanner-Plugin seines Herstellers über den Bereich Plug-ins, speichere deinen eigenen
Schlüssel, prüfe den Zugriff und aktiviere es. Seine Erklärung nennt einen Pfad für einen ausdrücklichen Verbindungstest
und einen Pfad für angeforderte Scans, und der gemeinsame native Plugin-Transport stellt diese Anfragen.
VOOL hat keinen eingebauten Scanner-Client und keine zentrale Einstellung für einen Scanner-Schlüssel und bietet kein eigenes
Scanner-Plugin an. Ein gespeicherter Eyebrow-Schlüssel aus einer älteren VOOL-Version bleibt auf diesem Computer und wird nicht verwendet. Die
IDs `vool-plugin-eyebrow` und `vool-plugin-usepod` sind reserviert; ein anderes Paket, das eine davon beansprucht,
wird vor der Prüfung abgelehnt.

Die eigene Prüfung von VOOL auf diesem Gerät läuft immer. Der Bericht eines Scanner-Plugins kommt daneben hinzu:
Findet einer von beiden ein Problem, blockiert das die Installation, und ein Plugin-Bericht muss genau den
geprüften Inhalt benennen. Ein Scanner-Plugin kann eine Installation nie allein freigeben.

Ein aktivierter Scanner mit fehlendem oder abgelehntem Schlüssel schlägt deutlich sichtbar fehl. VOOL ersetzt
diesen angeforderten entfernten Scan nie stillschweigend durch ein bestandenes lokales Ergebnis. Zeitüberschreitungen, Dienstfehler, Weiterleitungen,
übergroße Antworten und unvollständige Berichte lassen die Installation unakzeptiert. Eine Anfrage mit Zeitüberschreitung
kann trotzdem Guthaben des Dienstes verbraucht haben.

## Ausführbare Pakete und Store-Prüfung

Plugins nutzen eine vollständige Paketprüfung eines lokalen Ordners oder eines festgelegten öffentlichen GitHub-Pakets, getrennt vom
reinen Text-Importer für Skills. Die Installation führt keinen Paketcode aus und lässt ihn deaktiviert. Store-Plugins
erfordern außerdem den bestehenden signierten Beleg und den Digest der vollständigen Distribution. Das Aktivieren erfordert ein
gesondertes Zugriffsblatt, das Werkzeuge, Wirkungen, genaue Hosts, Plätze für Zugangsdaten und mitgelieferte Server abdeckt.

Ausführbarer Code erhält beim Aufruf nur seine eigenen erklärten Schlüssel. Er kann weder die Plätze eines anderen
Plugins noch die kostenpflichtigen Modellschlüssel des Besitzers lesen und hat keine Befugnis über Signieren, Limits, Freigaben oder Belege der Wallet.
Seine Ausgabe bleibt nicht vertrauenswürdig. Geänderte Bytes entziehen die Zustimmung; Deaktivieren stoppt Aufrufe. Nicht unterstützte
Isolierung verweigert die Ausführung. Zu den Laufzeitgrenzen siehe [Plugins verfassen](../../../guides/plugin-authoring.md).

Wenn VOOL seine Plugin-Einträge nicht lesen kann, bieten Pakete, zu denen es keinen Eintrag hat, keine Skills mehr an, außer
den mit VOOL ausgelieferten, und die beschädigte Datei wird neben der neuen aufbewahrt. Wenn es deine Ein- und
Aus-Einstellungen nicht lesen kann, gilt jedes Plugin als aus, bis du eines wieder einschaltest.

## Lokale API und Transport

`GET /api/addons` liest den Bestand. Änderungen über `POST /api/addons` erfordern lokalen Zugriff durch den Besitzer,
JSON, Browser-Anfragen vom selben Ursprung und vorgangsspezifische Felder. Die Zugänge zum Ein- und Ausschalten und für den Lebenszyklus von Plugins
(`POST /api/plugins/enable`, `POST /api/plugins/lifecycle`) wenden dieselbe Regel für JSON und
denselben Ursprung an. Downloads öffentlicher Quellen nutzen
exakte Revisionen und begrenzte, verifizierte Blobs. Native Dienste erfordern erklärte öffentliche HTTPS-Hosts,
validieren TLS, legen aufgelöste öffentliche Adressen fest, lehnen Weiterleitungen ab, begrenzen Antworten und stoppen, wenn
das Plugin deaktiviert wird.

Lokale simulierte HTTPS-Dienste können Transport und Fehlerbehandlung ohne kostenpflichtige Aufrufe prüfen. Sie belegen
weder die Live-Zuverlässigkeit des Dienstes noch die Abrechnung, die Kompetenz des Modells oder die Installation in anderen
Anwendungen. Dafür ist eine gesonderte Verifizierung nötig.
