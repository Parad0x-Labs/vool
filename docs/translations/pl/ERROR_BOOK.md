---
translation: machine
source_sha256: 72ade2152db88157a95e2155800ad7f7ecbca477e44e40d1aac1d95495bbc42e
---

# Księga błędów VOOL

<!-- GENERATED FILE — do not hand-edit. Regenerate with `python -m tools.generate_error_book`. -->

<!-- GENERATED FILE: the app's own texts for each code, and the words in tools/docs/error_book_words.json. Regenerate with tools/docs/translate_error_book.py, never by hand. -->

Wszystkie błędy, które VOOL może ci pokazać, wygenerowane z własnego katalogu błędów aplikacji: kody, komunikaty i dalsze kroki poniżej są dokładnie tym, co pokazuje aplikacja.

- **Schemat:** `vool.fault.v1`
- **Wersja katalogu:** `1`
- **Skrót katalogu:** `83290d22af3092c2aee6c9a3a5fe466107140dd7d52ab2aab1e8d817dc548b64`
- **Liczba błędów:** 58

Każdy kod jest stały i nigdy nie jest używany ponownie w innym znaczeniu. Każdy kod ma poniżej własną sekcję, której nagłówkiem jest sam kod, więc wyszukując na tej stronie kod w podanej ci postaci, znajdziesz jego sekcję, a link do sekcji to adres tej strony, po którym następują `#` i kod z łącznikami zamiast znaków podkreślenia. Każda sekcja mówi, co się stało, co mogło się już stać, co zrobić dalej i czy ponowna próba może pomóc. Jeśli podanego ci kodu nie ma w tej księdze, nie pochodzi on z tej wersji VOOL: sprawdź, która wersja go pokazała, albo zgłoś go, podając kod tak, jak został zapisany.

## Bezpieczeństwo

| Kod | Tytuł | Waga | Co mogło się już stać |
|---|---|---|---|
| [`confinement_refusal`](#confinement-refusal) | Zmiana poza dozwolonym zakresem | wysoka | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`credential_failure`](#credential-failure) | Nie udało się odczytać zapisanego poświadczenia | wysoka | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_backup_unavailable`](#wallet-backup-unavailable) | Kopia zapasowa była już raz pokazana | wysoka | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_caller_refused`](#wallet-caller-refused) | Nie uruchomiono z własnego okna VOOL | wysoka | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_card_data_refused`](#wallet-card-data-refused) | Odrzucono dane karty | wysoka | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_chain_identity_mismatch`](#wallet-chain-identity-mismatch) | Nieudowodniona tożsamość punktu końcowego sieci | wysoka | Żądanie dotarło do usługi zewnętrznej; nie wykonano żadnej płatności |
| [`wallet_environment_inactive`](#wallet-environment-inactive) | Niewłaściwe środowisko sieci | średnia | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_export_pin_not_accepted`](#wallet-export-pin-not-accepted) | Eksport odblokowuje tylko uwierzytelnianie urządzeniem | wysoka | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_export_refused`](#wallet-export-refused) | Stary sposób eksportu wycofany | wysoka | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_export_unavailable`](#wallet-export-unavailable) | Eksport niedostępny dla tego portfela | wysoka | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_legacy_surface_retired`](#wallet-legacy-surface-retired) | Starsza ścieżka pieniężna wycofana | wysoka | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_network_disabled`](#wallet-network-disabled) | Sieć niezadeklarowana dla tego portfela | wysoka | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_outbound_refused`](#wallet-outbound-refused) | Żądanie poza zatwierdzonymi punktami końcowymi | wysoka | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_quote_mismatch`](#wallet-quote-mismatch) | Zatwierdzenie nie pasuje już do podglądu | wysoka | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_recovery_refused`](#wallet-recovery-refused) | Dowód odzyskiwania nie pasuje do tego portfela | wysoka | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_signature_invalid`](#wallet-signature-invalid) | Podpis nie pasował do klucza portfela | wysoka | Żądanie dotarło do usługi zewnętrznej; nie wykonano żadnej płatności |
| [`wallet_unlock_throttled`](#wallet-unlock-throttled) | Portfel zablokowany po nieudanych odblokowaniach | wysoka | Nic nie wysłano, nie podpisano ani nie zmieniono |

## Integralność

| Kod | Tytuł | Waga | Co mogło się już stać |
|---|---|---|---|
| [`evidence_corruption`](#evidence-corruption) | Zapisy odpowiedzi nie przeszły sprawdzenia spójności | wysoka | Nastąpił lokalny zapis lub zmiana |
| [`integrity_verification_failure`](#integrity-verification-failure) | Zapis nie przeszedł sprawdzenia nienaruszalności | krytyczna | Nastąpił lokalny zapis lub zmiana |
| [`unsupported_claim`](#unsupported-claim) | Wstrzymano niepotwierdzone stwierdzenia | średnia | Nastąpił lokalny zapis lub zmiana |
| [`wallet_duplicate_payment`](#wallet-duplicate-payment) | Płatność już zaproponowana lub wysłana | średnia | Nastąpił lokalny zapis lub zmiana |

## Zasady

| Kod | Tytuł | Waga | Co mogło się już stać |
|---|---|---|---|
| [`evm_pocket_custody_unavailable`](#evm-pocket-custody-unavailable) | Starsza kieszeń tylko dla Solany | niska | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`permission_denied`](#permission-denied) | Brak uprawnień | średnia | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_acknowledgement_required`](#wallet-acknowledgement-required) | Wymagane potwierdzenie | niska | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_amount_invalid`](#wallet-amount-invalid) | Niedokładna kwota dla tej monety | niska | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_approval_rejected`](#wallet-approval-rejected) | Zgoda nie została przyjęta | średnia | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_confirmation_required`](#wallet-confirmation-required) | Wymagane wpisane potwierdzenie | niska | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_credential_mismatch`](#wallet-credential-mismatch) | Wpisy nie pasowały do siebie | niska | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_dependency_unavailable`](#wallet-dependency-unavailable) | Biblioteki EVM nie są zainstalowane | średnia | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_device_auth_denied`](#wallet-device-auth-denied) | Odrzucono odblokowanie urządzeniem | niska | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_disabled`](#wallet-disabled) | Portfel jest wyłączony | niska | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_insufficient_funds`](#wallet-insufficient-funds) | Saldo nie pokrywa kwoty i opłaty | niska | Żądanie dotarło do usługi zewnętrznej; nie wykonano żadnej płatności |
| [`wallet_limit_exceeded`](#wallet-limit-exceeded) | Przekroczono limit wydatków | średnia | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_not_found`](#wallet-not-found) | Nie rozpoznano portfela, propozycji ani odbiorcy | niska | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_password_invalid`](#wallet-password-invalid) | Nieprawidłowe hasło portfela | niska | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_paykit_unavailable`](#wallet-paykit-unavailable) | Dodatek pay dla Solany nie jest zainstalowany | średnia | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_payment_channel_off`](#wallet-payment-channel-off) | Kanał płatności wyłączony | niska | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_pin_invalid`](#wallet-pin-invalid) | Nieprawidłowy PIN | niska | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_quote_expired`](#wallet-quote-expired) | Podgląd wygasł lub został zastąpiony | niska | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_recipient_refused`](#wallet-recipient-refused) | Odbiorca nie może bezpiecznie tego przyjąć | średnia | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_request_ambiguous`](#wallet-request-ambiguous) | Pasuje więcej niż jedno konto lub sieć | niska | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_send_needs_vool_wallet`](#wallet-send-needs-vool-wallet) | Do wysyłania potrzebny jest VOOL Wallet | niska | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_setup_state_invalid`](#wallet-setup-state-invalid) | Krok konfiguracji nie ma teraz zastosowania | niska | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_signing_unavailable`](#wallet-signing-unavailable) | Portfel nie może stąd podpisywać | średnia | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_storage_class_refused`](#wallet-storage-class-refused) | Przechowywanie kluczy tylko w pamięci | średnia | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_x402_cap_exceeded`](#wallet-x402-cap-exceeded) | Płatny zasób powyżej automatycznego limitu | średnia | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_x402_sdk_unavailable`](#wallet-x402-sdk-unavailable) | Dodatek x402 nie jest zainstalowany | średnia | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`x402_scheme_unavailable`](#x402-scheme-unavailable) | Nieobsługiwany schemat płatności | niska | Nic nie wysłano, nie podpisano ani nie zmieniono |

## Dostępność

| Kod | Tytuł | Waga | Co mogło się już stać |
|---|---|---|---|
| [`provider_exhausted`](#provider-exhausted) | Osiągnięto limit dostawcy | średnia | Żądanie dotarło do usługi zewnętrznej; nie wykonano żadnej płatności |
| [`provider_unavailable`](#provider-unavailable) | Dostawca nieosiągalny | średnia | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`timeout`](#timeout) | Przekroczono limit czasu | średnia | Wynik nieznany — sprawdź stan przed ponowną próbą |
| [`tool_unavailable`](#tool-unavailable) | Narzędzie niedostępne | niska | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_broadcast_failed`](#wallet-broadcast-failed) | Nie udało się nadać płatności | średnia | Wynik nieznany — sprawdź stan przed ponowną próbą |
| [`wallet_device_auth_unavailable`](#wallet-device-auth-unavailable) | Odblokowanie urządzeniem niedostępne | średnia | Nic nie wysłano, nie podpisano ani nie zmieniono |
| [`wallet_quote_unavailable`](#wallet-quote-unavailable) | Nieznane saldo lub opłata | średnia | Żądanie dotarło do usługi zewnętrznej; nie wykonano żadnej płatności |
| [`wallet_simulation_failed`](#wallet-simulation-failed) | Płatność nie przeszła symulacji | średnia | Żądanie dotarło do usługi zewnętrznej; nie wykonano żadnej płatności |

## Anulowanie

| Kod | Tytuł | Waga | Co mogło się już stać |
|---|---|---|---|
| [`cancelled`](#cancelled) | Anulowano | informacyjna | Wynik nieznany — sprawdź stan przed ponowną próbą |

## Wewnętrzne

| Kod | Tytuł | Waga | Co mogło się już stać |
|---|---|---|---|
| [`unknown`](#unknown) | Nieoczekiwany błąd | średnia | Wynik nieznany — sprawdź stan przed ponowną próbą |

## Szczegóły według kodu

Wszystkie kody w kolejności alfabetycznej. Nagłówek sekcji jest stałą kotwicą kodu.

<a name="cancelled"></a>

### cancelled

**Anulowano**

- **Co się stało:** To zostało anulowane, więc nie zostało ukończone.
- **Co mogło się już stać:** Wynik nieznany — sprawdź stan przed ponowną próbą
- **Co zrobić dalej:** Nic nie trzeba; zacznij ponownie, jeśli chcesz, żeby było zrobione.
- **Ponowna próba:** Nie wysyłaj ponownie tego samego żądania bez zmian.
- **Waga:** informacyjna · **Kategoria:** anulowanie

<a name="confinement-refusal"></a>

### confinement_refusal

**Zmiana poza dozwolonym zakresem**

- **Co się stało:** Zmiana pliku poza dozwolonym zakresem została zablokowana. Nic poza zakresem nie zostało dotknięte.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Jeśli ta zmiana jest potrzebna, zażądaj jej jawnie, by była wewnątrz uprawnionego zakresu.
- **Ponowna próba:** Nie wysyłaj ponownie tego samego żądania bez zmian.
- **Waga:** wysoka · **Kategoria:** bezpieczeństwo · **Istotne dla bezpieczeństwa:** tak

<a name="credential-failure"></a>

### credential_failure

**Nie udało się odczytać zapisanego poświadczenia**

- **Co się stało:** Nie udało się odczytać zapisanego klucza. Wpisz go ponownie w ustawieniach, aby kontynuować.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Wpis sejfu nie odszyfrował się; zapisz poświadczenie ponownie albo sprawdź klucz sejfu.
- **Ponowna próba:** Nie wysyłaj ponownie tego samego żądania bez zmian.
- **Waga:** wysoka · **Kategoria:** bezpieczeństwo · **Istotne dla bezpieczeństwa:** tak

<a name="evidence-corruption"></a>

### evidence_corruption

**Zapisy odpowiedzi nie przeszły sprawdzenia spójności**

- **Co się stało:** Zapisy wspierające tę odpowiedź nie przeszły sprawdzenia spójności, więc traktuj odpowiedź jako niezweryfikowaną.
- **Co mogło się już stać:** Nastąpił lokalny zapis lub zmiana
- **Co zrobić dalej:** Zbadaj księgę wykonania tej tury wobec strumienia zdarzeń środowiska.
- **Ponowna próba:** Nie wysyłaj ponownie tego samego żądania bez zmian.
- **Waga:** wysoka · **Kategoria:** integralność · **Istotne dla bezpieczeństwa:** tak

<a name="evm-pocket-custody-unavailable"></a>

### evm_pocket_custody_unavailable

**Starsza kieszeń tylko dla Solany**

- **Co się stało:** Starsza kieszeń, czyli portfel oparty na frazie odzyskiwania, przechowuje tylko klucz Solany, więc nie da się jej utworzyć ani przywrócić w sieci EVM. Nic nie utworzono. Dla sieci EVM utwórz portfel VOOL w tej sieci w Ustawieniach, Krypto, dodaj konto tylko do podglądu albo połącz własny portfel EVM.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Starsza kieszeń pieczętuje jedno ziarno Solany ze swojej frazy odzyskiwania. W sieci EVM użyj portfela Crypto Pilot („Create wallet” przy tej sieci w Ustawieniach, Krypto, Sieci i portfele), konta tylko do podglądu albo zewnętrznego portfela EVM (EIP-1193), który sam przechowuje swój klucz.
- **Ponowna próba:** Nie wysyłaj ponownie tego samego żądania bez zmian.
- **Waga:** niska · **Kategoria:** zasady

<a name="integrity-verification-failure"></a>

### integrity_verification_failure

**Zapis nie przeszedł sprawdzenia nienaruszalności**

- **Co się stało:** Zapis nie przeszedł sprawdzenia nienaruszalności. Nic nie zostało po cichu zaakceptowane.
- **Co mogło się już stać:** Nastąpił lokalny zapis lub zmiana
- **Co zrobić dalej:** Traktuj dotknięty łańcuch pokwitowań jako skompromitowany; zbadaj, zanim mu zaufasz.
- **Ponowna próba:** Nie wysyłaj ponownie tego samego żądania bez zmian.
- **Waga:** krytyczna · **Kategoria:** integralność · **Istotne dla bezpieczeństwa:** tak

<a name="permission-denied"></a>

### permission_denied

**Brak uprawnień**

- **Co się stało:** Ta akcja nie została dozwolona, więc nic się nie zmieniło.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Sprawdź politykę uprawnień albo ustawienia zgód rządzące tym działaniem.
- **Ponowna próba:** Nie wysyłaj ponownie tego samego żądania bez zmian.
- **Waga:** średnia · **Kategoria:** zasady · **Istotne dla bezpieczeństwa:** tak

<a name="provider-exhausted"></a>

### provider_exhausted

**Osiągnięto limit dostawcy**

- **Co się stało:** Limit użycia dostawcy modeli został wyczerpany. Spróbuj później.
- **Co mogło się już stać:** Żądanie dotarło do usługi zewnętrznej; nie wykonano żadnej płatności
- **Co zrobić dalej:** Sprawdź limity, quo i płatności skonfigurowanego dostawcy.
- **Ponowna próba:** Spróbuj ponownie później: to, co to zatrzymało, z czasem ustępuje.
- **Waga:** średnia · **Kategoria:** dostępność

<a name="provider-unavailable"></a>

### provider_unavailable

**Dostawca nieosiągalny**

- **Co się stało:** Nie udało się połączyć z dostawcą modeli. Spróbuj za chwilę.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Sprawdź status dostawcy, osiągalność sieci i skonfigurowane punkty końcowe.
- **Ponowna próba:** Spróbuj ponownie później: to, co to zatrzymało, z czasem ustępuje.
- **Waga:** średnia · **Kategoria:** dostępność

<a name="timeout"></a>

### timeout

**Przekroczono limit czasu**

- **Co się stało:** To trwało zbyt długo i zostało zatrzymane. Ponowna próba może się udać.
- **Co mogło się już stać:** Wynik nieznany — sprawdź stan przed ponowną próbą
- **Co zrobić dalej:** Jeślimine przekraczają się, sprawdź opóźnienia sieci i responsywność dostawcy.
- **Ponowna próba:** Ponowna próba teraz może się udać.
- **Waga:** średnia · **Kategoria:** dostępność

<a name="tool-unavailable"></a>

### tool_unavailable

**Narzędzie niedostępne**

- **Co się stało:** To narzędzie jest teraz niedostępne, więc ta część nie została wykonana. Przed ponowną próbą trzeba je włączyć.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Włącz narzędzie dla tego trybu w ustawieniach albo wybierz inny sposób.
- **Ponowna próba:** Spróbuj ponownie po wprowadzeniu zmiany, którą wskazuje ten wpis.
- **Waga:** niska · **Kategoria:** dostępność

<a name="unknown"></a>

### unknown

**Nieoczekiwany błąd**

- **Co się stało:** Coś poszło nie tak na tym komputerze podczas obsługi. Możesz spróbować ponownie.
- **Co mogło się już stać:** Wynik nieznany — sprawdź stan przed ponowną próbą
- **Co zrobić dalej:** Zobacz zredagowany łańcuch przyczyn błędu w diagnostyce; nieznana przyczyna nigdy nie jest ponawiana w ciemno.
- **Ponowna próba:** Nie wysyłaj ponownie tego samego żądania bez zmian.
- **Waga:** średnia · **Kategoria:** wewnętrzna

<a name="unsupported-claim"></a>

### unsupported_claim

**Wstrzymano niepotwierdzone stwierdzenia**

- **Co się stało:** Jedno lub więcej stwierdzeń nie znalazło potwierdzenia w pobranych źródłach i zostało wstrzymanych.
- **Co mogło się już stać:** Nastąpił lokalny zapis lub zmiana
- **Co zrobić dalej:** Nic nie trzeba — to brama uziemienia przy pracy. Jeśli częste, sprawdź jakość wyszukiwania.
- **Ponowna próba:** Nie wysyłaj ponownie tego samego żądania bez zmian.
- **Waga:** średnia · **Kategoria:** integralność

<a name="wallet-acknowledgement-required"></a>

### wallet_acknowledgement_required

**Wymagane potwierdzenie**

- **Co się stało:** Ten krok wymaga najpierw twojego potwierdzenia, więc nic nie podpisano ani nie wysłano. Przeczytaj, co według portfela robi ta czynność, potwierdź to dokładnie i spróbuj ponownie.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Gdy zatwierdzenie dałoby trwałe prawa do wydawania, zmieniłoby własność lub uruchomiło aktualizację, albo portfel znajdzie niezgodność lub nie może odczytać żądania, prosi cię najpierw o wpisanie dokładnego tekstu potwierdzenia; nic ogólnego go nie zastąpi.
- **Ponowna próba:** Spróbuj ponownie po wprowadzeniu zmiany, którą wskazuje ten wpis.
- **Waga:** niska · **Kategoria:** zasady

<a name="wallet-amount-invalid"></a>

### wallet_amount_invalid

**Niedokładna kwota dla tej monety**

- **Co się stało:** Ta kwota nie jest kwotą, jaką ta moneta może dokładnie przechować, więc nic nie zaproponowano. Sprawdź kwotę i spróbuj ponownie.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Wyślij zwykłą kwotę dziesiętną, nie dokładniejszą niż pozwala moneta i w limicie pojedynczego przelewu pilota.
- **Ponowna próba:** Spróbuj ponownie po wprowadzeniu zmiany, którą wskazuje ten wpis.
- **Waga:** niska · **Kategoria:** zasady

<a name="wallet-approval-rejected"></a>

### wallet_approval_rejected

**Zgoda nie została przyjęta**

- **Co się stało:** Zgoda na tę płatność nie została przyjęta, więc nic nie podpisano i nie wysłano.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Zatwierdź dokładne parametry PIN-em albo biometrią właściciela; zgody nie przenoszą się między parametrami.
- **Ponowna próba:** Nie wysyłaj ponownie tego samego żądania bez zmian.
- **Waga:** średnia · **Kategoria:** zasady · **Istotne dla bezpieczeństwa:** tak

<a name="wallet-backup-unavailable"></a>

### wallet_backup_unavailable

**Kopia zapasowa była już raz pokazana**

- **Co się stało:** Klucz zapasowy tego portfela był już raz pokazany, więc nie może być pokazany ponownie. Jeśli go nie zapisałeś, anuluj konfigurację i nie wysyłaj środków na ten adres.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Kopia zapasowa pilota krypto ujawniana jest dokładnie raz, podczas konfiguracji; nie ma drugiego ujawnienia ani eksportu.
- **Ponowna próba:** Nie wysyłaj ponownie tego samego żądania bez zmian.
- **Waga:** wysoka · **Kategoria:** bezpieczeństwo · **Istotne dla bezpieczeństwa:** tak

<a name="wallet-broadcast-failed"></a>

### wallet_broadcast_failed

**Nie udało się nadać płatności**

- **Co się stało:** Nie udało się nadać płatności. Przed ponowną próbą sprawdź jej status.
- **Co mogło się już stać:** Wynik nieznany — sprawdź stan przed ponowną próbą
- **Co zrobić dalej:** Sprawdź punkt końcowy RPC sieci i pokwitowanie wniosku; wydatek nie został zapisany jako wysłany.
- **Ponowna próba:** Spróbuj ponownie później: to, co to zatrzymało, z czasem ustępuje.
- **Waga:** średnia · **Kategoria:** dostępność

<a name="wallet-caller-refused"></a>

### wallet_caller_refused

**Nie uruchomiono z własnego okna VOOL**

- **Co się stało:** Tej akcji portfela można dokonać tylko z okna VOOL na tym komputerze, więc nic nie zmieniono, podpisano ani wysłano. Otwórz VOOL i spróbuj tam.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Prowadź zaufane drzwi portfela z serwowanej strony lub okna natywnego; strona innej originy, ramka albo wtyczka jest odrzucana.
- **Ponowna próba:** Spróbuj ponownie po wprowadzeniu zmiany, którą wskazuje ten wpis.
- **Waga:** wysoka · **Kategoria:** bezpieczeństwo · **Istotne dla bezpieczeństwa:** tak

<a name="wallet-card-data-refused"></a>

### wallet_card_data_refused

**Odrzucono dane karty**

- **Co się stało:** Numery kart i kody bezpieczeństwa nigdy tu nie są przechowywane; przyjmowany jest tylko token dostawcy. Nic nie zapisano.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Ztokenizuj kartę u dostawcy i zarejestruj odnośnik tokena.
- **Ponowna próba:** Nie wysyłaj ponownie tego samego żądania bez zmian.
- **Waga:** wysoka · **Kategoria:** bezpieczeństwo · **Istotne dla bezpieczeństwa:** tak

<a name="wallet-chain-identity-mismatch"></a>

### wallet_chain_identity_mismatch

**Nieudowodniona tożsamość punktu końcowego sieci**

- **Co się stało:** Punkt końcowy sieci nie udowodnił tożsamości sieci, za którą się podaje, więc nic nie podpisano ani nie wysłano.
- **Co mogło się już stać:** Żądanie dotarło do usługi zewnętrznej; nie wykonano żadnej płatności
- **Co zrobić dalej:** Sprawdź punkt RPC dla sieci; kłamiący chain id albo genesis jest odrzucany przed każdą płatnością.
- **Ponowna próba:** Nie wysyłaj ponownie tego samego żądania bez zmian.
- **Waga:** wysoka · **Kategoria:** bezpieczeństwo · **Istotne dla bezpieczeństwa:** tak

<a name="wallet-confirmation-required"></a>

### wallet_confirmation_required

**Wymagane wpisane potwierdzenie**

- **Co się stało:** Ten krok wymaga najpierw wpisanego potwierdzenia, więc nic się nie zmieniło. Potwierdź i spróbuj ponownie.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Przeczytaj ostrzeżenie i wpisz dokładną frazę potwierdzenia, by iść dalej.
- **Ponowna próba:** Spróbuj ponownie po wprowadzeniu zmiany, którą wskazuje ten wpis.
- **Waga:** niska · **Kategoria:** zasady

<a name="wallet-credential-mismatch"></a>

### wallet_credential_mismatch

**Wpisy nie pasowały do siebie**

- **Co się stało:** Dwa wpisy nie pasowały do siebie, więc nic nie utworzono. Wpisz ten sam PIN lub hasło dwa razy i spróbuj ponownie.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Poświadczenie pilota krypto potwierdzane jest zanim powstanie jakikolwiek materiał klucza.
- **Ponowna próba:** Spróbuj ponownie po wprowadzeniu zmiany, którą wskazuje ten wpis.
- **Waga:** niska · **Kategoria:** zasady

<a name="wallet-dependency-unavailable"></a>

### wallet_dependency_unavailable

**Biblioteki EVM nie są zainstalowane**

- **Co się stało:** Ta wersja nie potrafi podpisywać EVM ani kodować ABI: na tym komputerze nie ma bibliotek Ethereum. Nic nie podpisano ani nie wysłano. Solana, przechowywanie i limity wydatków nie są dotknięte; po zainstalowaniu bibliotek spróbuj ponownie.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Zainstaluj dodatki EVM (eth-abi, eth-utils, eth-account), by włączyć pasma EVM; każda inna funkcja portfela działa bez nich.
- **Ponowna próba:** Spróbuj ponownie po wprowadzeniu zmiany, którą wskazuje ten wpis.
- **Waga:** średnia · **Kategoria:** zasady

<a name="wallet-device-auth-denied"></a>

### wallet_device_auth_denied

**Odrzucono odblokowanie urządzeniem**

- **Co się stało:** Touch ID lub hasło do Maca zostało odrzucone albo nie otworzyło tego portfela, więc nic nie utworzono, nie zmieniono, nie odblokowano, nie ujawniono, nie podpisano ani nie wysłano. Spróbuj ponownie.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Spróbuj ponownie i potwierdź prośbę o Touch ID lub hasło do Maca wyświetloną przez VOOL. Portfel, który zatwierdza przez Touch ID, nie ma własnego PIN-u ani hasła, których mógłby użyć zamiast tego. Jeśli prośba nadal się nie udaje, przywróć portfel z frazy odzyskiwania (Crypto Pilota z kopii zapasowej pokazanej podczas konfiguracji) i nadaj mu PIN lub hasło; eksport klucza prywatnego nadal wymaga Touch ID lub hasła do Maca.
- **Ponowna próba:** Spróbuj ponownie po wprowadzeniu zmiany, którą wskazuje ten wpis.
- **Waga:** niska · **Kategoria:** zasady

<a name="wallet-device-auth-unavailable"></a>

### wallet_device_auth_unavailable

**Odblokowanie urządzeniem niedostępne**

- **Co się stało:** Touch ID ani hasło do Maca nie są tu dostępne dla tego portfela, więc nic nie utworzono, nie zmieniono, nie odblokowano, nie ujawniono, nie podpisano ani nie wysłano. Spróbuj ponownie, gdy będą dostępne.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Nowy portfel może zamiast tego zatwierdzać własnym PIN-em lub hasłem. Portfel, który zatwierdza przez Touch ID, nie ma własnego PIN-u ani hasła: jeśli Touch ID i hasło do Maca pozostają niedostępne, przywróć go z frazy odzyskiwania (Crypto Pilota z kopii zapasowej pokazanej podczas konfiguracji) i nadaj mu PIN lub hasło. Eksport klucza prywatnego otwiera się tylko przez Touch ID lub hasło do Maca, więc przywróć portfel na Macu, na którym działają, i tam go wyeksportuj.
- **Ponowna próba:** Spróbuj ponownie po wprowadzeniu zmiany, którą wskazuje ten wpis.
- **Waga:** średnia · **Kategoria:** dostępność

<a name="wallet-disabled"></a>

### wallet_disabled

**Portfel jest wyłączony**

- **Co się stało:** Portfel jest wyłączony, więc nie zaproponowano ani nie wysłano płatności. Włącz przełącznik „Włącz krypto” w Ustawieniach, Krypto, potem spróbuj ponownie.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Włącz przełącznik „Włącz krypto” w Ustawieniach, Krypto tylko wtedy, gdy chcesz płatności na tej instalacji. Gdy ustawiona jest zmienna VOOL_WALLET_ENABLED, to ona włącza lub wyłącza portfel, niezależnie od tego, co pokazuje przełącznik.
- **Ponowna próba:** Spróbuj ponownie po wprowadzeniu zmiany, którą wskazuje ten wpis.
- **Waga:** niska · **Kategoria:** zasady

<a name="wallet-duplicate-payment"></a>

### wallet_duplicate_payment

**Płatność już zaproponowana lub wysłana**

- **Co się stało:** Ta płatność była już zaproponowana lub wysłana, więc nie wysłano jej ponownie.
- **Co mogło się już stać:** Nastąpił lokalny zapis lub zmiana
- **Co zrobić dalej:** Sprawdź pokwitowanie istniejącego wniosku; dla naprawdę nowej płatności użyj nowego klucza idempotencji.
- **Ponowna próba:** Nie wysyłaj ponownie tego samego żądania bez zmian.
- **Waga:** średnia · **Kategoria:** integralność · **Istotne dla bezpieczeństwa:** tak

<a name="wallet-environment-inactive"></a>

### wallet_environment_inactive

**Niewłaściwe środowisko sieci**

- **Co się stało:** Ta sieć należy do drugiego środowiska sieci (Mainnet albo sieci testowe), więc nic nie utworzono, zaproponowano ani podpisano. Przełącz w Ustawieniach, Kripto, Opcje deweloperskie, potem spróbuj ponownie.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Wybierz środowisko sieci świadomie w ustawieniach Krypto; konta, salda i pokwitowania nigdy nie wędrują między środowiskami.
- **Ponowna próba:** Spróbuj ponownie po wprowadzeniu zmiany, którą wskazuje ten wpis.
- **Waga:** średnia · **Kategoria:** bezpieczeństwo · **Istotne dla bezpieczeństwa:** tak

<a name="wallet-export-pin-not-accepted"></a>

### wallet_export_pin_not_accepted

**Eksport odblokowuje tylko uwierzytelnianie urządzeniem**

- **Co się stało:** Eksport nigdy nie odblokowuje się PIN-em, hasłem ani frazą hasła, tylko uwierzytelnianiem urządzeniem (Touch ID lub hasło do Maca), więc nic nie ujawniono. Spróbuj ponownie bez PIN-u lub hasła.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Eksport celowo odblokowuje tylko uwierzytelnianie urządzeniem; PIN, hasło lub fraza hasła w żądaniu są odrzucane, zanim w ogóle zostaną sprawdzone.
- **Ponowna próba:** Spróbuj ponownie po wprowadzeniu zmiany, którą wskazuje ten wpis.
- **Waga:** wysoka · **Kategoria:** bezpieczeństwo · **Istotne dla bezpieczeństwa:** tak

<a name="wallet-export-refused"></a>

### wallet_export_refused

**Stary sposób eksportu wycofany**

- **Co się stało:** Ten sposób eksportu klucza prywatnego został wycofany, więc nic nie ujawniono. Portfel, który pozwala na eksport, ma przycisk „Export private key” w Ustawieniach, Krypto, który otwiera się tylko z Touch ID lub hasłem do Maca.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Użyj „Export private key” w Ustawieniach, Krypto, Inne konta: eksportuje się tylko starsza kieszeń i tylko z Touch ID lub hasłem do Maca. Portfel Crypto Pilot nigdy się nie eksportuje; odzyskuje się go z kopii zapasowej pokazanej podczas konfiguracji.
- **Ponowna próba:** Nie wysyłaj ponownie tego samego żądania bez zmian.
- **Waga:** wysoka · **Kategoria:** bezpieczeństwo · **Istotne dla bezpieczeństwa:** tak

<a name="wallet-export-unavailable"></a>

### wallet_export_unavailable

**Eksport niedostępny dla tego portfela**

- **Co się stało:** Tego portfela nie da się tak wyeksportować: jest tylko do podglądu lub nie trzyma żadnego klucza na tym komputerze, jego jedyną kopię zapasową pokazano podczas konfiguracji i nigdy się nie eksportuje, VOOL nie eksportuje do wybranej aplikacji portfela, albo jest to starsza kieszeń z wcześniejszej wersji, którą przed eksportem do MetaMask trzeba przywrócić z jej frazy odzyskiwania. Nic nie ujawniono.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Eksportuje się tylko starsza kieszeń zapieczętowana na tym komputerze, do Phantom lub MetaMask, i tylko z Touch ID lub hasłem do Maca. Portfel Crypto Pilot odzyskuje się z kopii zapasowej pokazanej podczas konfiguracji. Starszą kieszeń z wcześniejszej wersji można wyeksportować do MetaMask po przywróceniu jej z frazy odzyskiwania.
- **Ponowna próba:** Nie wysyłaj ponownie tego samego żądania bez zmian.
- **Waga:** wysoka · **Kategoria:** bezpieczeństwo · **Istotne dla bezpieczeństwa:** tak

<a name="wallet-insufficient-funds"></a>

### wallet_insufficient_funds

**Saldo nie pokrywa kwoty i opłaty**

- **Co się stało:** Saldo nie pokrywa kwoty plus maksymalnej możliwej opłaty tej sieci, więc nic nie przygotowano. Zmniejsz kwotę lub dodaj środki i spróbuj ponownie.
- **Co mogło się już stać:** Żądanie dotarło do usługi zewnętrznej; nie wykonano żadnej płatności
- **Co zrobić dalej:** Wydawalne pomija inne trwające blokady; reszta na Solanie musi być bez zer na końcu i co najmniej równa czynszowi.
- **Ponowna próba:** Spróbuj ponownie po wprowadzeniu zmiany, którą wskazuje ten wpis.
- **Waga:** niska · **Kategoria:** zasady

<a name="wallet-legacy-surface-retired"></a>

### wallet_legacy_surface_retired

**Starsza ścieżka pieniężna wycofana**

- **Co się stało:** Ta ścieżka pieniężna została wycofana: każda płatność przechodzi przez przepływ propozycji i zatwierdzenia portfela. Nic nie podpisano ani nie wysłano.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Używaj narzędzi portfela (wallet.propose) albo API portfela; starszy podpisujący, wydający i eksporter już nie służą.
- **Ponowna próba:** Nie wysyłaj ponownie tego samego żądania bez zmian.
- **Waga:** wysoka · **Kategoria:** bezpieczeństwo · **Istotne dla bezpieczeństwa:** tak

<a name="wallet-limit-exceeded"></a>

### wallet_limit_exceeded

**Przekroczono limit wydatków**

- **Co się stało:** Ta płatność przekracza limit wydatków, więc nie została wysłana.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Przed ponowną próbą przejrzyj limity na transakcję, dzienne i na odbiorcę.
- **Ponowna próba:** Nie wysyłaj ponownie tego samego żądania bez zmian.
- **Waga:** średnia · **Kategoria:** zasady · **Istotne dla bezpieczeństwa:** tak

<a name="wallet-network-disabled"></a>

### wallet_network_disabled

**Sieć niezadeklarowana dla tego portfela**

- **Co się stało:** Ta sieć nie należy do sieci deklarowanych przez ten portfel dla tej akcji, więc nic nie utworzono ani nie wysłano.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Użyj jednego z ogłoszonych wierszy sieci; nieogłoszone łańcuchy, aliasy i łudzące nazwy odrzuca rejestr.
- **Ponowna próba:** Nie wysyłaj ponownie tego samego żądania bez zmian.
- **Waga:** wysoka · **Kategoria:** bezpieczeństwo · **Istotne dla bezpieczeństwa:** tak

<a name="wallet-not-found"></a>

### wallet_not_found

**Nie rozpoznano portfela, propozycji ani odbiorcy**

- **Co się stało:** Taki portfel, propozycja albo odbiorca nie został rozpoznany, więc nic nie zrobiono. Sprawdź i spróbuj ponownie.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Sprawdź id portfela, id wniosku albo adres odbiorcy i spróbuj ponownie.
- **Ponowna próba:** Spróbuj ponownie po wprowadzeniu zmiany, którą wskazuje ten wpis.
- **Waga:** niska · **Kategoria:** zasady

<a name="wallet-outbound-refused"></a>

### wallet_outbound_refused

**Żądanie poza zatwierdzonymi punktami końcowymi**

- **Co się stało:** To żądanie nie mogło opuścić zatwierdzonych punktów końcowych portfela, więc nie nawiązano połączenia.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Sprawdź ogłoszone originy RPC i politykę zasobów płatności; prywatne, metadane i niezatwierdzone hosty są odrzucane.
- **Ponowna próba:** Nie wysyłaj ponownie tego samego żądania bez zmian.
- **Waga:** wysoka · **Kategoria:** bezpieczeństwo · **Istotne dla bezpieczeństwa:** tak

<a name="wallet-password-invalid"></a>

### wallet_password_invalid

**Nieprawidłowe hasło portfela**

- **Co się stało:** Hasło nie spełniło zasad albo nie odblokowało portfela, więc nic nie podpisano ani nie zmieniono. Sprawdź hasło i spróbuj ponownie.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Hasła portfela mają od 10 do 128 znaków i nie składają się z samych cyfr. Złe hasło zostawia klucz zapieczętowany i liczy się do blokady portfela po zbyt wielu błędnych próbach.
- **Ponowna próba:** Spróbuj ponownie po wprowadzeniu zmiany, którą wskazuje ten wpis.
- **Waga:** niska · **Kategoria:** zasady

<a name="wallet-paykit-unavailable"></a>

### wallet_paykit_unavailable

**Dodatek pay dla Solany nie jest zainstalowany**

- **Co się stało:** Ta wersja nie potrafi opłacać ofert x402 ani MPP w Solanie: na tym komputerze nie ma opcjonalnego dodatku pay (Solana pay-kit, Python 3.11 lub nowszy). Nic nie podpisano ani nie wysłano. Każda inna funkcja portfela działa bez niego; po zainstalowaniu spróbuj ponownie.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Zainstaluj opcjonalny dodatek pay VOOL na Pythonie 3.11 lub nowszym, by włączyć płatności pay-kit; portfel w każdym przypadku zachowuje własnego sygnatariusza, limity wydatków i zatwierdzanie.
- **Ponowna próba:** Spróbuj ponownie po wprowadzeniu zmiany, którą wskazuje ten wpis.
- **Waga:** średnia · **Kategoria:** zasady

<a name="wallet-payment-channel-off"></a>

### wallet_payment_channel_off

**Kanał płatności wyłączony**

- **Co się stało:** Ta płatność idzie przez wyłączony kanał płatności, więc nic nie podpisano ani nie wysłano. Włącz kanał w Ustawieniach, Krypto, Kanały płatności, potem spróbuj ponownie.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Włącz kanał płatności w ustawieniach Krypto tylko wtedy, gdy chcesz, by płatności przez niego szły; limit i twoja zgoda obowiązują na każdym kanale.
- **Ponowna próba:** Spróbuj ponownie po wprowadzeniu zmiany, którą wskazuje ten wpis.
- **Waga:** niska · **Kategoria:** zasady

<a name="wallet-pin-invalid"></a>

### wallet_pin_invalid

**Nieprawidłowy PIN**

- **Co się stało:** PIN nie spełnił zasad albo nie otworzył portfela, więc nic nie podpisano. Sprawdź PIN i spróbuj ponownie.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Użyj PIN o 6–12 cyfrach; zły PIN zostawia zapieczętowany klucz nietkniętym.
- **Ponowna próba:** Spróbuj ponownie po wprowadzeniu zmiany, którą wskazuje ten wpis.
- **Waga:** niska · **Kategoria:** zasady

<a name="wallet-quote-expired"></a>

### wallet_quote_expired

**Podgląd wygasł lub został zastąpiony**

- **Co się stało:** Ten podgląd przelewu wygasł lub został zastąpiony, więc nie można go zatwierdzić i nic nie wysłano. Odśwież podgląd i spróbuj ponownie.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Wycena żyje krótkie okno i bywa zastąpiona odświeżeniem albo zmianą środowiska; zgoda zawsze wiąże obecną.
- **Ponowna próba:** Spróbuj ponownie po wprowadzeniu zmiany, którą wskazuje ten wpis.
- **Waga:** niska · **Kategoria:** zasady

<a name="wallet-quote-mismatch"></a>

### wallet_quote_mismatch

**Zatwierdzenie nie pasuje już do podglądu**

- **Co się stało:** Zatwierdzenie nie pasuje już do podglądanego przelewu, więc nic nie podpisano ani nie wysłano. Zacznij od nowa od świeżego podglądu.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Zgoda wiąże skrót jednej wyceny; zmienione konto, łańcuch, odbiorca, kwota, środowisko albo opłata to nowa zgoda.
- **Ponowna próba:** Nie wysyłaj ponownie tego samego żądania bez zmian.
- **Waga:** wysoka · **Kategoria:** bezpieczeństwo · **Istotne dla bezpieczeństwa:** tak

<a name="wallet-quote-unavailable"></a>

### wallet_quote_unavailable

**Nieznane saldo lub opłata**

- **Co się stało:** Sieć nie dała pełnej odpowiedzi o saldzie lub opłacie tego przelewu, więc nie przygotowano zatwierdzenia i nic nie wysłano. Spróbuj za chwilę.
- **Co mogło się już stać:** Żądanie dotarło do usługi zewnętrznej; nie wykonano żadnej płatności
- **Co zrobić dalej:** Nieznane dane łańcucha nigdy nie są traktowane jako zero; sprawdź punkt końcowy wiersza i jego dowód tożsamości.
- **Ponowna próba:** Spróbuj ponownie później: to, co to zatrzymało, z czasem ustępuje.
- **Waga:** średnia · **Kategoria:** dostępność

<a name="wallet-recipient-refused"></a>

### wallet_recipient_refused

**Odbiorca nie może bezpiecznie tego przyjąć**

- **Co się stało:** Ten odbiorca nie może bezpiecznie przyjąć tego przelewu w tej sieci, więc nic nie przygotowano. Sprawdź adres i spróbuj ponownie.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Odrzucono: nowe konto Solany pod minimum czynszu, konto wykonywalne albo poza krzywą, zła suma kontrolna EIP-55, adres zerowy, prekompilaty i kontrakty systemowe.
- **Ponowna próba:** Spróbuj ponownie po wprowadzeniu zmiany, którą wskazuje ten wpis.
- **Waga:** średnia · **Kategoria:** zasady

<a name="wallet-recovery-refused"></a>

### wallet_recovery_refused

**Dowód odzyskiwania nie pasuje do tego portfela**

- **Co się stało:** Ten dowód odzyskiwania nie pasuje do tego portfela, więc nic się nie zmieniło: zapisany klucz musi być kopią zapasową, którą VOOL pokazał dokładnie dla tego adresu, w formie, w jakiej była pokazana. Sprawdź i spróbuj ponownie albo użyj innych opcji.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Odtworzenie przepieczętowuje tylko istniejący portfel i tylko z kopii, która deszyfruje do własnego klucza portfela (obie strony).
- **Ponowna próba:** Spróbuj ponownie po wprowadzeniu zmiany, którą wskazuje ten wpis.
- **Waga:** wysoka · **Kategoria:** bezpieczeństwo · **Istotne dla bezpieczeństwa:** tak

<a name="wallet-request-ambiguous"></a>

### wallet_request_ambiguous

**Pasuje więcej niż jedno konto lub sieć**

- **Co się stało:** Do tego przelewu pasuje więcej niż jedno konto lub sieć, więc nic nie zaproponowano. Powiedz, które konto lub sieć masz na myśli, i spróbuj ponownie.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Przelew nazywa jedno konto w jednej sieci; kandydaci są wypisani w kontekście błędu.
- **Ponowna próba:** Spróbuj ponownie po wprowadzeniu zmiany, którą wskazuje ten wpis.
- **Waga:** niska · **Kategoria:** zasady

<a name="wallet-send-needs-vool-wallet"></a>

### wallet_send_needs_vool_wallet

**Do wysyłania potrzebny jest VOOL Wallet**

- **Co się stało:** To konto nie może wysyłać z VOOL w tej sieci: wysyła tu tylko VOOL Wallet utworzony na tym urządzeniu. Konto połączone lub w trybie podglądu nadal może odbierać środki i pokazywać saldo. Nic nie podpisano ani nie wysłano. Aby wysłać, wybierz VOOL Wallet na tym urządzeniu i spróbuj ponownie.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Wyślij z VOOL Wallet na tym urządzeniu albo z aplikacji połączonego portfela; w tej sieci VOOL niczego nie podpisuje za konto połączone lub w trybie podglądu.
- **Ponowna próba:** Spróbuj ponownie po wprowadzeniu zmiany, którą wskazuje ten wpis.
- **Waga:** niska · **Kategoria:** zasady

<a name="wallet-setup-state-invalid"></a>

### wallet_setup_state_invalid

**Krok konfiguracji nie ma teraz zastosowania**

- **Co się stało:** Ten krok konfiguracji nie dotyczy teraz tego portfela, więc nic się nie zmieniło. Sprawdź status konfiguracji portfela i spróbuj ponownie.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Konfiguracja idzie: generowanie, oczekiwanie na kopię, kopia ujawniona, gotowe; anulowanie i wznowienie nigdy nie ujawniają kopii drugi raz.
- **Ponowna próba:** Spróbuj ponownie po wprowadzeniu zmiany, którą wskazuje ten wpis.
- **Waga:** niska · **Kategoria:** zasady

<a name="wallet-signature-invalid"></a>

### wallet_signature_invalid

**Podpis nie pasował do klucza portfela**

- **Co się stało:** Podpis zwrócony dla tej płatności nie pasował do klucza portfela, więc nie został nadany.
- **Co mogło się już stać:** Żądanie dotarło do usługi zewnętrznej; nie wykonano żadnej płatności
- **Co zrobić dalej:** Sprawdź podłączonego podpisującego; niezgodny podpis jest odrzucany przed nadaniem.
- **Ponowna próba:** Nie wysyłaj ponownie tego samego żądania bez zmian.
- **Waga:** wysoka · **Kategoria:** bezpieczeństwo · **Istotne dla bezpieczeństwa:** tak

<a name="wallet-signing-unavailable"></a>

### wallet_signing_unavailable

**Portfel nie może stąd podpisywać**

- **Co się stało:** Ten portfel nie może stąd podpisywać: jest w trybie podglądu albo jego zewnętrzny podpisujący nie jest połączony. Nic nie wysłano; podepnij podpisującego i spróbuj ponownie.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Podłącz portfel zewnętrzny albo utwórz portfel VOOL w tej sieci w Ustawieniach, Krypto.
- **Ponowna próba:** Spróbuj ponownie po wprowadzeniu zmiany, którą wskazuje ten wpis.
- **Waga:** średnia · **Kategoria:** zasady

<a name="wallet-simulation-failed"></a>

### wallet_simulation_failed

**Płatność nie przeszła symulacji**

- **Co się stało:** Płatność nie przeszła symulacji, więc nie została wysłana. Sprawdź saldo i odbiorcę, potem spróbuj ponownie.
- **Co mogło się już stać:** Żądanie dotarło do usługi zewnętrznej; nie wykonano żadnej płatności
- **Co zrobić dalej:** Sprawdź saldo, odbiorcę i punkt końcowy RPC sieci, wtedy zgłoś ponownie.
- **Ponowna próba:** Spróbuj ponownie później: to, co to zatrzymało, z czasem ustępuje.
- **Waga:** średnia · **Kategoria:** dostępność

<a name="wallet-storage-class-refused"></a>

### wallet_storage_class_refused

**Przechowywanie kluczy tylko w pamięci**

- **Co się stało:** Ta instalacja trzyma klucz urządzenia tylko w pamięci, więc portfel tu utworzony nie mógłby być otwarty po restarcie. Nic nie utworzono. Przełącz przechowywanie kluczy na trwały tryb i spróbuj ponownie.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Portfele pilota krypto są odrzucane, gdy VOOL_KEY_STORAGE_MODE to ephemeral; użyj przechowywania file albo keyring.
- **Ponowna próba:** Spróbuj ponownie po wprowadzeniu zmiany, którą wskazuje ten wpis.
- **Waga:** średnia · **Kategoria:** zasady

<a name="wallet-unlock-throttled"></a>

### wallet_unlock_throttled

**Portfel zablokowany po nieudanych odblokowaniach**

- **Co się stało:** Było zbyt wiele błędnych prób PIN-u lub hasła, więc ten portfel jest na chwilę zablokowany i nic nie odblokowano. Odczekaj i spróbuj ponownie.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Nieudane odblokowania liczone są trwale per portfel i dla całej aplikacji, przez restarty; blokada schodzi sama.
- **Ponowna próba:** Spróbuj ponownie później: to, co to zatrzymało, z czasem ustępuje.
- **Waga:** wysoka · **Kategoria:** bezpieczeństwo · **Istotne dla bezpieczeństwa:** tak

<a name="wallet-x402-cap-exceeded"></a>

### wallet_x402_cap_exceeded

**Płatny zasób powyżej automatycznego limitu**

- **Co się stało:** Płatny zasób zażądał więcej niż automatyczny limit płatności, więc nie został opłacony.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Podnieś VOOL_WALLET_X402_CAP_MINOR świadomie albo zapłać za zasób jawnym wnioskiem.
- **Ponowna próba:** Nie wysyłaj ponownie tego samego żądania bez zmian.
- **Waga:** średnia · **Kategoria:** zasady · **Istotne dla bezpieczeństwa:** tak

<a name="wallet-x402-sdk-unavailable"></a>

### wallet_x402_sdk_unavailable

**Dodatek x402 nie jest zainstalowany**

- **Co się stało:** Ta wersja nie potrafi opłacać ofert x402 w sieci Base przez x402 Foundation SDK: na tym komputerze nie ma opcjonalnego dodatku x402. Nic nie podpisano ani nie wysłano. Każda inna funkcja portfela działa bez niego; po zainstalowaniu spróbuj ponownie.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Zainstaluj opcjonalny dodatek x402 VOOL, by opłacać oferty x402 w sieci Base z portfela Crypto Pilot; portfel w każdym przypadku zachowuje własnego sygnatariusza, limity wydatków i zatwierdzanie.
- **Ponowna próba:** Spróbuj ponownie po wprowadzeniu zmiany, którą wskazuje ten wpis.
- **Waga:** średnia · **Kategoria:** zasady

<a name="x402-scheme-unavailable"></a>

### x402_scheme_unavailable

**Nieobsługiwany schemat płatności**

- **Co się stało:** Ta oferta płatności wymaga schematu, którego ten portfel nie może udowodnić od początku do końca, więc nic nie podpisano. Wybierz ofertę, której schemat, token i pośrednik są zweryfikowane dla tej sieci.
- **Co mogło się już stać:** Nic nie wysłano, nie podpisano ani nie zmieniono
- **Co zrobić dalej:** Użyj oferty, której schema, token i pośrednik są zweryfikowani dla tej sieci; częściowe wsparcie podpisu tu nie wystarcza.
- **Ponowna próba:** Spróbuj ponownie po wprowadzeniu zmiany, którą wskazuje ten wpis.
- **Waga:** niska · **Kategoria:** zasady
