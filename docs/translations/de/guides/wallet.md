---
description: Die optionale Krypto-Pilot-Wallet — Netzwerke, x402-Zahlungen, DNA-Gebühren und Wiederherstellung, so wie dieser Build sie tatsächlich ausliefert.
translation: machine
source_sha256: 46121a5ed26be175ddd3c66b269df356e074adbb57c8e9806461b80197613cc2
---

# Wallet

Die Wallet ist **optional und standardmäßig ausgeschaltet**. Es gibt sie, damit VOOL einen kleinen Betrag an
Krypto für KI-Dienste halten kann, die pro Anfrage abrechnen. Nichts davon ist nötig, um VOOL zu nutzen.

Diese Seite beschreibt, was der aktuelle Build tatsächlich unterstützt. Die Wallet befindet sich im Stadium eines
**Pilotprojekts**: Funktionen kommen Spur für Spur hinzu, und jede Spur unten sagt genau, was sie tut.

## Netzwerkumgebungen

Wenn Krypto eingeschaltet ist, läuft VOOL im **Mainnet**: Solana, Robinhood Chain, Base, Ethereum, BNB und
Arbitrum One.
Die normale Einrichtung fragt dich nie, eine Netzwerkumgebung zu wählen.

* **Mainnet** — der Standard für jede Installation, auch für Installationen, die mit älteren Testnetz-Konten
  aktualisiert wurden. Jedes Ausgabenlimit, jede Freigabe und jeder Beleg gilt wie unten beschrieben.
* **Test networks** — Solana Devnet, Robinhood Chain Testnet und die anderen Testspuren. Nur für Entwickler:
  Umschalten unter **Einstellungen → Krypto → Developer options**.

Konten, Guthaben und Belege gehören zu einer Umgebung und wechseln nie zwischen den Umgebungen; Konten in der
anderen Umgebung bleiben in ihrem eigenen Netzwerk aufgeführt. Eine Anfrage für ein Netzwerk in der anderen
Umgebung wird abgelehnt, ohne dass etwas erstellt, vorgeschlagen oder signiert wird.

{% hint style="warning" %}
Frühere Dokumentation sagte, Mainnet sei in diesem Build unmöglich. Das stimmte vor dem Pilotprojekt und gilt
nicht mehr: Mainnet-Zeilen gibt es jetzt. Was weiterhin stimmt, ist enger gefasst — welche Spuren es im Mainnet
genau gibt, steht in den Abschnitten zu Überweisungen und Zahlungen unten.
{% endhint %}

## Was auf welcher Spur fließt

* **Überweisungen bewegen native Coins** (zum Beispiel SOL auf Solana, ETH auf Base, Ethereum oder
  Arbitrum One) sowie **USDT auf Solana, Ethereum, BNB Smart Chain und Arbitrum One**.
  Die Chain ist Teil jeder gespeicherten Kontaktadresse und jeder Freigabe. Token-Überweisungen brauchen außerdem
  den nativen Coin dieser Chain für die Gebühren; das Angebot zeigt beide Beträge innerhalb deiner Limits.
* **USDC auf Solana und Base ist nur für Zahlungskanäle verfügbar.** Es wird für gewöhnliches Senden nicht
  angeboten. Die Hauptanzeige des Guthabens in der Wallet zeigt native Coins, kein vollständiges Token-Portfolio.
* Jede Überweisung wird vorab angezeigt (ein **Angebot**: Betrag, Gebührenobergrenze, Empfänger, Netzwerk) und
  braucht deine ausdrückliche Freigabe, die genau an diese Vorschau gebunden ist. Ändert sich irgendetwas, ist die
  Freigabe ungültig — nichts wird signiert.
* Ein Name, der zu mehreren Kontakten oder Wallet-Adressen passt, fordert dich zur Auswahl auf. Prüfe die genaue
  Adresse und Chain. Eine geänderte Adresse oder ein umbenannter Kontakt macht die alte Prüfung ungültig.
* Im Mainnet sendet nur eine VOOL-Wallet, die auf diesem Gerät erstellt wurde. Eine externe Wallet, die du
  verbunden hast, oder eine Nur-Ansehen-Adresse kann empfangen und ihr Guthaben anzeigen; ein Senden oder eine
  Zahlung über einen Zahlungskanal von ihr aus wird abgelehnt, bevor überhaupt eine Freigabe angefragt wird, und
  nichts wird signiert.
* „Aktivität“ verzeichnet Zahlungen, die über VOOL geleistet wurden, und ihre Belege. Sie ist kein vollständiger
  Verlauf eingehender Zahlungen. Diese Wallet bietet keine Swap- oder Bridge-Aktion an.

## So wird eine Zahlung freigegeben

1. VOOL bereitet einen Vorschlag mit den genauen Beträgen und einer Gebührenobergrenze vor.
2. Du gibst ihn mit deiner PIN/deinem Passwort frei (oder per Anwesenheit am Gerät, wo verfügbar).
3. Die Zahlung wird signiert und gesendet, und ein Beleg wird verzeichnet.

Freigaben übertragen sich nicht zwischen Vorschlägen. Eine falsche PIN signiert nichts. Zu viele falsche
Versuche sperren die Wallet für eine Weile — bewusst, mit einer angegebenen Wartezeit.

## Automatische x402-Zahlungen

x402 ist ein Zahlungsprotokoll pro Anfrage, das manche Websites und KI-Dienste sprechen. **Websites, die eine
x402-Zahlung in USDC auf Base verlangen** (oder auf Base Sepolia, in den Developer options), können aus deiner
Krypto-Pilot-Wallet bezahlt werden: eine Freigabe mit deiner PIN, innerhalb deiner Limits. Der Zahlungsdienst der
Website übernimmt die Netzwerkgebühr. **Damit wurde bisher noch keine echte Mainnet-Zahlung geleistet.** Die
anderen Testnetze (Solana Devnet, Ethereum Sepolia und BNB testnet) bleiben in den Developer options.
**Einstellungen → Krypto → Payment channels** listet jeden Weg, auf dem die Wallet eine Website bezahlen kann,
jeweils mit eigenem Ein-/Aus-Schalter; ein ausgeschalteter Kanal lehnt ab, bevor irgendetwas signiert wird.
Bezahlen über Solana pay-kit ist in den Downloads nicht enthalten, daher wird dieser Kanal als nicht nutzbar
angezeigt. **Modell-Marktplatz:** Die Option mit Bezahlung pro Aufruf im Modell-Marktplatz in den Einstellungen
kann aus der Solana-Mainnet-Krypto-Pilot-Wallet in USDC oder SOL bezahlen. Jeder Aufruf wartet innerhalb deiner
Wallet-Limits auf deine Freigabe und zeigt die VOOL-Gebühr von 0,1 %. Dieser Weg wurde noch nicht auf einer
echten Chain getestet.
Wenn eine bezahlte Ressource x402 nutzt:

* Zahlungen laufen über dieselben Wallet-Limits und denselben Freigabeablauf.
* Automatische Zahlungen sind pro Zahlung begrenzt; eine Ressource über dem Limit wird **nicht bezahlt** (du kannst
  das Limit bewusst anheben oder sie per ausdrücklichem Vorschlag bezahlen).
* Wenn eine Zahlung nicht abgeschlossen werden konnte, wird das Ergebnis als **unbekannt** gemeldet — prüfe ihren
  Status, bevor du es erneut versuchst; VOOL lädt nicht zu einer blinden doppelten Zahlung ein.

## DNA-Servicegebühr

Die x402-Route mit Bezahlung pro Aufruf im Modell-Marktplatz berechnet auf jede akzeptierte Anbieterzahlung eine
Servicegebühr von **0,1 % (10 Basispunkte)**, die getrennt von der Zahlung und getrennt von der Netzwerkgebühr
angezeigt wird. Für nichts anderes fällt sie an: Gewöhnliches Senden, das Erstellen einer Wallet, lokale Inferenz
und Aufrufe mit deinem eigenen API-Schlüssel (BYOK) tragen **keine** Servicegebühr. Aufgelaufene Gebühren werden
wirtschaftlich eingezogen (mit einer späteren nativen Zahlung, unter einer einzigen Freigabe, nur wenn die Kosten
des Einzugs begrenzt sind) — nie durch eine überraschende separate Abbuchung.

## Wiederherstellung

* Die **Wiederherstellungsphrase wird genau einmal angezeigt**, bei der Erstellung.
* Der Sicherungsschlüssel einer Pilot-Wallet wird ebenso nur einmal während der Einrichtung herausgegeben, und es
  gibt keinen späteren Export dafür. Wenn du ihn nicht gespeichert hast, brich die Einrichtung ab und sende kein
  Geld an diese Adresse.
* Eine ältere VOOL-Wallet, die vor Krypto-Pilot erstellt wurde, kann ihren privaten Schlüssel einmal exportieren,
  um ihn in eine andere Wallet-App zu importieren. Der Export öffnet sich nur mit der eigenen Anmeldung deines Macs
  (Touch ID oder dein Mac-Passwort), nie mit der Wallet-PIN, und der Schlüssel wird einmal angezeigt, ohne
  gespeichert zu werden.
* Wallet verloren? Stelle sie aus der Phrase wieder her, die du bei der Erstellung notiert hast. Ein
  Wiederherstellungsnachweis, der nicht genau zu dieser Wallet passt, wird vor jedem Schreibvorgang abgelehnt.
