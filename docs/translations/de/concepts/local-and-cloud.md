---
description: Welche Daten deinen Computer verlassen, wenn du VOOL nutzt, und wann. Der Unterschied zwischen KI, die lokal läuft, und einem Cloud-Modell.
translation: machine
source_sha256: 2c2b9e26e99579c00a032dfeac95fd216fe9229497c965c1bc08aface7a6c071
---

# Lokal und Cloud

VOOL kann auf zwei Wegen antworten: auf diesem Computer oder über einen Cloud-Anbieter. Die Kopfzeile
des Chats zeigt, auf welchem Weg die nächste Anfrage läuft — zum Beispiel **Auto** mit
**Lokal zuerst**, wenn VOOL Auto von diesem Computer aus antwortet.

## Lokal

Das Modell und deine Dateien bleiben auf diesem Computer. Das Modell läuft über eine lokale Laufzeitumgebung, Dateien werden
von der Festplatte gelesen, und an keinen Anbieter wird eine Anfrage gestellt. Aktuelle Fakten wie Wetter
oder Preise kommen nur von öffentlichen Webdiensten, solange Websuchen eingeschaltet ist (bei einer frischen Installation ist es aus), und nie in Nur lokal.

## Cloud (Name des Anbieters)

Der ausgewählte Kontext wird direkt an den von dir ausgewählten Anbieter gesendet, von deinem Rechner aus,
mit deinem Schlüssel.

„Der ausgewählte Kontext“ ist das, was die App für diese Anfrage zusammengestellt hat: dein Prompt plus
die Dateien und früheren Gesprächsrunden, die die Anfrage braucht.

{% hint style="danger" %}
Der Cloud-Modus sendet Daten von deinem Rechner weg. Genau dafür ist der Cloud-Modus da. Was VOOL zusichert,
betrifft den *Weg* — die Anfrage geht von deinem Computer zu deinem Anbieter —, nicht, dass
nichts übertragen wird.
{% endhint %}

## Was dazwischen liegt

Nichts, was Parad0x Labs betreibt. Es gibt keinen Proxy, kein Gateway und kein Relay für den Modellverkehr.
Dein Schlüssel wird von der App auf deinem Rechner verwendet, um direkt mit dem Anbieter zu sprechen.

Alle Details: [Umgang mit Daten](../trust/data-handling.md).
