---
description: Ausgabenlimits, Preisschutz und was passiert, wenn ein bezahlter Aufruf unterbrochen wird.
translation: machine
source_sha256: a4a5246059942702330cbfc18e9872dc5b0f46192fa5b852bfc9060ca987eae6
---

# Ausgabenlimits und Preisschutz

VOOL gibt nie Geld in deinem Auftrag aus, ohne eine ausdrückliche Obergrenze, die du kontrollierst. Diese Seite
beschreibt die Ebenen, die bezahlte Modellaufrufe und bezahlte Ressourcen absichern, und was passiert, wenn ein
bezahlter Aufruf mittendrin unterbrochen wird.

## Zwei Preise, eine Regel

* **Katalogpreis** — was ein Anbieter derzeit für ein Modell ausschreibt. VOOL ermittelt ihn; er ist eine
  Information, keine Erlaubnis.
* **Dein akzeptierter Preis** — die Obergrenze, die du in den Einstellungen für diesen Anbieter und dieses Modell
  freigegeben hast. Ein ermittelter Preis über deiner Obergrenze wird abgelehnt, **bevor** der Anbieter kontaktiert
  wird. Die Ablehnung sagt genau das: Das Modell lief nicht, nichts wurde gesendet, nichts wurde berechnet.

## Ausgabenlimits

Für bezahlte Aufrufe reserviert VOOL vor jedem Aufruf Geld gegen die Limits:

| Limit | Bedeutung |
| --- | --- |
| Pro Aufruf | Die Obergrenze der voraussichtlichen Kosten eines einzelnen Aufrufs. |
| Pro Aufgabe | Das Höchste, was eine Aufgabe über alle ihre Aufrufe hinweg ausgeben darf. |
| Täglich / monatlich | Obergrenzen für die bezahlte Nutzung über den ganzen Zeitraum. |

Ein Aufruf, der ein Limit überschreiten würde, wird abgelehnt, bevor er gesendet wird. Die Ablehnung nennt das
Limit, das ihn abgelehnt hat (`per_task_spend_cap_exceeded` und verwandte Codes), und die Formulierung beschreibt
bewusst den Stand vor dem Senden: *„running it would exceed your spend cap“* — nie eine Behauptung, dass der
Anbieter kontaktiert wurde.

## Ein angeheftetes Modell wird nie stillschweigend ersetzt

Wenn du ausdrücklich ein bezahltes Modell wählst und ein Schutz es ablehnt, antwortet VOOL **nicht** heimlich mit
einem günstigeren Modell. Die Runde schlägt fehl, wobei das von dir gewählte Modell und die tatsächliche Sperre
genannt werden, mit dem Satz *„no other model was substituted.“* Wähle selbst ein anderes Modell, oder hebe das
Limit bewusst an.

## Bezahlt, aber die Antwort kam nie an

Wenn ein bezahlter Aufruf berechnet wurde, seine Antwort aber nicht ankam, ist das Ergebnis **unbekannt** — VOOL
tut nicht so, als wäre er fehlgeschlagen oder erfolgreich gewesen:

* An seiner Stelle wird keine Antwort erfunden, und es wird keine Erstattung angenommen.
* Die Zahlung bleibt als ungeklärt verzeichnet.
* Die Einstellungen führen sie unter **Paid calls waiting for an answer**, wo **Resume** einmal dieselbe Antwort
  anfordert, **ohne erneut zu bezahlen**.

Darum wird in diesen Fällen nie vorgeschlagen, blind zu wiederholen: Ein einfaches erneutes Senden könnte doppelt
bezahlen. Resume ist der einzige unterstützte Weg.

## Automatische Wallet-Zahlungen (x402)

Websites, die eine x402-Zahlung in USDC auf Base oder Base Sepolia verlangen, können aus deiner
Krypto-Pilot-Wallet bezahlt werden, mit einer Freigabe per PIN innerhalb dieser Limits; damit wurde bisher noch
keine echte Mainnet-Zahlung geleistet. Die Option mit Bezahlung pro Aufruf im Modell-Marktplatz in den
Einstellungen zahlt aus der Solana-Mainnet-Wallet, mit einer Freigabe pro Aufruf innerhalb dieser Limits, und
wurde noch nicht auf einer echten Chain getestet.
Über die Wallet bezahlte Ressourcen (siehe [Wallet](../guides/wallet.md)) haben ein eigenes Limit für
automatische Zahlungen. Eine Ressource, die mehr als dieses Limit verlangt, wird einfach nicht bezahlt; du kannst
das Limit bewusst anheben oder die Ressource über einen ausdrücklichen Vorschlag mit deiner Freigabe bezahlen.
