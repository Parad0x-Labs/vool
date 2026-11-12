---
description: Wie VOOL sich Dinge zwischen Sitzungen merkt und wo dieses Gedächtnis liegt.
translation: machine
source_sha256: 65af63e755076fe33675d7a5d6afa7f0c5664b77f716ffc0dc1f0f358e67f667
---

# Gedächtnis

Das Gedächtnis ist der Weg, auf dem VOOL Fakten über Sitzungen hinweg mitnimmt. Es wird als Dateien auf deinem
Rechner gespeichert, im Datenordner von VOOL, in einem lesbaren Format.

## Wie gut es sich erinnert

Bei 150 noch nie verwendeten Fragen aus dem LoCoMo-Benchmark für lange Gespräche beantwortete das Gedächtnis aus
VOOL 0.7 123 richtig (82,0 %). In der direkten Vergleichsrunde mit denselben Fragen, bei der für alle drei dasselbe
Modell antwortete, kam VOOL auf 124 (82,7 %), OpenClaw auf 107 (71,3 %) und Hermes auf 94 (62,7 %),
und VOOL sendete dabei 3,5- bis 5,4-mal weniger Tokens pro Frage. Jeder Lauf ist veröffentlicht:
[Gedächtnis-Benchmarks](https://github.com/Parad0x-Labs/vool/tree/main/docs/benchmarks).

## Eigenschaften

* **Auf der Festplatte, offen einsehbar.** Das Gedächtnis besteht aus Dateien, die du lesen kannst. `MEMORY.md` ist
  die lesbare Ansicht; die Fakten dahinter liegen in einer Eintragsdatei daneben.
* **Lokal.** Das Gedächtnis wird nicht mit einem Server synchronisiert.
* **Ein Speicher pro VOOL-Datenordner.** Das Gedächtnis ist nicht nach Arbeitsbereichen aufgeteilt — es wird
  für die ganze Installation geführt und ist daher keine Isolationsgrenze pro Projekt.

## Das Gedächtnis verwalten

Notizen, die du selbst in `MEMORY.md` einträgst, bleiben erhalten. Fakten, die VOOL gespeichert hat, werden
über die Vergessen-Funktion von VOOL getilgt, nicht durch Löschen der Datei: Das Vergessen schreibt den
Eintragsspeicher und die Spiegeldatei in einem Schritt neu und liest beide erneut, um zu bestätigen, dass der
Text weg ist. Nur `MEMORY.md` zu löschen, ist keine Tilgung — die Eintragsdatei enthält die Fakten weiterhin,
und die Spiegeldatei wird aus ihr neu aufgebaut.

Im Cloud-Modus kann relevantes Gedächtnis Teil des Kontexts sein, der an den Anbieter gesendet wird, zu
denselben Bedingungen wie jeder andere Kontext. Siehe [Lokal und Cloud](local-and-cloud.md).

Vergessen in einem Chat tilgt die Wörter auch aus dem Ausführungsverlauf dieses Chats in der Datenbank der
Laufzeitumgebung: aus den Datensätzen der Gesprächsrunden, Checkpoints, Versuchen, Aufgabenplänen, fertigen Antworten
(samt den Kopien, die die Tilgung von Antworten miterfasst), Wissenskandidaten, Kontextberichten,
Benachrichtigungen, Recherche-Artefakten, die der Chat erzeugt hat, und angehefteten Nachrichten, die sie
zitierten. Datensätze, von denen andere Datensätze abhängen, bleiben mit geschwärzten vergessenen Wörtern
erhalten, zusammen mit jedem daneben gespeicherten Hashwert; der Rest wird gelöscht. Eine Gesprächsrunde, die
noch lief, als das Vergessen kam, kann die Wörter nicht zurückschreiben: Ihre Ereignisse werden geschwärzt, und
wenn sie fertig ist, werden ihre Datensätze erneut getilgt. Danach wird das Write-Ahead-Log der Datenbank in die
Datenbank übernommen, damit ältere Kopien nicht in der `-wal`-Datei bleiben. Die Datensätze eines anderen Chats
werden nicht angerührt. Eine Nachricht in der Warteschlange, die noch nicht gesendet wurde, bleibt erhalten, und
das Vergessen meldet dann, dass es nicht vollständig angewendet werden konnte. Eine Tabelle, die kein
Tilgungsschritt kennt, wird in der Antwort genannt, und die Antwort sagt, dass ein erneuter Versuch sie nicht
bereinigen wird.

Wörter werden ohne Rücksicht auf Groß- und Kleinschreibung verglichen, und eine Ziffer, die in einer anderen
Schrift geschrieben ist, gilt als diese Ziffer (ein „１２“ in voller Breite ist 12). Eine vergessene Zahl passt nur
dort, wo sie als eigene Zahl steht: Die Buchstaben und Ziffern, die mit ihr zusammengeschrieben sind, dürfen keine
andere Ziffer enthalten, und sie darf nicht über `.`, `,`, `:`, `/`, `-` oder `_` hinweg in eine weitere Ziffer übergehen. „12“ zu vergessen, tilgt „Code 12“, „12kg“ und „B12“,
aber nicht „Zimmer 312“, „3.12“, „12.5“, „12:30“ oder „2026-10-12“, und „200“ zu vergessen, tilgt
nicht „1,200“. Im Ausführungsverlauf wird eine bloße Zahl, die die Laufzeitumgebung gezählt oder gemessen hat
(etwa ein Wert von `elapsed_ms` oder `step_count`), nicht erfasst.

Leser des Gedächtnisses prüfen außerdem eine dauerhafte Vergessen-Instanz. Ein neuer Gedächtnisordner legt sie
an, bevor er Schreibvorgänge annimmt. Ein älterer semantischer Speicher wird nur dann direkt aktualisiert, wenn
keine frühere Tilgung verzeichnet ist; seine behaltenen Erinnerungen bleiben verfügbar. Eine vorhandene, aber
unlesbare Instanz, eine fehlende Instanz, zu der ein gespeicherter Zeuge existiert, oder eine verzeichnete
Vergessen-Sperre bewirkt, dass Leser das betroffene Gedächtnis zurückhalten. Instanzdateien zu löschen, ist
kein Weg, vergessene Inhalte zurückzuholen.

Ein ausstehendes Tilgungsjournal behält sein Token für die Wiederherstellung. Sobald der dauerhafte, auf den
Geltungsbereich begrenzte Widerruf existiert, entfernt der Abschluss das überflüssige Token des Journals, behält
aber die Identität und den Status des Vorgangs. Die Widerrufs-Instanz bleibt bestehen.
