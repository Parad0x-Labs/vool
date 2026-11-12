---
description: Lösungen für die Probleme, die am häufigsten auftreten.
translation: machine
source_sha256: 809bae11bc4bf8c2d707cfef616f80056a5d98d35b0786cc937a40526e802689
---

# Fehlerbehebung

## macOS weigert sich, die App zu öffnen

Das ist zu erwarten: Die App ist noch nicht notariell beglaubigt. Prüfe zuerst die Prüfsumme, öffne dann
**Systemeinstellungen → Datenschutz & Sicherheit** und klicke auf **Dennoch öffnen**. Unter macOS 14 kannst
du stattdessen bei gedrückter Taste „ctrl“ auf VOOL klicken und **Öffnen** wählen.

## Windows

In diesem Release gibt es keinen Windows-Download. Unter Windows wird aus dem Quellcode installiert, und
das ist experimentell; siehe die [Installationsanleitung](../getting-started/install.md#aus-dem-quellcode-installieren).

## Es werden keine lokalen Modelle angezeigt

1. Prüfe, ob die Laufzeitumgebung läuft.
2. Prüfe, ob mindestens ein Modell heruntergeladen ist.
3. Prüfe, ob der Port dem entspricht, den VOOL erwartet.

```bash
curl http://localhost:11434/api/tags
```

Wenn das nichts zurückgibt, läuft die Laufzeitumgebung nicht.

## Antworten sind langsam

Meist liegt es an der Modellgröße im Verhältnis zum verfügbaren Arbeitsspeicher. Siehe
[Ein lokales Modell ausführen](../guides/local-models.md).

## Ein Fehler zeigt einen Code

Jeder Fehler hat einen festen Code. [Fehler und Fehlercodes](../reference/errors.md) erklärt, wie
man ihn liest, und das [Fehlerbuch](../ERROR_BOOK.md) sagt zu jedem Code, was passiert ist, was
womöglich schon passiert ist und was als Nächstes zu tun ist.

## Sauber neu anfangen

Beende VOOL und entferne den Ordner „runtime home“, der unter
[Konfiguration](../reference/configuration.md) aufgeführt ist. Das löscht deine Chats, dein
Gedächtnis, deine Einstellungen, deine gespeicherten Schlüssel und jede Wallet, die VOOL auf diesem
Computer aufbewahrt. Sichere also zuerst, was du brauchst: siehe
[Sicherung und Wiederherstellung](backup-and-recovery.md). Dateien im Arbeitsbereich werden nicht
angerührt.
