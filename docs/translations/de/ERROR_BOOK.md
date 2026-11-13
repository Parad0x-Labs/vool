---
translation: machine
source_sha256: 5a872f9502c66828af4dfb1e7e687226facc2aaad8cb6544054c90e1fafe8d7d
---

# VOOL-Fehlerbuch

<!-- GENERATED FILE — do not hand-edit. Regenerate with `python -m tools.generate_error_book`. -->

<!-- GENERATED FILE: the app's own texts for each code, and the words in tools/docs/error_book_words.json. Regenerate with tools/docs/translate_error_book.py, never by hand. -->

Alle Fehler, die VOOL dir zeigen kann, erzeugt aus dem eigenen Fehlerkatalog der App: Die Codes, Meldungen und nächsten Schritte weiter unten sind genau das, was die App anzeigt.

- **Schema:** `vool.fault.v1`
- **Katalogversion:** `1`
- **Katalog-Prüfsumme:** `2d2c27206b99b26ca524806ec42ac41eac2f0d28a110a74f6ac5ef42006f313d`
- **Anzahl der Fehler:** 59

Jeder Code ist fest und wird nie für eine andere Bedeutung wiederverwendet. Jeder Code hat weiter unten einen eigenen Abschnitt, dessen Überschrift der Code selbst ist. Wenn du diese Seite also nach dem Code durchsuchst, so wie du ihn bekommen hast, findest du seinen Abschnitt, und ein Link auf den Abschnitt ist die Adresse dieser Seite, gefolgt von `#` und dem Code mit Bindestrichen statt Unterstrichen. Jeder Abschnitt sagt, was passiert ist, was womöglich schon passiert ist, was als Nächstes zu tun ist und ob ein erneuter Versuch helfen kann. Steht ein Code, den du bekommen hast, nicht in diesem Buch, stammt er nicht aus dieser Version von VOOL: Prüfe, welche Version ihn angezeigt hat, oder melde ihn mit dem Code, so wie er geschrieben ist.

## Sicherheit

| Code | Titel | Schweregrad | Was womöglich schon passiert ist |
|---|---|---|---|
| [`confinement_refusal`](#confinement-refusal) | Änderung außerhalb des erlaubten Bereichs | hoch | Nichts wurde gesendet, signiert oder geändert |
| [`credential_failure`](#credential-failure) | Gespeicherter Schlüssel ließ sich nicht lesen | hoch | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_backup_unavailable`](#wallet-backup-unavailable) | Sicherung bereits einmal angezeigt | hoch | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_caller_refused`](#wallet-caller-refused) | Nicht aus VOOLs eigenem Fenster gestartet | hoch | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_card_data_refused`](#wallet-card-data-refused) | Kartendaten abgelehnt | hoch | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_chain_identity_mismatch`](#wallet-chain-identity-mismatch) | Identität des Ketten-Endpunkts nicht bewiesen | hoch | Eine Anfrage hat einen externen Dienst erreicht; es wurde keine Zahlung ausgeführt |
| [`wallet_environment_inactive`](#wallet-environment-inactive) | Falsche Netzwerkumgebung | mittel | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_export_pin_not_accepted`](#wallet-export-pin-not-accepted) | Export wird nur per Geräte-Authentifizierung entsperrt | hoch | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_export_refused`](#wallet-export-refused) | Alter Exportweg eingestellt | hoch | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_export_unavailable`](#wallet-export-unavailable) | Export für dieses Wallet nicht verfügbar | hoch | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_legacy_surface_retired`](#wallet-legacy-surface-retired) | Alter Geldweg eingestellt | hoch | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_network_disabled`](#wallet-network-disabled) | Netzwerk für dieses Wallet nicht deklariert | hoch | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_outbound_refused`](#wallet-outbound-refused) | Anfrage außerhalb der genehmigten Endpunkte | hoch | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_quote_mismatch`](#wallet-quote-mismatch) | Freigabe passt nicht mehr zur Vorschau | hoch | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_recovery_refused`](#wallet-recovery-refused) | Wiederherstellungsnachweis passt nicht zu diesem Wallet | hoch | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_signature_invalid`](#wallet-signature-invalid) | Signatur passte nicht zum Schlüssel des Wallets | hoch | Eine Anfrage hat einen externen Dienst erreicht; es wurde keine Zahlung ausgeführt |
| [`wallet_unlock_throttled`](#wallet-unlock-throttled) | Wallet nach fehlgeschlagenen Entsperrungen gesperrt | hoch | Nichts wurde gesendet, signiert oder geändert |

## Integrität

| Code | Titel | Schweregrad | Was womöglich schon passiert ist |
|---|---|---|---|
| [`evidence_corruption`](#evidence-corruption) | Antwortbelege bestanden die Konsistenzprüfung nicht | hoch | Ein lokaler Eintrag oder eine lokale Änderung ist erfolgt |
| [`integrity_verification_failure`](#integrity-verification-failure) | Eintrag bestand seine Manipulationsprüfung nicht | kritisch | Ein lokaler Eintrag oder eine lokale Änderung ist erfolgt |
| [`unsupported_claim`](#unsupported-claim) | Nicht gestützte Aussagen zurückgehalten | mittel | Ein lokaler Eintrag oder eine lokale Änderung ist erfolgt |
| [`wallet_duplicate_payment`](#wallet-duplicate-payment) | Zahlung bereits vorgeschlagen oder gesendet | mittel | Ein lokaler Eintrag oder eine lokale Änderung ist erfolgt |

## Richtlinie

| Code | Titel | Schweregrad | Was womöglich schon passiert ist |
|---|---|---|---|
| [`evm_pocket_custody_unavailable`](#evm-pocket-custody-unavailable) | Alte Tasche nur für Solana | niedrig | Nichts wurde gesendet, signiert oder geändert |
| [`permission_denied`](#permission-denied) | Berechtigung verweigert | mittel | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_acknowledgement_required`](#wallet-acknowledgement-required) | Bestätigung erforderlich | niedrig | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_amount_invalid`](#wallet-amount-invalid) | Betrag für diese Münze nicht exakt | niedrig | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_approval_rejected`](#wallet-approval-rejected) | Freigabe nicht angenommen | mittel | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_confirmation_required`](#wallet-confirmation-required) | Getippte Bestätigung erforderlich | niedrig | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_credential_mismatch`](#wallet-credential-mismatch) | Eingaben stimmten nicht überein | niedrig | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_dependency_unavailable`](#wallet-dependency-unavailable) | EVM-Bibliotheken nicht installiert | mittel | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_device_auth_denied`](#wallet-device-auth-denied) | Entsperrung per Gerät abgelehnt | niedrig | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_disabled`](#wallet-disabled) | Wallet ist ausgeschaltet | niedrig | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_insufficient_funds`](#wallet-insufficient-funds) | Guthaben deckt Betrag und Gebühr nicht | niedrig | Eine Anfrage hat einen externen Dienst erreicht; es wurde keine Zahlung ausgeführt |
| [`wallet_limit_exceeded`](#wallet-limit-exceeded) | Ausgabengrenze überschritten | mittel | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_not_found`](#wallet-not-found) | Wallet, Vorschlag oder Empfänger nicht erkannt | niedrig | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_password_invalid`](#wallet-password-invalid) | Wallet-Passwort ungültig | niedrig | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_paykit_unavailable`](#wallet-paykit-unavailable) | Erweiterung pay für Solana nicht installiert | mittel | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_payment_channel_off`](#wallet-payment-channel-off) | Zahlungskanal ausgeschaltet | niedrig | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_pin_invalid`](#wallet-pin-invalid) | PIN ungültig | niedrig | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_quote_expired`](#wallet-quote-expired) | Vorschau abgelaufen oder ersetzt | niedrig | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_recipient_refused`](#wallet-recipient-refused) | Empfänger kann dies nicht sicher empfangen | mittel | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_request_ambiguous`](#wallet-request-ambiguous) | Mehrere Konten oder Netzwerke passen | niedrig | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_send_needs_vool_wallet`](#wallet-send-needs-vool-wallet) | Zum Senden ist ein VOOL Wallet nötig | niedrig | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_setup_state_invalid`](#wallet-setup-state-invalid) | Einrichtungsschritt passt gerade nicht | niedrig | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_signing_unavailable`](#wallet-signing-unavailable) | Wallet kann von hier nicht signieren | mittel | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_storage_class_refused`](#wallet-storage-class-refused) | Schlüsselspeicher nur im Arbeitsspeicher | mittel | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_x402_cap_exceeded`](#wallet-x402-cap-exceeded) | Bezahlte Ressource über der automatischen Obergrenze | mittel | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_x402_sdk_unavailable`](#wallet-x402-sdk-unavailable) | Erweiterung x402 nicht installiert | mittel | Nichts wurde gesendet, signiert oder geändert |
| [`x402_scheme_unavailable`](#x402-scheme-unavailable) | Zahlungsschema nicht unterstützt | niedrig | Nichts wurde gesendet, signiert oder geändert |

## Verfügbarkeit

| Code | Titel | Schweregrad | Was womöglich schon passiert ist |
|---|---|---|---|
| [`provider_exhausted`](#provider-exhausted) | Limit des Anbieters erreicht | mittel | Eine Anfrage hat einen externen Dienst erreicht; es wurde keine Zahlung ausgeführt |
| [`provider_unavailable`](#provider-unavailable) | Anbieter nicht erreichbar | mittel | Nichts wurde gesendet, signiert oder geändert |
| [`timeout`](#timeout) | Zeitüberschreitung | mittel | Ergebnis unbekannt — prüfe den Zustand, bevor du es erneut versuchst |
| [`tool_unavailable`](#tool-unavailable) | Werkzeug nicht verfügbar | niedrig | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_broadcast_failed`](#wallet-broadcast-failed) | Zahlung konnte nicht übertragen werden | mittel | Ergebnis unbekannt — prüfe den Zustand, bevor du es erneut versuchst |
| [`wallet_device_auth_unavailable`](#wallet-device-auth-unavailable) | Entsperrung per Gerät nicht verfügbar | mittel | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_quote_unavailable`](#wallet-quote-unavailable) | Guthaben oder Gebühr unbekannt | mittel | Eine Anfrage hat einen externen Dienst erreicht; es wurde keine Zahlung ausgeführt |
| [`wallet_security_key_needed`](#wallet-security-key-needed) | Sicherheitsschlüssel nötig | niedrig | Nichts wurde gesendet, signiert oder geändert |
| [`wallet_simulation_failed`](#wallet-simulation-failed) | Zahlung bestand die Simulation nicht | mittel | Eine Anfrage hat einen externen Dienst erreicht; es wurde keine Zahlung ausgeführt |

## Abbruch

| Code | Titel | Schweregrad | Was womöglich schon passiert ist |
|---|---|---|---|
| [`cancelled`](#cancelled) | Abgebrochen | Info | Ergebnis unbekannt — prüfe den Zustand, bevor du es erneut versuchst |

## Intern

| Code | Titel | Schweregrad | Was womöglich schon passiert ist |
|---|---|---|---|
| [`unknown`](#unknown) | Unerwarteter Fehler | mittel | Ergebnis unbekannt — prüfe den Zustand, bevor du es erneut versuchst |

## Referenz nach Code

Alle Codes in alphabetischer Reihenfolge. Die Überschrift des Abschnitts ist der feste Anker des Codes.

<a name="cancelled"></a>

### cancelled

**Abgebrochen**

- **Was passiert ist:** Das wurde abgebrochen und daher nicht abgeschlossen.
- **Was womöglich schon passiert ist:** Ergebnis unbekannt — prüfe den Zustand, bevor du es erneut versuchst
- **Was als Nächstes zu tun ist:** Kein Handeln nötig; beginne erneut, wenn es erledigt sein soll.
- **Erneuter Versuch:** Sende dieselbe Anfrage nicht unverändert erneut.
- **Schweregrad:** Info · **Kategorie:** Abbruch

<a name="confinement-refusal"></a>

### confinement_refusal

**Änderung außerhalb des erlaubten Bereichs**

- **Was passiert ist:** Eine Dateiänderung außerhalb des erlaubten Bereichs wurde blockiert. Außerhalb des Bereichs wurde nichts berührt.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Ist diese Änderung gewollt, fordere sie ausdrücklich an, damit sie innerhalb des autorisierten Bereichs liegt.
- **Erneuter Versuch:** Sende dieselbe Anfrage nicht unverändert erneut.
- **Schweregrad:** hoch · **Kategorie:** Sicherheit · **Sicherheitsrelevant:** ja

<a name="credential-failure"></a>

### credential_failure

**Gespeicherter Schlüssel ließ sich nicht lesen**

- **Was passiert ist:** Ein gespeicherter Schlüssel ließ sich nicht lesen. Gib ihn in den Einstellungen neu ein, um fortzufahren.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Der Tresoreintrag ließ sich nicht entschlüsseln; speichere das Berechtigungsdatum neu oder prüfe den Tresorschlüssel.
- **Erneuter Versuch:** Sende dieselbe Anfrage nicht unverändert erneut.
- **Schweregrad:** hoch · **Kategorie:** Sicherheit · **Sicherheitsrelevant:** ja

<a name="evidence-corruption"></a>

### evidence_corruption

**Antwortbelege bestanden die Konsistenzprüfung nicht**

- **Was passiert ist:** Die Belege dieser Antwort bestanden die Konsistenzprüfung nicht, betrachte die Antwort daher als ungeprüft.
- **Was womöglich schon passiert ist:** Ein lokaler Eintrag oder eine lokale Änderung ist erfolgt
- **Was als Nächstes zu tun ist:** Untersuche die Ausführungskasse dieses Zugs gegen den Ereignisstrom der Laufzeit.
- **Erneuter Versuch:** Sende dieselbe Anfrage nicht unverändert erneut.
- **Schweregrad:** hoch · **Kategorie:** Integrität · **Sicherheitsrelevant:** ja

<a name="evm-pocket-custody-unavailable"></a>

### evm_pocket_custody_unavailable

**Alte Tasche nur für Solana**

- **Was passiert ist:** Die alte Tasche, also das Wallet aus einer Wiederherstellungsphrase, hält nur einen Solana-Schlüssel, daher lässt sie sich in einem EVM-Netzwerk weder erstellen noch wiederherstellen. Es wurde nichts erstellt. Für ein EVM-Netzwerk erstelle unter Einstellungen, Krypto eine VOOL-Wallet in diesem Netzwerk, füge das Konto nur zum Ansehen hinzu oder verbinde dein eigenes EVM-Wallet.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Die alte Tasche versiegelt einen einzigen Solana-Seed aus ihrer Wiederherstellungsphrase. In einem EVM-Netzwerk nutze ein Crypto Pilot Wallet („Create wallet“ bei diesem Netzwerk unter Einstellungen, Krypto, Netze und Wallets), ein Konto nur zum Ansehen oder ein externes EVM-Wallet (EIP-1193), das seinen Schlüssel selbst verwahrt.
- **Erneuter Versuch:** Sende dieselbe Anfrage nicht unverändert erneut.
- **Schweregrad:** niedrig · **Kategorie:** Richtlinie

<a name="integrity-verification-failure"></a>

### integrity_verification_failure

**Eintrag bestand seine Manipulationsprüfung nicht**

- **Was passiert ist:** Ein Eintrag bestand seine Manipulationsprüfung nicht. Nichts wurde stillschweigend akzeptiert.
- **Was womöglich schon passiert ist:** Ein lokaler Eintrag oder eine lokale Änderung ist erfolgt
- **Was als Nächstes zu tun ist:** Behandle die betroffene Belegkette als kompromittiert; untersuche sie, bevor du ihren Einträgen vertraust.
- **Erneuter Versuch:** Sende dieselbe Anfrage nicht unverändert erneut.
- **Schweregrad:** kritisch · **Kategorie:** Integrität · **Sicherheitsrelevant:** ja

<a name="permission-denied"></a>

### permission_denied

**Berechtigung verweigert**

- **Was passiert ist:** Diese Aktion war nicht erlaubt, also wurde nichts geändert.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Öffne die Einstellungen, wähle „Privatsphäre & Berechtigungen“, und stelle „Was VOOL allein darf“ ein, um zu ändern, was diese Aktion regelt.
- **Erneuter Versuch:** Sende dieselbe Anfrage nicht unverändert erneut.
- **Schweregrad:** mittel · **Kategorie:** Richtlinie · **Sicherheitsrelevant:** ja

<a name="provider-exhausted"></a>

### provider_exhausted

**Limit des Anbieters erreicht**

- **Was passiert ist:** Das Nutzungslimit des Modellanbieters ist erreicht. Versuche es später wieder.
- **Was womöglich schon passiert ist:** Eine Anfrage hat einen externen Dienst erreicht; es wurde keine Zahlung ausgeführt
- **Was als Nächstes zu tun ist:** Prüfe Kontingent, Ratenlimits und Abrechnung des konfigurierten Anbieters.
- **Erneuter Versuch:** Versuche es später erneut: Was das verhindert hat, legt sich mit der Zeit.
- **Schweregrad:** mittel · **Kategorie:** Verfügbarkeit

<a name="provider-unavailable"></a>

### provider_unavailable

**Anbieter nicht erreichbar**

- **Was passiert ist:** Der Modellanbieter war nicht erreichbar. Versuche es gleich noch einmal.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Prüfe den Status des Anbieters, die Netzerreichbarkeit und die konfigurierten Endpunkte.
- **Erneuter Versuch:** Versuche es später erneut: Was das verhindert hat, legt sich mit der Zeit.
- **Schweregrad:** mittel · **Kategorie:** Verfügbarkeit

<a name="timeout"></a>

### timeout

**Zeitüberschreitung**

- **Was passiert ist:** Das hat zu lange gedauert und wurde gestoppt. Ein erneuter Versuch kann gelingen.
- **Was womöglich schon passiert ist:** Ergebnis unbekannt — prüfe den Zustand, bevor du es erneut versuchst
- **Was als Nächstes zu tun ist:** Wiederholen sich Zeitüberschreitungen, prüfe Netzlatenz und Ansprechbarkeit des Anbieters.
- **Erneuter Versuch:** Ein erneuter Versuch kann jetzt gelingen.
- **Schweregrad:** mittel · **Kategorie:** Verfügbarkeit

<a name="tool-unavailable"></a>

### tool_unavailable

**Werkzeug nicht verfügbar**

- **Was passiert ist:** Dieses Werkzeug ist gerade nicht verfügbar, daher wurde dieser Teil nicht erledigt. Es muss erst aktiviert werden, bevor du es erneut versuchst.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Öffne die Einstellungen, wähle „Privatsphäre & Berechtigungen“, und stelle „Was VOOL allein darf“ ein, oder wähle einen anderen Weg.
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** niedrig · **Kategorie:** Verfügbarkeit

<a name="unknown"></a>

### unknown

**Unerwarteter Fehler**

- **Was passiert ist:** Auf diesem Computer ist beim Verarbeiten etwas schiefgegangen. Du kannst es erneut versuchen.
- **Was womöglich schon passiert ist:** Ergebnis unbekannt — prüfe den Zustand, bevor du es erneut versuchst
- **Was als Nächstes zu tun ist:** Sieh die redigierte Ursachenkette des Fehlers in der Diagnose; eine unbekannte Ursache wird nie blind wiederholt.
- **Erneuter Versuch:** Sende dieselbe Anfrage nicht unverändert erneut.
- **Schweregrad:** mittel · **Kategorie:** intern

<a name="unsupported-claim"></a>

### unsupported_claim

**Nicht gestützte Aussagen zurückgehalten**

- **Was passiert ist:** Eine oder mehrere Aussagen wurden von den abgerufenen Quellen nicht gestützt und zurückgehalten.
- **Was womöglich schon passiert ist:** Ein lokaler Eintrag oder eine lokale Änderung ist erfolgt
- **Was als Nächstes zu tun ist:** Kein Handeln nötig — das Belegtor arbeitet. Häuft es sich, prüfe die Qualität der Suche.
- **Erneuter Versuch:** Sende dieselbe Anfrage nicht unverändert erneut.
- **Schweregrad:** mittel · **Kategorie:** Integrität

<a name="wallet-acknowledgement-required"></a>

### wallet_acknowledgement_required

**Bestätigung erforderlich**

- **Was passiert ist:** Dieser Schritt braucht zuerst deine Bestätigung, daher wurde nichts signiert oder gesendet. Lies, was das Wallet über diese Aktion sagt, bestätige es genau so und versuche es dann erneut.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Wenn eine Freigabe dauerhafte Ausgaberechte erteilen, den Besitz ändern oder ein Upgrade ausführen würde, oder das Wallet eine Abweichung findet oder die Anfrage nicht lesen kann, bittet es dich zuerst, seinen genauen Bestätigungstext einzutippen; nichts Allgemeines gilt an seiner Stelle.
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** niedrig · **Kategorie:** Richtlinie

<a name="wallet-amount-invalid"></a>

### wallet_amount_invalid

**Betrag für diese Münze nicht exakt**

- **Was passiert ist:** Dieser Betrag ist kein exakter Betrag, den diese Münze halten kann, daher wurde nichts vorgeschlagen. Prüfe den Betrag und versuche es erneut.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Sende einen schlichten Dezimalbetrag, nicht genauer als die Münze erlaubt und innerhalb der Pro-Überweisung-Grenze des Piloten.
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** niedrig · **Kategorie:** Richtlinie

<a name="wallet-approval-rejected"></a>

### wallet_approval_rejected

**Freigabe nicht angenommen**

- **Was passiert ist:** Die Freigabe dieser Zahlung wurde nicht angenommen, daher wurde nichts signiert oder gesendet.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Gib die exakten Parameter mit der PIN oder Biometrie des Eigentümers frei; Freigaben übertragen sich nicht zwischen Parametern.
- **Erneuter Versuch:** Sende dieselbe Anfrage nicht unverändert erneut.
- **Schweregrad:** mittel · **Kategorie:** Richtlinie · **Sicherheitsrelevant:** ja

<a name="wallet-backup-unavailable"></a>

### wallet_backup_unavailable

**Sicherung bereits einmal angezeigt**

- **Was passiert ist:** Der Sicherungsschlüssel dieses Wallets wurde bereits einmal angezeigt und kann daher nicht erneut angezeigt werden. Wenn du ihn nicht gesichert hast, brich die Einrichtung ab und sende keine Gelder an diese Adresse.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Eine Krypto-Pilot-Sicherung wird genau einmal während der Einrichtung gezeigt; es gibt kein zweites Zeigen und keinen Export.
- **Erneuter Versuch:** Sende dieselbe Anfrage nicht unverändert erneut.
- **Schweregrad:** hoch · **Kategorie:** Sicherheit · **Sicherheitsrelevant:** ja

<a name="wallet-broadcast-failed"></a>

### wallet_broadcast_failed

**Zahlung konnte nicht übertragen werden**

- **Was passiert ist:** Die Zahlung konnte nicht übertragen werden. Prüfe ihren Status, bevor du es erneut versuchst.
- **Was womöglich schon passiert ist:** Ergebnis unbekannt — prüfe den Zustand, bevor du es erneut versuchst
- **Was als Nächstes zu tun ist:** Prüfe den RPC-Endpunkt des Netzwerks und den Beleg des Antrags; die Ausgabe wurde nicht als gesendet verzeichnet.
- **Erneuter Versuch:** Versuche es später erneut: Was das verhindert hat, legt sich mit der Zeit.
- **Schweregrad:** mittel · **Kategorie:** Verfügbarkeit

<a name="wallet-caller-refused"></a>

### wallet_caller_refused

**Nicht aus VOOLs eigenem Fenster gestartet**

- **Was passiert ist:** Diese Wallet-Aktion kann nur aus VOOLs eigenem Fenster auf diesem Computer gestartet werden, daher wurde nichts geändert, signiert oder gesendet. Öffne VOOL und versuche es dort.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Steuere vertrauenswürdige Wallet-Türen über die ausgelieferte Seite oder das native Fenster; eine Seite anderer Herkunft, ein Frame oder ein Plugin wird abgelehnt.
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** hoch · **Kategorie:** Sicherheit · **Sicherheitsrelevant:** ja

<a name="wallet-card-data-refused"></a>

### wallet_card_data_refused

**Kartendaten abgelehnt**

- **Was passiert ist:** Kartennummern und Prüfziffern werden hier niemals gespeichert; akzeptiert wird nur ein Anbieter-Token. Es wurde nichts gespeichert.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Tokenisiere die Karte beim Anbieter und registriere die Token-Referenz.
- **Erneuter Versuch:** Sende dieselbe Anfrage nicht unverändert erneut.
- **Schweregrad:** hoch · **Kategorie:** Sicherheit · **Sicherheitsrelevant:** ja

<a name="wallet-chain-identity-mismatch"></a>

### wallet_chain_identity_mismatch

**Identität des Ketten-Endpunkts nicht bewiesen**

- **Was passiert ist:** Der Ketten-Endpunkt hat die Identität des Netzwerks, für das er sich ausgibt, nicht bewiesen, daher wurde nichts signiert oder gesendet.
- **Was womöglich schon passiert ist:** Eine Anfrage hat einen externen Dienst erreicht; es wurde keine Zahlung ausgeführt
- **Was als Nächstes zu tun ist:** Prüfe den RPC-Endpunkt für das Netz; eine lügende Chain-ID oder Genesis wird vor jeder Zahlung abgelehnt.
- **Erneuter Versuch:** Sende dieselbe Anfrage nicht unverändert erneut.
- **Schweregrad:** hoch · **Kategorie:** Sicherheit · **Sicherheitsrelevant:** ja

<a name="wallet-confirmation-required"></a>

### wallet_confirmation_required

**Getippte Bestätigung erforderlich**

- **Was passiert ist:** Dieser Schritt braucht zuerst die getippte Bestätigung, daher wurde nichts geändert. Bestätige und versuche es erneut.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Lies die Warnung und tippe die exakte Bestätigungsphrase, um fortzufahren.
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** niedrig · **Kategorie:** Richtlinie

<a name="wallet-credential-mismatch"></a>

### wallet_credential_mismatch

**Eingaben stimmten nicht überein**

- **Was passiert ist:** Die beiden Eingaben stimmten nicht überein, daher wurde nichts erstellt. Gib zweimal dieselbe PIN oder dasselbe Passwort ein und versuche es erneut.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Ein Krypto-Pilot-Berechtigungsdatensatz wird bestätigt, bevor irgendein Schlüsselmaterial existiert.
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** niedrig · **Kategorie:** Richtlinie

<a name="wallet-dependency-unavailable"></a>

### wallet_dependency_unavailable

**EVM-Bibliotheken nicht installiert**

- **Was passiert ist:** Dieser Build kann EVM-Signaturen oder ABI-Kodierung nicht durchführen: Die Ethereum-Bibliotheken sind auf diesem Rechner nicht installiert. Es wurde nichts signiert oder gesendet. Solana, Verwahrung und Ausgabengrenzen sind nicht betroffen; nach Installation der Bibliotheken versuche es erneut.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Installiere die EVM-Erweiterungen (eth-abi, eth-utils, eth-account) für die EVM-Spalten; jede andere Wallet-Funktion läuft ohne sie.
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** mittel · **Kategorie:** Richtlinie

<a name="wallet-device-auth-denied"></a>

### wallet_device_auth_denied

**Entsperrung per Gerät abgelehnt**

- **Was passiert ist:** Touch ID oder dein Mac-Passwort wurde abgelehnt oder hat dieses Wallet nicht geöffnet, daher wurde nichts erstellt, geändert, entsperrt, offengelegt, signiert oder gesendet. Versuche es erneut.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Versuche es erneut und bestätige die Abfrage von Touch ID oder dem Mac-Passwort, die VOOL zeigt. Ein Wallet, das mit Touch ID freigibt, hat keine eigene PIN und kein eigenes Passwort, die es stattdessen nutzen könnte. Schlägt die Abfrage weiter fehl, stelle das Wallet aus seiner Wiederherstellungsphrase wieder her (einen Crypto Pilot aus der bei der Einrichtung angezeigten Sicherung) und gib ihm eine PIN oder ein Passwort; ein Export des privaten Schlüssels braucht weiterhin Touch ID oder das Mac-Passwort.
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** niedrig · **Kategorie:** Richtlinie

<a name="wallet-device-auth-unavailable"></a>

### wallet_device_auth_unavailable

**Entsperrung per Gerät nicht verfügbar**

- **Was passiert ist:** Touch ID oder dein Mac-Passwort ist für dieses Wallet hier nicht verfügbar, daher wurde nichts erstellt, geändert, entsperrt, offengelegt, signiert oder gesendet. Versuche es erneut, sobald es verfügbar ist.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Ein neues Wallet kann stattdessen mit einer eigenen PIN oder einem eigenen Passwort freigeben. Ein Wallet, das mit Touch ID freigibt, hat keine eigene PIN und kein eigenes Passwort: Bleiben Touch ID und das Mac-Passwort nicht verfügbar, stelle es aus seiner Wiederherstellungsphrase wieder her (einen Crypto Pilot aus der bei der Einrichtung angezeigten Sicherung) und gib ihm eine PIN oder ein Passwort. Ein Export des privaten Schlüssels öffnet sich nur mit Touch ID oder dem Mac-Passwort, also stelle das Wallet auf einem Mac wieder her, auf dem sie funktionieren, und exportiere es dort.
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** mittel · **Kategorie:** Verfügbarkeit

<a name="wallet-disabled"></a>

### wallet_disabled

**Wallet ist ausgeschaltet**

- **Was passiert ist:** Das Wallet ist ausgeschaltet, daher wurde keine Zahlung vorgeschlagen oder gesendet. Schalte „Krypto einschalten“ unter Einstellungen, Krypto ein und versuche es dann erneut.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Schalte „Krypto einschalten“ unter Einstellungen, Krypto nur ein, wenn du auf dieser Installation Zahlungen willst. Ist die Variable VOOL_WALLET_ENABLED gesetzt, schaltet sie das Wallet ein oder aus, egal was der Schalter zeigt.
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** niedrig · **Kategorie:** Richtlinie

<a name="wallet-duplicate-payment"></a>

### wallet_duplicate_payment

**Zahlung bereits vorgeschlagen oder gesendet**

- **Was passiert ist:** Diese Zahlung wurde bereits vorgeschlagen oder gesendet, daher wurde sie nicht erneut gesendet.
- **Was womöglich schon passiert ist:** Ein lokaler Eintrag oder eine lokale Änderung ist erfolgt
- **Was als Nächstes zu tun ist:** Prüfe den Beleg des bestehenden Antrags; nutze für eine wirklich neue Zahlung einen neuen Idempotenz-Schlüssel.
- **Erneuter Versuch:** Sende dieselbe Anfrage nicht unverändert erneut.
- **Schweregrad:** mittel · **Kategorie:** Integrität · **Sicherheitsrelevant:** ja

<a name="wallet-environment-inactive"></a>

### wallet_environment_inactive

**Falsche Netzwerkumgebung**

- **Was passiert ist:** Dieses Netzwerk gehört zur anderen Netzwerkumgebung (Mainnet oder Testnetze), daher wurde nichts erstellt, vorgeschlagen oder signiert. Wechsle unter Einstellungen, Crypto, Entwickleroptionen und versuche es dann erneut.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Wähle die Netzumgebung bewusst in den Krypto-Einstellungen; Konten, Guthaben und Belege wandern nie zwischen Umgebungen.
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** mittel · **Kategorie:** Sicherheit · **Sicherheitsrelevant:** ja

<a name="wallet-export-pin-not-accepted"></a>

### wallet_export_pin_not_accepted

**Export wird nur per Geräte-Authentifizierung entsperrt**

- **Was passiert ist:** Der Export wird nie mit einer PIN, einem Passwort oder einer Passphrase entsperrt, nur per Geräte-Authentifizierung (Touch ID oder dein Mac-Passwort), daher wurde nichts offengelegt. Versuche es ohne PIN oder Passwort erneut.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Der Export wird absichtlich nur per Geräte-Authentifizierung entsperrt; eine PIN, ein Passwort oder eine Passphrase in der Anfrage wird abgelehnt, bevor sie überhaupt geprüft wird.
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** hoch · **Kategorie:** Sicherheit · **Sicherheitsrelevant:** ja

<a name="wallet-export-refused"></a>

### wallet_export_refused

**Alter Exportweg eingestellt**

- **Was passiert ist:** Dieser Weg, einen privaten Schlüssel zu exportieren, ist eingestellt, daher wurde nichts offengelegt. Ein Wallet, das den Export erlaubt, hat unter Einstellungen, Krypto die Schaltfläche „Export private key“, die sich nur mit Touch ID oder deinem Mac-Passwort öffnet.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Nutze „Export private key“ unter Einstellungen, Krypto, Andere Konten: Nur die alte Tasche lässt sich exportieren, und nur mit Touch ID oder dem Mac-Passwort. Ein Crypto Pilot Wallet wird nie exportiert; es wird aus der bei der Einrichtung angezeigten Sicherung wiederhergestellt.
- **Erneuter Versuch:** Sende dieselbe Anfrage nicht unverändert erneut.
- **Schweregrad:** hoch · **Kategorie:** Sicherheit · **Sicherheitsrelevant:** ja

<a name="wallet-export-unavailable"></a>

### wallet_export_unavailable

**Export für dieses Wallet nicht verfügbar**

- **Was passiert ist:** Dieses Wallet lässt sich so nicht exportieren: Es ist nur zum Ansehen oder hält auf diesem Rechner keinen Schlüssel, seine einzige Sicherung wurde bei der Einrichtung angezeigt und es wird nie exportiert, VOOL exportiert nicht zur gewählten Wallet-App, oder es ist eine alte Tasche aus einer früheren Version, die erst aus ihrer Wiederherstellungsphrase wiederhergestellt werden muss, bevor sie zu MetaMask exportiert. Nichts wurde offengelegt.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Exportieren lässt sich nur eine auf diesem Rechner versiegelte alte Tasche, zu Phantom oder MetaMask, und nur mit Touch ID oder dem Mac-Passwort. Ein Crypto Pilot Wallet wird aus der bei der Einrichtung angezeigten Sicherung wiederhergestellt. Eine alte Tasche aus einer früheren Version lässt sich zu MetaMask exportieren, nachdem sie aus ihrer Wiederherstellungsphrase wiederhergestellt wurde.
- **Erneuter Versuch:** Sende dieselbe Anfrage nicht unverändert erneut.
- **Schweregrad:** hoch · **Kategorie:** Sicherheit · **Sicherheitsrelevant:** ja

<a name="wallet-insufficient-funds"></a>

### wallet_insufficient_funds

**Guthaben deckt Betrag und Gebühr nicht**

- **Was passiert ist:** Das Guthaben deckt den Betrag plus das mögliche Maximum der Netzwerkgebühr nicht, daher wurde nichts vorbereitet. Senke den Betrag oder lade Guthaben auf und versuche es erneut.
- **Was womöglich schon passiert ist:** Eine Anfrage hat einen externen Dienst erreicht; es wurde keine Zahlung ausgeführt
- **Was als Nächstes zu tun ist:** Ausgebbar lässt andere laufende Reservierungen außen vor; ein Solana-Rest muss ohne Nullen enden und mindestens die Miete decken.
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** niedrig · **Kategorie:** Richtlinie

<a name="wallet-legacy-surface-retired"></a>

### wallet_legacy_surface_retired

**Alter Geldweg eingestellt**

- **Was passiert ist:** Dieser Geldweg ist eingestellt: jede Zahlung geht über den Vorschlags- und Freigabefluss des Wallets. Es wurde nichts signiert oder gesendet.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Nutze die Wallet-Werkzeuge (wallet.propose) oder die Wallet-API; der alte Signierer, Ausgeber und Exporteur dienen nicht mehr.
- **Erneuter Versuch:** Sende dieselbe Anfrage nicht unverändert erneut.
- **Schweregrad:** hoch · **Kategorie:** Sicherheit · **Sicherheitsrelevant:** ja

<a name="wallet-limit-exceeded"></a>

### wallet_limit_exceeded

**Ausgabengrenze überschritten**

- **Was passiert ist:** Diese Zahlung liegt über einer Ausgabengrenze und wurde daher nicht gesendet.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Prüfe vor dem erneuten Versuch die Grenzen je Transaktion, je Tag und je Empfänger.
- **Erneuter Versuch:** Sende dieselbe Anfrage nicht unverändert erneut.
- **Schweregrad:** mittel · **Kategorie:** Richtlinie · **Sicherheitsrelevant:** ja

<a name="wallet-network-disabled"></a>

### wallet_network_disabled

**Netzwerk für dieses Wallet nicht deklariert**

- **Was passiert ist:** Dieses Netzwerk gehört nicht zu den Netzwerken, die dieses Wallet für diese Aktion deklariert, daher wurde nichts erstellt oder gesendet.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Nutze eine der erklärten Netz-Zeilen; unerklärte Ketten, Aliasse und täuschend ähnliche Namen lehnt die Registrierung ab.
- **Erneuter Versuch:** Sende dieselbe Anfrage nicht unverändert erneut.
- **Schweregrad:** hoch · **Kategorie:** Sicherheit · **Sicherheitsrelevant:** ja

<a name="wallet-not-found"></a>

### wallet_not_found

**Wallet, Vorschlag oder Empfänger nicht erkannt**

- **Was passiert ist:** Dieses Wallet, dieser Vorschlag oder Empfänger wurde nicht erkannt, daher wurde nichts getan. Prüfe und versuche es erneut.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Prüfe die Wallet-Kennung, die Antrags-Kennung oder die Empfängeradresse und versuche es erneut.
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** niedrig · **Kategorie:** Richtlinie

<a name="wallet-outbound-refused"></a>

### wallet_outbound_refused

**Anfrage außerhalb der genehmigten Endpunkte**

- **Was passiert ist:** Diese Anfrage durfte die genehmigten Endpunkte des Wallets nicht verlassen, daher wurde keine Verbindung hergestellt.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Prüfe die erklärten RPC-Herkünfte und die Zahlungsressourcen-Richtlinie; private, Metadaten- und nicht freigegebene Hosts werden abgelehnt.
- **Erneuter Versuch:** Sende dieselbe Anfrage nicht unverändert erneut.
- **Schweregrad:** hoch · **Kategorie:** Sicherheit · **Sicherheitsrelevant:** ja

<a name="wallet-password-invalid"></a>

### wallet_password_invalid

**Wallet-Passwort ungültig**

- **Was passiert ist:** Das Passwort erfüllte die Richtlinie nicht oder hat das Wallet nicht entsperrt, daher wurde nichts signiert oder geändert. Prüfe das Passwort und versuche es erneut.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Wallet-Passwörter haben 10 bis 128 Zeichen und bestehen nicht nur aus Ziffern. Ein falsches Passwort lässt den Schlüssel versiegelt und zählt zur Sperre des Wallets nach zu vielen Fehlversuchen.
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** niedrig · **Kategorie:** Richtlinie

<a name="wallet-paykit-unavailable"></a>

### wallet_paykit_unavailable

**Erweiterung pay für Solana nicht installiert**

- **Was passiert ist:** Dieser Build kann keine Solana-Angebote über x402 oder MPP bezahlen: Die optionale Erweiterung pay (Solana pay-kit, Python 3.11 oder neuer) ist auf diesem Rechner nicht installiert. Es wurde nichts signiert oder gesendet. Jede andere Wallet-Funktion läuft ohne sie; nach der Installation versuche es erneut.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Installiere die optionale Erweiterung pay von VOOL unter Python 3.11 oder neuer, um pay-kit-Zahlungen zu aktivieren; die Wallet behält in jedem Fall ihren eigenen Signierer, ihre Ausgabengrenzen und ihre Freigabe.
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** mittel · **Kategorie:** Richtlinie

<a name="wallet-payment-channel-off"></a>

### wallet_payment_channel_off

**Zahlungskanal ausgeschaltet**

- **Was passiert ist:** Diese Zahlung läuft über einen Zahlungskanal, der ausgeschaltet ist, daher wurde nichts signiert oder gesendet. Schalte den Kanal unter Einstellungen, Krypto, Zahlungskanäle ein und versuche es dann erneut.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Schalte einen Zahlungskanal in den Krypto-Einstellungen nur ein, wenn Zahlungen darüber laufen sollen; die Obergrenze und deine Freigabe gelten auf jedem Kanal.
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** niedrig · **Kategorie:** Richtlinie

<a name="wallet-pin-invalid"></a>

### wallet_pin_invalid

**PIN ungültig**

- **Was passiert ist:** Die PIN erfüllte die Richtlinie nicht oder hat das Wallet nicht entsperrt, daher wurde nichts signiert. Prüfe die PIN und versuche es erneut.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Nutze eine 6- bis 12-stellige PIN; eine falsche PIN lässt den versiegelten Schlüssel unberührt.
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** niedrig · **Kategorie:** Richtlinie

<a name="wallet-quote-expired"></a>

### wallet_quote_expired

**Vorschau abgelaufen oder ersetzt**

- **Was passiert ist:** Diese Übertragungsvorschau ist abgelaufen oder wurde ersetzt, sie kann also nicht freigegeben werden und es wurde nichts gesendet. Aktualisiere die Vorschau und versuche es erneut.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Ein Angebot lebt kurz und wird durch eine Aktualisierung oder einen Umgebungswechsel ersetzt; eine Freigabe bindet immer das gegenwärtige.
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** niedrig · **Kategorie:** Richtlinie

<a name="wallet-quote-mismatch"></a>

### wallet_quote_mismatch

**Freigabe passt nicht mehr zur Vorschau**

- **Was passiert ist:** Die Freigabe passt nicht mehr zur gezeigten Übertragung, daher wurde nichts signiert oder gesendet. Beginne mit einer frischen Vorschau von vorn.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Eine Freigabe bindet den Prüfsummenwert eines Angebots; geändertes Konto, Kette, Empfänger, Betrag, Umgebung oder Gebühr verlangt eine neue Freigabe.
- **Erneuter Versuch:** Sende dieselbe Anfrage nicht unverändert erneut.
- **Schweregrad:** hoch · **Kategorie:** Sicherheit · **Sicherheitsrelevant:** ja

<a name="wallet-quote-unavailable"></a>

### wallet_quote_unavailable

**Guthaben oder Gebühr unbekannt**

- **Was passiert ist:** Das Netz gab keine vollständige Antwort zu Guthaben oder Gebühr dieser Übertragung, daher wurde keine Freigabe vorbereitet und nichts gesendet. Versuche es gleich noch einmal.
- **Was womöglich schon passiert ist:** Eine Anfrage hat einen externen Dienst erreicht; es wurde keine Zahlung ausgeführt
- **Was als Nächstes zu tun ist:** Unbekannte Ketten-Daten werden nie als null behandelt; prüfe den Endpunkt der Zeile und seinen Identitätsnachweis.
- **Erneuter Versuch:** Versuche es später erneut: Was das verhindert hat, legt sich mit der Zeit.
- **Schweregrad:** mittel · **Kategorie:** Verfügbarkeit

<a name="wallet-recipient-refused"></a>

### wallet_recipient_refused

**Empfänger kann dies nicht sicher empfangen**

- **Was passiert ist:** Dieser Empfänger kann diese Übertragung in diesem Netzwerk nicht sicher empfangen, daher wurde nichts vorbereitet. Prüfe die Adresse und versuche es erneut.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Abgelehnt: ein neues Solana-Konto unter der Miete, ein ausführbares oder außerhalb-der-Kurve-Konto, eine fehlerhafte EIP-55-Prüfsumme, die Nulladresse, Precompiles und Systemverträge.
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** mittel · **Kategorie:** Richtlinie

<a name="wallet-recovery-refused"></a>

### wallet_recovery_refused

**Wiederherstellungsnachweis passt nicht zu diesem Wallet**

- **Was passiert ist:** Dieser Wiederherstellungsnachweis passt nicht zu diesem Wallet, daher wurde nichts geändert: Der gespeicherte Schlüssel muss das Backup sein, das VOOL genau für diese Adresse gezeigt hat, in der Form, in der es gezeigt wurde. Prüfe und versuche es erneut oder nutze die anderen Optionen.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Wiederherstellung versiegelt nur eine bestehende Wallet und nur aus einer Sicherung, die auf deren eigenen Schlüssel dekodiert (beide Richtungen).
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** hoch · **Kategorie:** Sicherheit · **Sicherheitsrelevant:** ja

<a name="wallet-request-ambiguous"></a>

### wallet_request_ambiguous

**Mehrere Konten oder Netzwerke passen**

- **Was passiert ist:** Mehrere Konten oder Netzwerke passen zu dieser Übertragung, daher wurde nichts vorgeschlagen. Sage, welches Konto oder Netzwerk du meinst, und versuche es erneut.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Eine Überweisung benennt ein Konto in einem Netz; die Kandidaten stehen im Fehlerkontext.
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** niedrig · **Kategorie:** Richtlinie

<a name="wallet-security-key-needed"></a>

### wallet_security_key_needed

**Sicherheitsschlüssel nötig**

- **Was passiert ist:** Dieses Wallet braucht auch seinen Sicherheitsschlüssel, und der Schlüssel hat nicht geantwortet: Er ist nicht eingesteckt, es ist ein anderer Schlüssel, oder er wurde nicht rechtzeitig berührt. Nichts wurde entsperrt, geändert, signiert oder gesendet. Stecke den Sicherheitsschlüssel dieses Wallets ein, berühre ihn, wenn er blinkt, und versuche es erneut.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Stecke den Sicherheitsschlüssel ein, der diesem Wallet hinzugefügt wurde, und berühre ihn, wenn er blinkt. Ist der Schlüssel verloren, hole das Wallet mit seinem Sicherungsschlüssel zurück (aufgeschrieben, im Schlüsselbund oder auf einem USB-Stick): Das setzt eine neue PIN und entfernt den verlorenen Schlüssel aus dem Wallet.
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** niedrig · **Kategorie:** Verfügbarkeit

<a name="wallet-send-needs-vool-wallet"></a>

### wallet_send_needs_vool_wallet

**Zum Senden ist ein VOOL Wallet nötig**

- **Was passiert ist:** Dieses Konto kann in VOOL auf diesem Netzwerk nicht senden: Hier sendet nur ein VOOL Wallet, das auf diesem Gerät erstellt wurde. Ein verbundenes oder Nur-Ansicht-Konto kann weiterhin empfangen und seinen Kontostand zeigen. Nichts wurde signiert oder gesendet. Um zu senden, wähle ein VOOL Wallet auf diesem Gerät und versuche es erneut.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Sende von einem VOOL Wallet auf diesem Gerät oder aus der App des verbundenen Wallets selbst; VOOL signiert auf diesem Netzwerk nichts für ein verbundenes oder Nur-Ansicht-Konto.
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** niedrig · **Kategorie:** Richtlinie

<a name="wallet-setup-state-invalid"></a>

### wallet_setup_state_invalid

**Einrichtungsschritt passt gerade nicht**

- **Was passiert ist:** Dieser Einrichtungsschritt passt gerade nicht zu diesem Wallet, daher änderte sich nichts. Prüfe den Einrichtungsstatus des Wallets und versuche es erneut.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Die Einrichtung geht: erzeugen, auf Sicherung warten, Sicherung gezeigt, bereit; Abbrechen und Fortsetzen zeigen eine Sicherung nie ein zweites Mal.
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** niedrig · **Kategorie:** Richtlinie

<a name="wallet-signature-invalid"></a>

### wallet_signature_invalid

**Signatur passte nicht zum Schlüssel des Wallets**

- **Was passiert ist:** Die für diese Zahlung zurückgegebene Signatur passte nicht zum Schlüssel des Wallets, daher wurde sie nicht übertragen.
- **Was womöglich schon passiert ist:** Eine Anfrage hat einen externen Dienst erreicht; es wurde keine Zahlung ausgeführt
- **Was als Nächstes zu tun ist:** Prüfe den verbundenen Signierer; eine nicht passende Signatur wird vor dem Senden abgelehnt.
- **Erneuter Versuch:** Sende dieselbe Anfrage nicht unverändert erneut.
- **Schweregrad:** hoch · **Kategorie:** Sicherheit · **Sicherheitsrelevant:** ja

<a name="wallet-signing-unavailable"></a>

### wallet_signing_unavailable

**Wallet kann von hier nicht signieren**

- **Was passiert ist:** Dieses Wallet kann von hier nicht signieren: es ist Nur-Ansicht oder sein externer Signierer ist nicht verbunden. Nichts wurde gesendet; verbinde einen Signierer und versuche es erneut.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Verbinde die externe Wallet oder erstelle unter Einstellungen, Krypto eine VOOL-Wallet in diesem Netzwerk.
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** mittel · **Kategorie:** Richtlinie

<a name="wallet-simulation-failed"></a>

### wallet_simulation_failed

**Zahlung bestand die Simulation nicht**

- **Was passiert ist:** Die Zahlung bestand die Simulation nicht und wurde daher nicht gesendet. Prüfe Guthaben und Empfänger und versuche es erneut.
- **Was womöglich schon passiert ist:** Eine Anfrage hat einen externen Dienst erreicht; es wurde keine Zahlung ausgeführt
- **Was als Nächstes zu tun ist:** Prüfe Guthaben, Empfänger und den RPC-Endpunkt des Netzwerks und stelle dann erneut den Antrag.
- **Erneuter Versuch:** Versuche es später erneut: Was das verhindert hat, legt sich mit der Zeit.
- **Schweregrad:** mittel · **Kategorie:** Verfügbarkeit

<a name="wallet-storage-class-refused"></a>

### wallet_storage_class_refused

**Schlüsselspeicher nur im Arbeitsspeicher**

- **Was passiert ist:** Diese Installation hält den Geräteschlüssel nur im Speicher, daher könnte ein hier erstelltes Wallet nach einem Neustart nicht geöffnet werden. Es wurde nichts erstellt. Stelle den Schlüsselspeicher auf einen dauerhaften Modus und versuche es erneut.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Krypto-Pilot-Wallets werden abgelehnt, solange VOOL_KEY_STORAGE_MODE ephemeral ist; nutze file- oder keyring-Speicherung.
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** mittel · **Kategorie:** Richtlinie

<a name="wallet-unlock-throttled"></a>

### wallet_unlock_throttled

**Wallet nach fehlgeschlagenen Entsperrungen gesperrt**

- **Was passiert ist:** Es gab zu viele falsche PIN- oder Passwortversuche, daher ist dieses Wallet eine Weile gesperrt und nichts wurde entsperrt. Warte und versuche es erneut.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Fehlgeschlagene Entsperrungen werden dauerhaft je Wallet und für die ganze App gezählt, über Neustarts hinweg; die Sperre hebt sich von selbst.
- **Erneuter Versuch:** Versuche es später erneut: Was das verhindert hat, legt sich mit der Zeit.
- **Schweregrad:** hoch · **Kategorie:** Sicherheit · **Sicherheitsrelevant:** ja

<a name="wallet-x402-cap-exceeded"></a>

### wallet_x402_cap_exceeded

**Bezahlte Ressource über der automatischen Obergrenze**

- **Was passiert ist:** Eine bezahlte Ressource verlangte mehr als die automatische Zahlungsobergrenze und wurde daher nicht bezahlt.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Erhöhe VOOL_WALLET_X402_CAP_MINOR bewusst oder zahle für die Ressource über einen ausdrücklichen Antrag.
- **Erneuter Versuch:** Sende dieselbe Anfrage nicht unverändert erneut.
- **Schweregrad:** mittel · **Kategorie:** Richtlinie · **Sicherheitsrelevant:** ja

<a name="wallet-x402-sdk-unavailable"></a>

### wallet_x402_sdk_unavailable

**Erweiterung x402 nicht installiert**

- **Was passiert ist:** Dieser Build kann keine x402-Angebote auf Base über das x402 Foundation SDK bezahlen: Die optionale Erweiterung x402 ist auf diesem Rechner nicht installiert. Es wurde nichts signiert oder gesendet. Jede andere Wallet-Funktion läuft ohne sie; nach der Installation versuche es erneut.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Installiere die optionale Erweiterung x402 von VOOL, um x402-Angebote auf Base aus einer Crypto-Pilot-Wallet zu bezahlen; die Wallet behält in jedem Fall ihren eigenen Signierer, ihre Ausgabengrenzen und ihre Freigabe.
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** mittel · **Kategorie:** Richtlinie

<a name="x402-scheme-unavailable"></a>

### x402_scheme_unavailable

**Zahlungsschema nicht unterstützt**

- **Was passiert ist:** Dieses Zahlungsangebot braucht ein Schema, das dieses Wallet nicht Ende-zu-Ende nachweisen kann, daher wurde nichts signiert. Wähle ein Angebot, dessen Schema, Token und Vermittler für dieses Netzwerk geprüft sind.
- **Was womöglich schon passiert ist:** Nichts wurde gesendet, signiert oder geändert
- **Was als Nächstes zu tun ist:** Nutze ein Angebot, dessen Schema, Token und Vermittler für das Netz geprüft sind; teilweises Signieren genügt hier nicht.
- **Erneuter Versuch:** Versuche es erneut, nachdem du die Änderung vorgenommen hast, die dieser Eintrag nennt.
- **Schweregrad:** niedrig · **Kategorie:** Richtlinie
