---
description: Welche Daten deinen Computer verlassen, wenn du VOOL nutzt, und wann. Der Unterschied zwischen KI, die lokal läuft, und einem Cloud-Modell.
translation: machine
source_sha256: 3217e13bf6d684853b92d19097627d07e64bdd17745560b1204871cb0b7b0753
---

# Lokal und Cloud

VOOL kennt zwei Zustände. Die Oberfläche zeigt immer, welcher gerade aktiv ist.

## Lokal

Alles bleibt auf diesem Computer. Das Modell läuft über eine lokale Laufzeitumgebung, Dateien werden
von der Festplatte gelesen, und an keinen Anbieter wird eine Anfrage gestellt.

## Cloud (Name des Anbieters)

Der ausgewählte Kontext wird direkt an den von dir ausgewählten Anbieter gesendet, von deinem Rechner aus,
mit deinem Schlüssel.

„Der ausgewählte Kontext“ ist das, was die App für diese Anfrage zusammengestellt hat: dein Prompt plus
die Dateien und früheren Gesprächsrunden, die die Anfrage braucht. Er wird vor dem Senden angezeigt.

{% hint style="danger" %}
Der Cloud-Modus sendet Daten von deinem Rechner weg. Genau dafür ist der Cloud-Modus da. Was VOOL zusichert,
betrifft den *Weg* — die Anfrage geht von deinem Computer zu deinem Anbieter —, nicht, dass
nichts übertragen wird.
{% endhint %}

## Was dazwischen liegt

Nichts, was Parad0x Labs betreibt. Es gibt keinen Proxy, kein Gateway und kein Relay für den Modellverkehr.
Dein Schlüssel wird von der App auf deinem Rechner verwendet, um direkt mit dem Anbieter zu sprechen.

Alle Details: [Umgang mit Daten](../trust/data-handling.md).
