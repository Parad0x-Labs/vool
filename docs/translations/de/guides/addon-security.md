---
translation: machine
source_sha256: 671f147e5d9649b6c2b7c2d391ea085120e783a8ace29f92ac8c3d5ecb4407eb
---
# Skills entdecken und Eyebrow-Prüfungen

Die Seite „Skills & Erweiterungen“ hat vier Tabs: **Durchsuchen**, **Fertigkeiten-Codex**, **Meine Add-ons** und **Sicherheit**. „Durchsuchen“ listet zuerst die eigenen Skills von VOOL, darunter 53 reine Text-Skills von VOOL, die mit der App gebündelt sind, dann die geprüften Add-ons des Katalogs, und kann ein öffentliches GitHub-Repository in einer festgelegten Revision abrufen. „Fertigkeiten-Codex“ listet zuerst die Skills des VOOL-Teams, vor der Auswahl der Redaktion und vorgestellten Skills. Die Suche ignoriert Groß- und Kleinschreibung und übliche Trennzeichen; Steuerelemente für Kategorie und Sortierung grenzen die Liste ein. Quelle, Lizenz, Revision und Voraussetzungen sind vor dem Herunterladen sichtbar.

## Erste Nutzung

1. Speichere einen Eyebrow-API-Schlüssel im Tab „Sicherheit“, der auch unter Einstellungen → API-Schlüssel erreichbar ist. Der bestehende Zugangsdatenspeicher verwaltet den Schlüssel. Das Speichern testet weder die Verbindung, noch scannt es etwas; „Verbindung testen“ kontaktiert Eyebrow ausdrücklich.
2. Wähle in „Durchsuchen“ einen Skill und erlaube, seinen öffentlichen Inhalt herunterzuladen und diesen Inhalt an Eyebrow zu senden. Das verbraucht Scan-Guthaben des Dienstes gemäß deinem Tarif. Es gibt keine automatischen Wiederholungen.
3. Lies den zurückgegebenen Bericht. Eine erfolgreiche HTTP-Antwort allein ist kein sauberes Ergebnis. Dieser erste Katalog erlaubt die Installation nur mit einem vollständigen `pass`-Bericht, ohne Befunde und ohne Richtlinienverstöße. „Ablehnen“ verwirft den bereitgestellten Download, ohne ihn zu installieren.
4. Akzeptiere die Installation separat. VOOL bewahrt die Lizenz, installiert über seinen bestehenden Plugin-Lebenszyklus, verifiziert genau das geprüfte Paket und aktiviert den Skill. Bestehende Berechtigungen regeln weiterhin jede Aktion, die er vorschlagen könnte.

Ein Scan ist die Einschätzung bestimmter Bytes durch einen Drittanbieter, keine Garantie für Sicherheit, Kompatibilität oder Antwortqualität. Die Aufnahme in den Katalog ist keine Zertifizierung des Herausgebers.

## Grenzen

Inhalte kommen nur aus öffentlichen GitHub-Repositorys in einer festen, 40-stelligen Revision, mit per SHA-256 festgelegten Skill- und Lizenzdateien. In dieser Version gibt es keine privaten Repositorys, kein Entpacken von Archiven, kein Installationsskript und keinen Importer für ausführbare oder MCP-Plugins. Nur die eigenständige SKILL.md wird an Eyebrow gesendet; lokale Chats, Dateien im Arbeitsbereich, Zugangsdaten und Lizenzinhalte werden nicht zum Scannen übermittelt.

Downloads bleiben wirkungslos, bis sie geprüft und akzeptiert sind. Lokal authentifizierte Belege binden den Bericht an den festgelegten Inhalt. Die Installation lehnt abgelaufene Prüfungen (24 Stunden), veränderte bereitgestellte Dateien und bereits vorhandene Zielverzeichnisse ab. Die Laufzeitumgebung prüft den Lebenszykluszustand und die geprüften Bytes, bevor sie einen verwalteten Skill in den Modellkontext aufnimmt. Hinzugefügte Dateien, Symlinks oder geänderte Inhalte machen ihn nicht verfügbar. Anleitungen werden vollständig eingefügt oder ausdrücklich als wegen unzureichenden Prompt-Budgets ausgelassen verzeichnet; die gescannten Anweisungen werden nicht stillschweigend gekürzt.

Diese Prüfungen isolieren keinen bösartigen Code, der bereits als Benutzer des Betriebssystems läuft. Sie erstrecken sich nicht auf bereits vorhandene, manuell installierte Plugins. Ein Scan-Beleg gewährt keine neue Ausführungsberechtigung und keine Befugnis über die Wallet.

## API und Fehlerbehandlung

`GET /api/addons` gibt den Katalog und den lokalen Status zurück, ohne Verbindungstest oder Scan. `POST /api/addons` unterstützt `save_key`, `remove_key`, `test_key`, `scan`, `install` und `reject`. Änderungen erfordern Loopback, JSON und Browser-Anfragen vom selben Ursprung; nur vorgangsspezifische Felder werden akzeptiert. Vom Aufrufer gelieferte Quell-URLs und Pfade werden abgelehnt.

Der Client nutzt die Inline-Anfrage mit Skill-Quelle von Eyebrow v1 unter `/v1/scan`, mit Bearer-Authentifizierung, einem festen HTTPS-Ursprung, ohne Weiterleitungen, mit begrenzter Antwortgröße und ohne automatische Wiederholungen. `/v1/version` dient dem ausdrücklichen Verbindungstest. API-Referenz: https://eyebrow.cc/api/reference .

Typische typisierte Fehler sind:

| Code | Bedeutung / Abhilfe |
| --- | --- |
| `eyebrow_key_missing` / `eyebrow_unauthorized` | Speichere oder ersetze den Schlüssel. |
| `eyebrow_quota` | Prüfe das Guthaben des Dienstes, bevor du es erneut versuchst. |
| `eyebrow_timeout` / `eyebrow_unreachable` | Keine automatische Wiederholung; ein Scan mit Zeitüberschreitung kann trotzdem Guthaben verbrauchen. |
| `eyebrow_invalid_report` / `eyebrow_no_coverage` | Der Nachweis ist unvollständig; nichts wird aktiviert. |
| `addon_source_changed` / `addon_changed` | Festgelegte oder geprüfte Bytes weichen ab; aktiviere sie nicht. |
| `review_untrusted` / `review_expired` | Hol vor der Installation eine neue Prüfung ein. |
| `addon_already_installed` | Vorhandene Dateien wurden bewahrt. Automatisches Ersetzen ist nicht implementiert. |

## Aktuelle Einschränkungen und Verifizierung

Es gibt kein automatisches Update, keinen Importer für beliebige Kataloge und keinen Ablauf zum Reparieren oder Neuinstallieren an Ort und Stelle für ein bestehendes, verändertes Paket. Die Wiederherstellung ausstehender Prüfungen nach einem Neustart der App ist in der Oberfläche nicht verfügbar. Neue Texte der Oberfläche fallen derzeit auf Englisch zurück; vollständige Übersetzungen werden nicht behauptet.

Automatisierte Tests prüfen den echten Lebenszyklus, das Laden von Skills, die API-Handler und die ausgelieferte Browser-Oberfläche mit ausdrücklich simulierten öffentlichen Downloads und Eyebrow-Antworten. Sie belegen das Verhalten von Laufzeitumgebung und Oberfläche, nicht die Live-Verfügbarkeit von Eyebrow oder die Kompetenz des Modells. Ein echter Scan mit Schlüssel und die Abnahme der nativen, paketierten App bleiben Release-Prüfungen. Zum Ausführen dieser Tests wird weder ein Schlüssel noch ein Aufruf eines kostenpflichtigen Dienstes gebraucht.
