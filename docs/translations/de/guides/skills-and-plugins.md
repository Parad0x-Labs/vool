---
description: VOOL mit Skills und Plugins erweitern.
translation: machine
source_sha256: 4dd2a317a7b10ee024616f6e0c27bd73087c44334f29e2c5f60f69773f4e54e8
---

# Skills und Plugins

Ein **Skill** ist ein gebündelter Satz von Anweisungen für eine wiederkehrende Aufgabe. Ein **Plugin** fügt Werkzeuge,
einen Modellanbieter oder einen Add-on-Scanner hinzu. Plugins können auch Skills mitbringen.

## Mitgeliefert oder extern installiert

VOOL wird mit einer **nativen Skill-Bibliothek** ausgeliefert — den Skills, die mit der App selbst kommen — und
in aktuellen Builds mit dem Plugin **VOOL Database**. Du kannst auch eigene Skills und Plugins installieren.

| | Mitgeliefert (kommt mit VOOL) | Extern installiert |
| --- | --- | --- |
| Wo es liegt | In der App | Dein Plugin-Ordner (verwaltet im Plugin-Fenster von VOOL, nicht in den Einstellungen) |
| Updates | Mit der App | Wenn du das Paket aktualisierst |
| Kann deaktiviert werden | Noch keine Schaltfläche in den Fenstern der App | Ja — im Plugin-Fenster |
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

„Skills & Erweiterungen“ listet zuerst die eigenen Skills von VOOL. **Durchsuchen** bringt 75 fertige
VOOL-Skills mit. Jeder ist ein Satz von Anweisungen, mit der App gebündelt, daher listet „Durchsuchen“ sie
ohne Online-Abfrage. 22 davon nutzen VOOL-Werkzeuge (deine E-Mails, Notizen, Dateien, PDFs, Wallet und
mehr) in den Runden, in denen sie gewählt werden: Die Werkzeuge eines Skills grenzen nur ein, was VOOL für deine
Anfrage ohnehin anbietet, ein ausgeschaltetes Werkzeug bleibt aus, und jeder Aufruf fragt wie immer nach. Seine
**Quelle & technische Details** listen die Werkzeuge, die er nutzt. Der Tab **Fertigkeiten-Codex** listet zuerst die Skills
des VOOL-Teams. Prüfe einen Skill, bevor du ihn aktivierst.

Wie ein Skill aus „Durchsuchen“ hinzugefügt wird, hängt davon ab, wer ihn gemacht hat:

* **Von VOOL gemachte Skills** (die 75 oben): Drücke **Diesen Skill hinzufügen**. VOOL prüft auf deinem
  Computer, dass die Datei genau die ist, mit der es ausgeliefert wurde, liest sie auf riskante
  Anweisungen hin und zeigt dir dann das Ergebnis. Es wird kein Schlüssel gebraucht, und nichts wird irgendwohin gesendet.
  Stimmt die Datei nicht überein, lehnt VOOL sie ab und sagt das.
* **Skills anderer Hersteller** (andere Herausgeber oder ein GitHub-Link, den du einfügst): Drücke
  **Herunterladen und scannen**. VOOL lädt den öffentlichen Skill in einer festen Version herunter und prüft ihn auf
  deinem Computer. Wenn du ein Scanner-Plugin aktiviert hast, erhält es ebenfalls den Inhalt des Skills, und
  sein Bericht kommt neben dem von VOOL hinzu. Lies das Ergebnis und entscheide dann, ob du den
  Skill hinzufügst: Meldet eine der Prüfungen ein Problem, fügt VOOL ihn erst hinzu, nachdem du die Risiken für
  diese Version akzeptiert hast. Es wird kein Schlüssel gebraucht, außer dein Scanner-Plugin verlangt einen.

Du kannst auch ganz ohne Schlüssel im Chat einen eigenen Skill machen: siehe
[Einen eigenen Skill machen](../../../guides/make-your-own-skill.md).

Du kannst auch in jedem Chat fragen: *Welche Skills habe ich?* listet sie auf, und *Wie füge ich einen
Skill hinzu?* erklärt beide Wege.

## Einen Skill ein- oder ausschalten

Öffne **Skills & Erweiterungen**, dann **Meine Add-ons**, dann **Fertigkeiten**. Jeder Skill zeigt **Ein** oder
**Aus** mit einer Schaltfläche **Aktivieren** oder **Deaktivieren**. Einen Skill, den du ausschaltest, nutzt kein Chat,
bis du ihn wieder einschaltest. Seine Dateien bleiben erhalten.

Die Karte **Built-in skills** unter **Plug-ins** hat keine eigene Schaltfläche: Schalte ihre Skills unter
**Fertigkeiten** einzeln ein oder aus.

## Sehen, welcher Skill eine Antwort geprägt hat

Wenn die Anweisungen eines Skills dazu beigetragen haben, wie VOOL eine Antwort geschrieben hat, sagt das eine kurze Zeile unter der
Antwort, zum Beispiel **Verwendeter Skill: Browser**. Sie erscheint direkt nach der Antwort, solange
der Chat offen ist; später aus deinem Verlauf geladene Antworten zeigen sie noch nicht. Regeln zum Antwortstil,
die bei den meisten Antworten mitlaufen, werden dort nicht genannt.

## Skills zum Bauen von VOOL

Die Bibliothek enthält auch die Skills, mit denen das VOOL-Team VOOL baut: Code-Review, Testläufe,
Release-Prüfungen, Reparatur der Pipeline und Ähnliches. Sie stehen weder im Chat noch auf der Skills-Seite,
und sie steuern keine alltäglichen Antworten. Entwickler können sie anzeigen und nutzen, indem sie VOOL mit
`VOOL_DEVELOPER_SKILLS=1` starten.

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

## Plugins in Zusatzmodule

Öffne **Zusatzmodule → Meine Add-ons → Plug-ins**. Prüfe einen lokalen Ordner oder einen öffentlichen GitHub-Ordner in
einem genauen Commit. Die Installation erfasst das vollständige Paket und lässt es deaktiviert. Signierte
Store-Plugins nutzen die bestehende Registry-Kette und werden ebenfalls deaktiviert installiert. Prüfe das Zugriffsblatt
gesondert, bevor du Werkzeuge oder Dienste aktivierst. Das Speichern eines Dienstschlüssels löst keine Anfrage aus;
Verbindungstests und das Aktualisieren der Modellliste sind ausdrückliche Aktionen.

Deaktivieren entfernt Werkzeuge und stoppt laufende Aufrufe. Deinstallieren deaktiviert zuerst, entfernt eigene Dateien
und den Scratch-Bereich, löscht standardmäßig gespeicherte Schlüssel und behält den Verlauf. Updates und Neuinstallationen
erfordern eine neue Prüfung und starten deaktiviert. Ein Update aus einem anderen Repository oder von einem anderen Herausgeber
behält die gespeicherten Schlüssel nicht. UsePod und Eyebrow gehören nicht mehr zu VOOL, und VOOL
bietet für keines davon ein Plugin an. Ein gespeicherter UsePod- oder Eyebrow-Schlüssel bleibt auf diesem Computer, und VOOL
verwendet ihn nicht; Zusatzmodule sagt das, bis du den Hinweis schließt. Chats, die ein UsePod-Modell genutzt haben,
haben nach dem Upgrade kein Modell ausgewählt, und Auto wechselt in diesen Chats nicht zu einem Cloud-Modell;
ein Hinweis sagt das, bis du ihn schließt.

Siehe [Plugins verfassen](../../../guides/plugin-authoring.md) und [Add-on-Prüfungen](addon-security.md).

## Vollständige Paketfreigabe

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
Der Interpreter der Laufzeitumgebung und die Systembibliotheken bleiben Teil der vertrauenswürdigen Plattform, statt als
geprüfte Paket-Bytes von Drittanbietern dargestellt zu werden. Diese Integritätsprüfung zertifiziert kein
Verhalten, bescheinigt keinen beliebigen Code, der von außerhalb der freigegebenen Distribution heruntergeladen
oder geladen wird, vermittelt nicht jede interne Wirkung und ersetzt nicht die native Freigabe der Wallet.

Unter macOS läuft Plugin-Code in der macOS-Sandbox. Schlüssel erreichen einen Handler nur über seine private
Eingabe, nie über seine Umgebung, denn jedes Programm desselben Benutzers, Plugin-Code eingeschlossen,
kann auf einem Mac die Startumgebung eines anderen Programms lesen. Ein Verbindungsserver, der einen Schlüssel in seiner
Umgebung braucht, läuft nur in der optionalen isolierten Linux-Laufzeitumgebung, die du selbst bereitstellst;
VOOL startet nie eine VM und lädt nie ein Image herunter. Ohne sie bleibt dieses Plugin aus und sagt, warum.
Linux nutzt bwrap, einschließlich eines privaten Prozess-Namespace und `/proc`.

Unter macOS kann ein Werkzeug, das Netzwerkzugriff erklärt, nur einen privaten Proxy erreichen, den VOOL für jeden
Aufruf startet, und der Proxy verbindet sich nur mit den erklärten Hosts. Unter Linux und in der isolierten Laufzeitumgebung
haben Plugin-Prozesse kein Netzwerk. Native Anfragen an Anbieter und Scanner nutzen auf jeder Plattform ihren eigenen
HTTPS-Transport zu erklärten Hosts.

## Einen Skill schreiben

* Um einen Skill zu machen, indem du im Chat fragst, siehe [Einen eigenen Skill machen](../../../guides/make-your-own-skill.md).
* Um selbst eine SKILL.md-Datei zu schreiben, zu prüfen (Lint), zu installieren und zu testen, siehe [Einen Skill bauen und testen](../../../guides/build-and-test-a-skill.md).
