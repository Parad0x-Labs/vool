---
description: So verbindest du OpenRouter mit VOOL, damit ein einziger API-Schlüssel viele Cloud-KI-Modelle erreicht.
translation: machine
source_sha256: 75cb20d43eb37adb1d30913959ca1cb46864ad57ba7a1a22ec261f0e0eaf2e9a
---

# OpenRouter verbinden

OpenRouter bietet Zugang zu vielen Anbietern hinter einem einzigen Schlüssel. VOOL unterstützt das Hinzufügen
eines OpenRouter-Schlüssels.

## Schlüssel hinzufügen

Erstelle einen Schlüssel in deinem OpenRouter-Konto, füge ihn unter **Einstellungen → API-Schlüssel** ein und
drücke **Schlüssel speichern**. VOOL prüft den Schlüssel einmal bei OpenRouter und bewahrt ihn dann in einer
verschlüsselten Datei auf diesem Computer auf (siehe [Sicherheitsmodell](../trust/security.md#schlüssel)).

## Abrechnung und Zuordnung

{% hint style="info" %}
Dein OpenRouter-Konto bleibt deins. VOOL sendet Anfragen direkt von diesem Computer
mit deinem Schlüssel. Parad0x leitet deine Prompts nicht weiter und bezahlt deine Nutzung nicht. Echte
VOOL-Nutzung kann in den öffentlichen, zusammengefassten OpenRouter-Statistiken von VOOL erscheinen, aber
einzelne Prompts und Identitäten werden nicht veröffentlicht.
{% endhint %}

## Schlüssel entfernen

Entferne den Schlüssel unter **Einstellungen → API-Schlüssel**. Widerrufe den Schlüssel zusätzlich in deinem OpenRouter-Konto, wenn er auch auf dem Server ungültig sein soll.
