---
description: Bezpieczne zgłaszanie błędów — zredagowane szkice, zatwierdzanie dokładnych bajtów i to, co nigdy nie opuszcza twojego komputera.
translation: machine
source_sha256: 4e64e6313c1f6a8be6784a1f35cedc87b81a5661fdb548c6821109f935db818f
---

# Zgłaszanie błędów i prywatność

## Wbudowane narzędzie do zgłaszania błędów

Użyj narzędzia do zgłaszania błędów w aplikacji (albo wiersza poleceń `python -m apps.vool_cli bug-report`).
Nigdy niczego nie wysyła samo — proces celowo ma trzy etapy:

1. **Szkic** — raport jest tworzony lokalnie z ostatnich logów i danych diagnostycznych i redagowany już
   podczas tworzenia. *„Nothing is sent yet — a local draft is created first.”*
2. **Podgląd** — widzisz **dokładne bajty**, które zostałyby wysłane, wraz ze skrótem ładunku i podsumowaniem
   redakcji, które wymienia, co zostało usunięte. Pola, których nie chcesz wysyłać (logi, flagi, notatki
   o odtwarzaniu problemu, załączniki), można usunąć; każda edycja kasuje zatwierdzenie.
3. **Zatwierdzenie** — wysłanie wymaga twojego wyraźnego zatwierdzenia dokładnie tych bajtów. Jeśli ładunek
   się zmieni, zatwierdzenie przestaje obowiązywać i musisz zatwierdzić ponownie.

Po zatwierdzeniu **Open on GitHub** otwiera w twojej przeglądarce wypełnioną stronę raportu w publicznym
repozytorium [vool-reports](https://github.com/Parad0x-Labs/vool-reports). Przeczytaj go tam jeszcze raz
i naciśnij Submit. Sam VOOL niczego nie wysyła i nie jest potrzebny żaden token GitHub.
**Copy report** i **Save a local copy** w ogóle nie wymagają zatwierdzenia: dają ci te same oczyszczone
bajty i nic nie opuszcza twojego komputera. **My reports** wymienia twoje szkice i to, w jaki sposób każdy
z nich został przekazany.

## Zgłaszanie błędnej odpowiedzi

Naciśnij **Zgłoś** albo 👎 pod dowolną odpowiedzią albo zaznacz błędny fragment i naciśnij **Zgłoś**.
Raport cytuje tylko ten fragment (albo całą odpowiedź). Twoje pytanie jest pomijane, chyba że zaznaczysz
pole wyboru. Dołączane jest potwierdzenie odpowiedzi: model, dostawca, tokeny, czas i to, które kroki zostały
wykonane, nigdy ich treść. Odpowiedzi, którą VOOL na twoją prośbę zapomniał, nie można zgłosić.

## Co jest dodawane, aby pomóc w badaniu problemu

Charakterystyka twojego komputera: wersja systemu operacyjnego, chip, pamięć RAM, GPU i wersja lokalnego serwera
modeli. Do tego krótka kontrola stanu: czy lokalny serwer modeli odpowiada oraz ilość wolnego miejsca na dysku
w postaci przedziału. Nigdy nazwa twojego komputera, twoja nazwa użytkownika, numery seryjne ani adresy
sieciowe. W podglądzie możesz usunąć każdą z tych informacji.

## Co usuwa redakcja

Deterministycznie, zanim w ogóle zobaczysz szkic: poświadczenia i nagłówki uwierzytelniania, ścieżki folderów
domowych i nazwy użytkowników, nazwę twojego komputera, adresy e-mail, adresy IP, PIN-y i kody jednorazowe,
numery kart i kont bankowych (IBAN), numery telefonów, adresy portfeli, dane osobowe zapisane w twoim
profilu, oznaczone sekrety i bloki danych o wysokiej entropii — a na końcu całość przechodzi jeszcze
skanowanie w poszukiwaniu sekretów.
VOOL nie rozpoznaje nazwisk ani prywatnych faktów wpisanych w zwykłych zdaniach, więc przeczytaj podgląd. Ślady stosu są odtwarzane na podstawie listy dozwolonych elementów, a nie wklejane w surowej postaci.
Dowody błędów w raportach zawierają **wyłącznie kody o określonym typie** — nigdy kontekstu wewnętrznego
ani łańcuchów przyczyn.

Każda usunięta wartość jest zastępowana znacznikiem. Adres e-mail, osoba, folder domowy, adres IP,
numer telefonu, adres portfela, nazwa twojego komputera lub dane z twojego profilu zachowują w całym raporcie
jeden numerowany znacznik, na przykład `[EMAIL_1]` i `[EMAIL_2]` dla dwóch różnych adresów, dzięki czemu
raport pozostaje czytelny.
Każdy usunięty klucz, hasło, PIN, numer karty lub konta bankowego jest pokazywany jako `[SECRET_REMOVED]`,
nigdy z numerem. Lista łącząca znacznik z prawdziwą wartością jest przechowywana tylko w pamięci operacyjnej
VOOL, gdy pracujesz nad raportem, więc edycja zachowuje te same znaczniki. Nigdy nie jest zapisywana ani
wysyłana i zostaje zapomniana, gdy usuniesz raport, po godzinie nieużywania albo gdy VOOL uruchomi się ponownie.

## Czego VOOL nigdy nie robi

* Żadnego automatycznego zgłaszania czegokolwiek na zewnątrz.
* Żadnego mechanizmu telemetrii czekającego na włączenie w ustawieniach.
* Problemy z bezpieczeństwem trafiają prywatnym kanałem opisanym w
  [zasadach bezpieczeństwa](https://github.com/Parad0x-Labs/vool/security/policy) repozytorium — nigdy przez
  publiczne zgłoszenie.

## Podsumowanie prywatności

* Gdy wybrany jest model lokalny, prompty i pliki są przetwarzane na twoim komputerze.
* Wywołania w chmurze używają podanego przez ciebie klucza API, przechowywanego lokalnie; klucze nigdy nie
  trafiają do raportów.
* Pełny opis przepływu danych zawiera strona [Przetwarzanie danych](../trust/data-handling.md).
