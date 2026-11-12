---
description: Was du in dieser Version von VOOL sichern, umziehen und wiederherstellen kannst: Chats, Gedächtnis, Schlüssel und Wallets.
translation: machine
source_sha256: 3b2f11975a8cf5f5ab2e07e8b4a3083a9598de9e301965c493408005aa5893fa
---

# Sicherung und Wiederherstellung

Alles, was VOOL aufbewahrt, liegt auf deinem Computer, in einem einzigen Profilordner (wo genau, steht unter
[Konfiguration](../reference/configuration.md)). Ein Update oder eine Neuinstallation der App berührt ihn
nie. VOOL hat keine Cloud-Kopie deiner Daten und keine Sicherung des ganzen Profils in einem Schritt. So
sicherst du, was wichtig ist, und holst es zurück.

## Chats

| Du möchtest | Verwende | Was du bekommst |
| --- | --- | --- |
| Eine Kopie zum Lesen oder Teilen | **Exportieren** in der Kopfzeile des Chats | Den ganzen Chat als Text oder Markdown. Angehängte Dateien werden nur mit Namen aufgeführt. |
| Eine Kopie, die du wieder in VOOL holen kannst | Einstellungen, Sicherung & Wiederherstellung, **Gespräch exportieren** | Einen Chat als `.voolsession`-Datei, signiert von diesem Computer. |
| Einen Chat aus dieser Datei zurück | Einstellungen, Sicherung & Wiederherstellung, **Gespräch importieren** | Den Chat, neben deinen anderen hinzugefügt. |
| Deine Chats aus anderen KI-Apps | Einstellungen, Sicherung & Wiederherstellung, **Chats aus anderen KI-Apps mitbringen** | Diese Chats, mit ihren Namen und Nachrichten. |

**Gespräch exportieren** enthält die Nachrichten, Belege und Verweise auf Nachweise des Chats, nie das
interne Denken von VOOL, deine Einstellungen, dein Gedächtnis oder deine Schlüssel. Schütze die Datei mit einer
Passphrase, wenn sie diesen Computer verlässt: Ein Export ohne Passphrase bittet dich vorher um Bestätigung.
Eine Vorschau zeigt genau, was geschrieben wird, bevor irgendetwas geschrieben wird.

**Gespräch importieren** zeigt, was die Datei enthält und wer sie signiert hat, bevor etwas hinzugefügt wird.
Eine Datei, die mit einem Schlüssel signiert ist, den VOOL nicht kennt, wird abgelehnt, außer du akzeptierst
sie, und der Chat bleibt als nicht vertrauenswürdig markiert. Ein Import ändert nie einen Chat, den du schon
hast: Ist derselbe Chat hier, kommt die Kopie als neuer Chat an.

**Chats aus anderen KI-Apps mitbringen** liest eine App auf diesem Computer oder eine Datei aus einem
Datenexport. Du hakst die Chats an, die du übernehmen möchtest. Nichts verlässt deinen Computer, und die
Dateien der anderen App werden nie geändert. VOOL bewahrt diese Chats als Verlauf auf: Es lernt nicht aus
ihnen und folgt nichts, was darin geschrieben steht.

## Gedächtnis

Einstellungen, Gedächtnis & Personalisierung, **Profil exportieren** speichert, was VOOL über dich weiß, als
Datei mit dem Namen `vool-memory-` und dem Datum. Sie enthält nie einen Schlüssel oder ein Passwort. Sie ist
eine Kopie zum Lesen und Aufbewahren: VOOL hat keinen Import dafür.

## Schlüssel für Modellanbieter

Anbieterschlüssel sind in keinem Export enthalten. Füge sie auf einem neuen Computer in den Einstellungen
wieder hinzu.

## Wallets

* **Eine VOOL-Wallet, die für ein Netzwerk erstellt wurde**, zeigt während der Einrichtung eine Sicherung des
  privaten Schlüssels, und nur dann. Bewahre sie offline auf: Wer sie hat, kontrolliert die Wallet. Wenn du
  die PIN oder das Passwort der Wallet vergisst, stellt **Forgot PIN or password?** an der Wallet unter
  Einstellungen, Krypto den Zugang mit dieser Sicherung wieder her, und du wählst eine neue PIN oder ein neues
  Passwort.
* **Die alte Tasche** wird über ihre Wiederherstellungsphrase gesichert, die einmal angezeigt wurde, als sie
  erstellt wurde. **Restore from phrase…** unter Einstellungen, Krypto, Andere Konten holt sie aus der Phrase
  zurück. Sie kann ihren privaten Schlüssel auch mit **Export private key** nach Phantom oder MetaMask
  exportieren; das öffnet sich nur mit Touch ID oder deinem Mac-Passwort.
* Eine Wallet, die du nur beobachtest, oder eine, die du aus einer anderen App verbunden hast, hat keinen
  Schlüssel in VOOL: Hier gibt es nichts zu sichern.

Siehe [Wallet](../guides/wallet.md) für das vollständige Bild.

## Wenn ein Update fehlschlägt

Ein Update, das bei irgendeinem Schritt fehlschlägt, lässt die App, wie sie war. Schlägt das Übertragen
deiner Daten auf die neue Version fehl, werden deine Daten wiederhergestellt, wie sie waren, und VOOL startet
mit der vorherigen Version. Siehe [Updates und Migration](../reference/updates-migration.md).
