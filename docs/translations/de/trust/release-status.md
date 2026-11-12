---
description: Wo VOOL steht, klar gesagt.
translation: machine
source_sha256: bf79dfc262c1aca9c489c8474ad2de5fa633b0b60a515f2835f52954c66ad73f
---

# Release-Status

Diese Seite ist die gepflegte maßgebliche Quelle für den Release-Status. Andere Dokumente nennen Fakten zum
Release nebenbei; wenn sie dieser Seite widersprechen, gilt diese Seite.

## Aktuelle Phase

VOOL ist in der **Beta**. Das aktuelle Release ist
[**v0.7.7-beta**](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.7-beta), ein GitHub-Release, gebaut
aus `main` bei `8af687c`. Es bietet App-Downloads für drei Plattformen, jeweils mit einer veröffentlichten
SHA-256-Prüfsumme. Die Installation aus dem Quellcode mit dem Bootstrap-Skript wird unter macOS, Linux und
Windows weiterhin unterstützt; siehe [Installation](../getting-started/install.md).

| Download | Für |
| --- | --- |
| `VOOL-0.7.7-beta-macos-arm64.dmg` | Macs mit Apple Silicon, macOS 14 oder neuer |
| `VOOL-0.7.7-beta-macos-x86_64.dmg` | Intel-Macs, macOS 14 oder neuer |
| `vool-0.7.7-beta-linux-x86_64.tar.gz` | Linux x86_64, getestet unter Ubuntu 22.04 und 24.04 |

Die Release-Aufzeichnungen dieses Repositorys decken den GitHub-Kanal ab. Andere Download-Kanäle werden
getrennt betrieben; prüfe jeden Download anhand der Prüfsumme, die im GitHub-Release veröffentlicht ist.

| | Status |
| --- | --- |
| Öffentliche Downloads | DMG für macOS Apple Silicon, DMG für macOS Intel, Linux-x86_64-Tarball im GitHub-Release v0.7.7-beta |
| Veröffentlichte Prüfsummen | SHA-256 für jeden Download, im Release |
| Codesignatur | Die macOS-Apps sind ad-hoc signiert, nicht mit einer Developer ID. Die Signatur kommt nach dieser Beta hinzu und ist keine Voraussetzung für sie |
| Notarisierung für macOS | Nicht notariell beglaubigt. macOS bittet dich, das erste Öffnen zu bestätigen (siehe unten) |
| Windows | Kein Windows-Download in diesem Release; unter Windows wird aus dem Quellcode installiert, und das ist experimentell |
| Updates | Ab 0.7.5 aktualisiert sich VOOL selbst: Ist „Von selbst online gehen“ eingeschaltet, zeigt VOOL einen Hinweis und installiert eine neue Version mit einem Klick. Update von 0.7.4 oder 0.7.3 auf einem Mac: Schalte „Von selbst online gehen“ ein (Einstellungen > Privatsphäre & Berechtigungen) und drücke einmal auf „Nach Updates suchen“. Unter Linux oder von 0.7.2, 0.7.1 oder 0.7.0: Lade 0.7.7 herunter und installiere es über deine aktuelle Version. Deine Chats und Einstellungen liegen in deinem Profil, außerhalb der App. Siehe [Updates und Migration](../reference/updates-migration.md) |
| API-Stabilität | Ändert sich zwischen Builds |

## Was das für dich bedeutet

* Beim ersten Start einer Mac-App weigert sich macOS, sie zu öffnen, weil sie nicht notariell beglaubigt
  ist. Das ist in dieser Phase zu erwarten und kein Zeichen für einen beschädigten Download. Prüfe die
  Prüfsumme, öffne dann **Systemeinstellungen → Datenschutz & Sicherheit** und klicke auf **Dennoch öffnen**.
  Unter macOS 14 kannst du stattdessen bei gedrückter Taste „ctrl“ auf die App klicken und **Öffnen** wählen.
* Wähle den Mac-Download, der zu deinem Chip passt. Die App für Apple Silicon läuft nicht auf einem
  Intel-Mac und sagt das beim Start.
* Konfigurationsformate können sich zwischen Builds ändern.
* Krypto-Funktionen sind aus, bis du sie einschaltest.

## Einschränkungen von Beta und Pilot, ausdrücklich genannt

| Funktion | Stand in diesem Build |
| --- | --- |
| Krypto und Wallet | Standardmäßig aus. Wenn du es einschaltest, nutzt VOOL das Mainnet; Testnetze findest du in den Developer options. Jede Zahlung läuft über die Wallet von VOOL, mit ihren Limits, deiner Freigabe und einem Beleg. Siehe [Wallet](../guides/wallet.md). |
| Mainnet-Überweisungen | Native Coins sowie USDT auf Solana, Ethereum, BNB Smart Chain und Arbitrum One, gesendet aus einer VOOL-Wallet, die auf diesem Gerät erstellt wurde. USDC auf Solana und Base dient nur für Zahlungskanäle. Ein verbundenes oder nur beobachtetes Konto kann empfangen und sein Guthaben zeigen, aber nicht aus VOOL senden. |
| Bezahlen mit x402 | Websites, die eine x402-Zahlung in USDC auf Base oder Base Sepolia verlangen, können aus deiner Krypto-Pilot-Wallet bezahlt werden: eine Freigabe mit deiner PIN, innerhalb deiner Limits. Der Zahlungsdienst der Website übernimmt die Netzwerkgebühr. **Einstellungen → Krypto → Payment channels** hat für jeden Weg, auf dem die Wallet eine Website bezahlen kann, einen Ein/Aus-Schalter; ein ausgeschalteter Kanal lehnt ab, bevor irgendetwas signiert wird. Damit wurde noch keine echte Mainnet-Zahlung durchgeführt. Bezahlen über Solana pay-kit ist in den Downloads nicht enthalten; dieser Kanal wird als nicht nutzbar angezeigt. |
| X | Aus jedem Chat: Beiträge und Konten, Spaces, Trends und DMs durchsuchen und frühere Calls zu einem Token nachschlagen. Alles, was in deinem Konto handelt, wartet auf dein OK. |
| Diktat | In diesem Beta-Build vorübergehend nicht verfügbar; es kommt mit einem Update zurück. |
| Cloud-Fragemodus „Burst“ | Die Rückfrage bei jedem Aufruf ist in diesem Build noch nicht angebunden. |
| Lokale Modelle | Bevor ein lokales Modell antworten kann, öffne **Einstellungen → Modelle & Anbieter → Lokale Modelle** und drücke dafür einmal **Certify**. |
| Intel-Macs | Lokale Modelle laufen auf dem Prozessor und sind langsam. Die Zertifizierung berücksichtigt langsamere Prozessoren, aber eine lange Antwort aus dem Gedächtnis kann in eine Zeitüberschreitung laufen, nutze dort also vorerst ein Cloud-Modell. |
| Privater Chat | Sag „Privater Chat“, „Speicher das nicht“ oder „Das bleibt unter uns“, und nichts aus diesem Chat wird gespeichert. Die Befehle funktionieren in allen 17 Sprachen der App. |
| Vergessen | Vergessen löscht das Gedächtnis von VOOL und den passenden Ausführungsverlauf, sodass der Eintrag nicht wieder abgerufen oder aufgelistet wird, und tilgt die vergessenen Wörter aus der lokalen Gedächtnisdatei von VOOL und ihrem Suchindex. VOOL behält die Wörter, die es vergessen sollte, in einer kurzen Liste auf deinem Computer, damit es sie weiterhin ablehnen kann. Nachdem du in einem Chat etwas vergessen lassen hast, nimmt VOOL genau diese Tatsache in diesem Chat nicht wieder auf; starte einen neuen Chat, um sie ihm erneut zu sagen. |
| Gescannte PDFs | Werden mit der eingebauten Texterkennung des Mac gelesen, also nur auf Macs. |
| Schreibtisch-Tier | Das Tier lebt im VOOL-Fenster. Wenn du es aus dem Fenster ziehst, wandert es nicht auf den Schreibtisch. |
| Fertigkeiten-Codex | „Fertigkeiten-Codex durchsuchen“ zeigt die Skills, die für deine genaue VOOL-Version gelistet sind, und du prüfst einen Skill, bevor er installiert wird. „Im Fertigkeiten-Codex veröffentlichen“ prüft einen Skill, den du auf deinem Computer erstellt hast, und öffnet dann die Website des Fertigkeiten-Codex mit dem ausgefüllten Skill; dort meldest du dich an und drückst auf Send, und VOOL selbst sendet nichts. Nur der Text des Skills wird mitgeschickt, daher lässt sich ein Skill mit zusätzlichen Dateien noch nicht teilen. |

Signierung mit Developer ID und Notarisierung für macOS sowie signierte Prüfsummen für Linux sind die nächsten
Schritte auf dem Weg zum Release; keins von beidem wird heute behauptet.
