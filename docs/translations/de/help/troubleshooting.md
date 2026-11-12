---
description: Lösungen für die Probleme, die am häufigsten auftreten.
translation: machine
source_sha256: 792c90fd68caf7409212d3742109c46e49d1f7e640aa3e9f70db9fe61ca55d77
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
[Ein lokales Modell ausführen](../../../guides/local-models.md).

## Sauber neu anfangen

Beende VOOL und entferne das Konfigurationsverzeichnis, das unter
[Konfiguration](../../../reference/configuration.md) aufgeführt ist. Dateien im Arbeitsbereich werden nicht angerührt.
