---
description: Häufige Fragen zu VOOL — ob es offline funktioniert, wo dein API-Schlüssel gespeichert wird, ob es kostenlos ist und welche Systeme es unterstützt.
translation: machine
source_sha256: d98db6dcf1791e9de7aed2dbb7204ab774afd9ea47d883bc0211640a8895413e
---

# FAQ

## Funktioniert VOOL offline?

Ja, mit einem lokalen Modell. Cloud-Modelle brauchen naturgemäß eine Netzwerkverbindung.

## Brauche ich einen Cloud-Schlüssel?

Nein. Ein lokales Modell reicht aus, um die App zu nutzen.

## Wo wird mein Schlüssel gespeichert?

In einer verschlüsselten Datei im „runtime home“ von VOOL auf deinem Computer, die nur dein Benutzerkonto
lesen kann. Schlüssel, die VOOL auf einem Mac schon im macOS-Schlüsselbund aufbewahrt hat, bleiben dort.
Schlüssel bleiben auf deinem Rechner. Siehe [Sicherheitsmodell](../trust/security.md#schlüssel).

## Sieht Parad0x meine Prompts?

Nicht im lokalen Modus und auch nicht im Cloud-Modus — Cloud-Anfragen gehen von deinem Rechner zu deinem
Anbieter. Siehe [Umgang mit Daten](../trust/data-handling.md).

## Warum warnt mein System, wenn ich die App öffne?

Die macOS-Apps sind ad-hoc signiert, aber noch nicht von Apple notariell beglaubigt, daher bittet macOS dich,
das erste Öffnen zu bestätigen. Die Schritte stehen unter [Installation](../getting-started/install.md#erstes-öffnen-auf-dem-mac).
Siehe [Release-Status](../trust/release-status.md).

## Kann ich mein eigenes Modell verwenden?

Ja. Jede Laufzeitumgebung, die einen OpenAI-kompatiblen lokalen Endpunkt bereitstellt. Siehe
[Unterstützte Modelle](../reference/models.md).

## Ist es kostenlos?

Die Anwendung ist während der Beta kostenlos. Die Nutzung von Cloud-Modellen rechnet dein Anbieter über
dein Konto ab.
