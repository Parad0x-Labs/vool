---
description: VOOL mit Skills und Plugins erweitern.
translation: machine
source_sha256: a86860b8624e3c97c81e813ec92e3998c687a87d7bc43b7960c489cdcd309196
---

# Skills und Plugins

Ein **Skill** ist ein gebündelter Satz von Anweisungen für eine wiederkehrende Aufgabe. Ein **Plugin** fügt der
Laufzeitumgebung neue Werkzeuge hinzu.

## Mitgeliefert oder extern installiert

VOOL wird mit einer **nativen Skill-Bibliothek** ausgeliefert — den Skills, die mit der App selbst kommen — und
in aktuellen Builds mit dem Plugin **VOOL Database**. Du kannst auch eigene Skills und Plugins installieren.

| | Mitgeliefert (kommt mit VOOL) | Extern installiert |
| --- | --- | --- |
| Wo es liegt | In der App | Dein Plugin-Ordner (in den Einstellungen festgelegt) |
| Updates | Mit der App | Wenn du das Paket aktualisierst |
| Kann deaktiviert werden | Ja — einzeln | Ja — einzeln |
| Gewährt Berechtigungen | **Nein** | **Nein** |

Zwei Regeln gelten für beide Quellen:

* **Skills lehren; sie gewähren nie etwas.** Die erklärten Werkzeuge eines Skills sind ein *einschränkendes*
  Signal (das Werkzeugbudget des Workflows), nie die Gewährung von irgendetwas. Die Berechtigungssteuerung liest
  keine Skills.
* **Ein einziger Bestand.** Der Katalog, den die Konsole zeigt, und die Pakete, die die Laufzeitumgebung lädt,
  sind dieselbe Liste. Dass ein Skill in einer Liste erscheint, beweist nicht, dass er geladen werden kann — jeder
  Eintrag trägt seinen aktuellen Zustand (verfügbar, deaktiviert, Voraussetzung fehlt, ungültiger Vertrag) und den
  Grund.

## Skills finden

„Skills & Erweiterungen“ listet zuerst die eigenen Skills von VOOL. **Durchsuchen** bringt 53 fertige
VOOL-Skills mit. Sie bestehen nur aus Text: Jeder ist ein Satz von Anweisungen, mit der App gebündelt, daher
listet „Durchsuchen“ sie ohne Online-Abfrage. VOOL-Skills, die VOOL-Werkzeuge nutzen, stehen im Tab
**Fertigkeiten-Codex**, wo die Skills des VOOL-Teams zuerst kommen. Prüfe einen Skill, bevor du ihn aktivierst.

## Verfügbarkeitszustände

Ein Skill kann mit einem typisierten Grund als nicht verfügbar erscheinen: eine fehlende Voraussetzung (zum
Beispiel braucht der Datenbank-Skill das aktivierte Datenbank-Plugin), eine auf diesem Rechner nicht verfügbare
Fähigkeit, ein ungültiger Vertrag oder von dir deaktiviert. Gründe werden pro Eintrag angezeigt, und das
Deaktivieren gilt pro Eintrag und bleibt über Neustarts hinweg bestehen.

## Doppelte Identitäten

Wenn zwei Pakete dieselbe Plugin-Identität erklären (etwa ein mitgeliefertes Paket und eine installierte Kopie),
wird das mitgelieferte **deterministisch** behalten, und die verworfene Kopie wird im Katalog gemeldet — nie
stillschweigend überdeckt.

## Eigene installieren

Installiere aus dem Katalog in der App, oder richte VOOL auf einen eigenen Plugin-Ordner aus. Pakete bringen
ein Manifest (`plugin.json`) mit, das ihre Werkzeuge erklärt; ein Manifest, dessen Berechtigungsangaben nicht
zu seiner erklärten Verhaltensklasse passen, wird beim Laden abgelehnt.

{% hint style="danger" %}
Prüfe das gesamte Paket vor der Verifizierung. Eine Prüfung ist ein Nachweis über bestimmte Bytes,
kein Sicherheitszertifikat und keine Gewährung von Berechtigungen.
{% endhint %}

## Paketfreigabe in v0.7

Die Verifizierung umfasst jede Paketdatei und jedes Verzeichnis, einschließlich ausführbarer Handler,
mitgelieferter Bibliotheken, Lockdateien für Abhängigkeiten, Referenzen, Assets und Ausführungsrechte-Bits.
Jedes Hinzufügen, Entfernen, Umbenennen oder Ändern davon macht die Freigabe ungültig. Symlinks, Hardlinks,
Spezialdateien, privilegierte Modi, instabile Lesevorgänge und übergroße Distributionen werden abgelehnt.
Die aktuellen Erfassungsgrenzen sind 4.096 Einträge, 64 MiB pro Datei und 128 MiB pro Paket. Innerhalb
eines Pakets gibt es keine ignorierten Cache- oder Zustandsverzeichnisse.

Früher gespeicherte Freigaben, die nur Metadaten umfassen, aktivieren in dieser Version keine Pakete.
Prüfe die vollständige Distribution, verifiziere sie ausdrücklich erneut und aktiviere sie dann. Updates
bleiben deaktiviert, bis sie verifiziert und aktiviert sind; eine Freigabe wird nie stillschweigend übernommen.
Verwaltete Add-ons brauchen außerdem eine neue, authentifizierte Prüfung durch den Store, wenn ein älterer Beleg
nicht die vollständige Distribution bindet; eine gewöhnliche Verifizierung kann diesen Beleg nicht ersetzen.

`requires` nennt installierte Plugin-Abhängigkeiten. Die Verifizierung legt deren rekursive Freigabe-Digests
fest; jede Abhängigkeit muss bereits verifiziert und aktiviert sein. Eine geänderte, aktualisierte, deaktivierte
oder widerrufene Abhängigkeit macht die von ihr abhängigen Pakete nicht verfügbar, auch über zwischengespeicherte
Werkzeuge. Die Zulassung prüft nach dem Hashen den Aktivierungs- und Widerrufsstatus der gesamten erfassten Hülle
erneut. Eine Abhängigkeit erneut zu verifizieren, gibt ihre neuen Bytes nicht stillschweigend für das
übergeordnete Paket frei: Prüfe und verifiziere auch jedes übergeordnete Paket erneut. Jeder `approval_digest`
bindet seinen Paket-Digest und die Freigaben der Abhängigkeiten, sodass eine geänderte transitive Abhängigkeit
diese Freigabe ändert, selbst wenn die eigenen Dateien des übergeordneten Pakets identisch bleiben. Eine zyklische
Abhängigkeitsfreigabe kann nicht aktiviert werden. Die Abhängigkeitshülle ist auf 64 Pakete begrenzt.

Jeder registrierte Aufruf prüft den Lebenszykluszustand erneut und erfasst private, schreibgeschützte Kopien
des Pakets und seiner festgelegten Abhängigkeitshülle. Der Werkzeugvertrag muss zum erfassten Manifest passen.
Der registrierte Handler startet aus dieser Erfassung; wird die Quelle nach der Erfassung bearbeitet, kann das
weder diesen Handler noch seine mitgelieferten Dateien ersetzen. Ausführungsbeobachtungen enthalten Paket-,
rekursive Freigabe- und Abhängigkeits-Digests. Registrierte Pakete erfordern eine echte Isolierung durch den Kernel, selbst wenn ein
lokaler Low-Level-Aufrufer eine schwächere Übersteuerung gewählt hat.
Hosts ohne sichere No-Follow-Erfassung über Deskriptoren oder ohne Isolierung durch den Kernel lehnen
registrierte Pakete ab. Zeitlimits für Aufrufe sind auf 1–300 Sekunden begrenzt; die eigene Prozessgruppe des
Aufrufs wird gestoppt, bevor seine Ausführungskopie entfernt wird.

Bewahre dauerhafte Ausgaben, Caches und Logs in `PLUGIN_SCRATCH` auf, nicht neben ausführbarem Code.
Rein lesende Werkzeuge erhalten keine Schreiberlaubnis. Andere Werkzeuge dürfen in den Scratch-Bereich schreiben,
können aber ihre Ausführungskopie nicht verändern. `VOOL_PLUGIN_DEPENDENCIES` ist eine JSON-Zuordnung von
erklärten Abhängigkeitsidentitäten zu den erfassten Abhängigkeitsverzeichnissen. Bündle andere Bibliotheken von
Drittanbietern und ihre Lockdateien im Paket; eine Lockdatei allein bescheinigt keine externe Umgebung.
Der Interpreter des Hosts und die Systembibliotheken bleiben Teil der vertrauenswürdigen Plattform, statt als
geprüfte Paket-Bytes von Drittanbietern dargestellt zu werden. Diese Integritätsprüfung zertifiziert kein
Verhalten, bescheinigt keinen beliebigen Code, der von außerhalb der freigegebenen Distribution heruntergeladen
oder geladen wird, vermittelt nicht jede interne Wirkung und ersetzt nicht die native Freigabe der Wallet.

## Einen Skill schreiben

Ein Skill ist ein Ordner mit einer Anweisungsdatei und allen Dateien, auf die sie verweist:

```text
my-skill/
├── SKILL.md
└── templates/
```

Entwirf ihn in der App (`skill.create` legt ihn bereit, ohne etwas zu aktivieren), validiere ihn und installiere
ihn dann, um ihn zu aktivieren — mit Absicht drei getrennte Schritte, damit das Verfassen nie stillschweigend das
Verhalten ändert. Der Katalog und das Format zum Verfassen werden für die öffentliche Veröffentlichung noch
fertiggestellt.
