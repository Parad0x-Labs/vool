---
description: Jak czytać błąd VOOL, czy coś już się stało i gdzie wyjaśniony jest każdy kod.
translation: machine
source_sha256: ccb01d4c778254a055fd67f46241c670aac90cfc83f168b923a19cef1f949de2
---

# Błędy i kody błędów

Gdy coś się nie powiedzie, VOOL mówi prostymi słowami, co się stało, co mogło się już stać
i co zrobić dalej. Każde niepowodzenie ma też stały kod. Znajdziesz go w szczegółach technicznych
pod błędem, w wierszu poleceń (`wallet_export_refused: …`) i w odpowiedziach API. Ta
strona wyjaśnia, jak czytać błąd. [Księga błędów](../ERROR_BOOK.md) wyjaśnia każdy kod.

## Jak czytać błąd

Zanim spróbujesz ponownie, sprawdź trzy rzeczy:

1. **Co się stało.** Komunikat. Nigdy nie zawiera wewnętrznych ścieżek ani sekretów.
2. **Co mogło się już stać.** Większość odmów zatrzymuje się, zanim cokolwiek zostanie wysłane lub zmienione,
   i komunikat to mówi. Przy niektórych niepowodzeniach nie da się ustalić, czy czynność się zakończyła, na przykład
   przy przekroczeniu limitu czasu po wysłaniu żądania. Wtedy komunikat mówi, że wynik jest nieznany, i prosi
   o sprawdzenie przed ponowną próbą. Płatność nigdy nie zostanie przypadkiem wysłana dwa razy: drugie wysłanie
   tej samej płatności jest odrzucane z kodem
   [`wallet_duplicate_payment`](../ERROR_BOOK.md#wallet-duplicate-payment).
3. **Co zrobić dalej.** Jeden konkretny krok.

Znaczenie niesie kod. Status HTTP, taki jak 403 lub 429, mówi tylko, jak żądanie poradziło sobie
po drodze, a dwie różne odmowy mogą mieć ten sam status. Czytaj kod, a nie status.

## Kody i Księga błędów

Kod nigdy nie zmienia nazwy i nigdy nie jest używany ponownie w innym znaczeniu.
[Księga błędów](../ERROR_BOOK.md) jest generowana z własnego katalogu błędów aplikacji, więc kody,
komunikaty i kolejne kroki są w niej dokładnie takie, jak pokazuje je aplikacja. Każdy kod ma własną sekcję:
wyszukaj na stronie kod dokładnie w takiej postaci, w jakiej został ci podany, albo podaj link do sekcji, używając
adresu księgi, `#` i kodu z podkreśleniami zamienionymi na łączniki, na przykład
[`wallet_broadcast_failed`](../ERROR_BOOK.md#wallet-broadcast-failed).

Kody, które najprawdopodobniej zobaczysz:

| Kod | Tytuł | Co mogło się już stać |
| --- | --- | --- |
| [`permission_denied`](../ERROR_BOOK.md#permission-denied) | Brak uprawnień | Nic nie zostało zmienione. |
| [`provider_unavailable`](../ERROR_BOOK.md#provider-unavailable) | Dostawca nieosiągalny | Nic nie zostało wysłane. |
| [`provider_exhausted`](../ERROR_BOOK.md#provider-exhausted) | Osiągnięto limit dostawcy | Żądanie dotarło do dostawcy. |
| [`timeout`](../ERROR_BOOK.md#timeout) | Przekroczono limit czasu | Nieznane: sprawdź przed ponowną próbą. |
| [`cancelled`](../ERROR_BOOK.md#cancelled) | Anulowano | Nieznane: część mogła już zostać wykonana. |
| [`credential_failure`](../ERROR_BOOK.md#credential-failure) | Nie udało się odczytać zapisanego poświadczenia | Nic nie zostało wysłane ani zmienione. |
| [`unknown`](../ERROR_BOOK.md#unknown) | Nieoczekiwany błąd | Nieznane: sprawdź przed ponowną próbą. |
| [`wallet_quote_mismatch`](../ERROR_BOOK.md#wallet-quote-mismatch) | Zatwierdzenie nie pasuje już do podglądu | Nic nie zostało podpisane ani wysłane. |
| [`wallet_broadcast_failed`](../ERROR_BOOK.md#wallet-broadcast-failed) | Nie udało się nadać płatności | Nieznane: przed ponowną próbą sprawdź status płatności. |
| [`wallet_duplicate_payment`](../ERROR_BOOK.md#wallet-duplicate-payment) | Płatność już zaproponowana lub wysłana | Nic nowego nie zostało wysłane; wcześniejsza płatność jest zapisana. |

## Błędy i inne wyniki

VOOL odróżnia od siebie te sytuacje i tak samo robią jego komunikaty:

* **Błąd.** Coś się nie powiodło. Kod i komunikat mówią, co, i co zrobić.
* **Czeka na ciebie.** Otwarta jest prośba o zgodę albo zatwierdzenie płatności. Nic się nie uruchomiło
  i nic się nie uruchomi, dopóki nie odpowiesz. Zobacz [Uprawnienia](../concepts/permissions.md).
* **Zatrzymane przez limit wydatków lub zabezpieczenie cenowe.** Odmowa następuje, zanim nastąpi kontakt
  z dostawcą, a komunikat nigdy nie sugeruje, że dostawca został zapytany. Zobacz
  [Limity wydatków i zabezpieczenia cenowe](../guides/spending-limits.md).
* **Anulowano.** Zostało zatrzymane przed zakończeniem, przez ciebie albo przez VOOL. Część mogła już zostać
  wykonana, więc zanim uruchomisz to ponownie, sprawdź, co się zmieniło.
* **Tutaj niemożliwe.** Ta kompilacja albo ten komputer nie może tego zrobić, a komunikat wskazuje, co może:
  oferta płatności, której schematu VOOL nie potrafi sprawdzić od początku do końca, albo opcjonalny dodatek,
  który nie jest zainstalowany.
* **Wynik nieznany.** Czynność mogła się zakończyć albo nie, na przykład płatność, której nadanie nigdy nie
  zostało potwierdzone. Najpierw sprawdź jej status. VOOL nigdy nie płaci ponownie na ślepo.

## Błędy z własnymi kodami

Kilka części VOOL objaśnia swoje niepowodzenia własnymi krótkimi kodami, na ekranie, na którym do nich
dochodzi, i tych kodów nie ma w Księdze błędów: załączniki (na przykład zbyt duży plik albo poświadczenie
znalezione we wklejonym pliku), aktualizacje aplikacji, foldery projektów, wybór modelu, limity wydatków,
kontakty, wtyczki i umiejętności, wprowadzanie głosowe, przechowywane dane oraz raporty o błędach. Ich
komunikaty w ten sam sposób mówią, co się stało i co zrobić. Zobacz
[Czaty i załączniki](../concepts/chats-and-attachments.md),
[Aktualizacje i migracja](updates-migration.md) oraz
[Zgłaszanie błędów i prywatność](../trust/bug-reporting.md).

## Kod, którego nie rozpoznajesz

Kod, którego nie ma w Księdze błędów, nie pochodzi z tej wersji VOOL. Sprawdź, która wersja go
pokazała, a potem przeszukaj [Księgę błędów](../ERROR_BOOK.md) tej wersji. Jeśli jednak pochodzi
z tej wersji, zgłoś go z kodem zapisanym dokładnie tak, jak został pokazany, oraz z identyfikatorem
(`fault-` i dwanaście znaków), jeśli odpowiedź go zawierała. Niepowodzenie, którego VOOL nie potrafi
sklasyfikować, jest pokazywane jako [`unknown`](../ERROR_BOOK.md#unknown), nigdy jako zgadywana przyczyna.
Zobacz [Zgłaszanie błędów i prywatność](../trust/bug-reporting.md).

## Rozwiązywanie konkretnych problemów

Problemy z instalacją, modelami i platformami opisuje strona [Rozwiązywanie problemów](../help/troubleshooting.md),
a odzyskiwanie czatów, pamięci i portfela — strona [Kopia zapasowa i odzyskiwanie](../help/backup-and-recovery.md).
