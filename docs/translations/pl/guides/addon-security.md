---
translation: machine
source_sha256: 671f147e5d9649b6c2b7c2d391ea085120e783a8ace29f92ac8c3d5ecb4407eb
---
# Odkrywanie umiejętności i kontrole Eyebrow

Strona Umiejętności i wtyczki ma cztery karty: **Przeglądaj**, **Kodeks umiejętności**, **Moje dodatki** i **Bezpieczeństwo**. Przeglądaj pokazuje najpierw własne umiejętności VOOL, w tym 53 tekstowe umiejętności VOOL dołączone do aplikacji, potem przejrzane dodatki z katalogu, a ponadto może wyszukać publiczne repozytorium GitHub w przypiętej rewizji. Kodeks umiejętności pokazuje najpierw umiejętności zespołu VOOL, przed wyborem redakcji i polecanymi umiejętnościami. Wyszukiwanie ignoruje wielkość liter i typowe separatory; filtry kategorii i sortowanie zawężają listę. Źródło, licencja, rewizja i wymagania są widoczne przed pobraniem.

## Pierwsze użycie

1. Zapisz klucz API Eyebrow na karcie Bezpieczeństwo, dostępnej też w Ustawienia → Klucze API. Kluczem zarządza istniejący magazyn poświadczeń. Zapisanie nie testuje połączenia ani niczego nie skanuje; z Eyebrow łączy się jawnie dopiero przycisk Przetestuj połączenie.
2. Na karcie Przeglądaj wybierz umiejętność i zatwierdź pobranie jej publicznej zawartości oraz wysłanie tej zawartości do Eyebrow. Zużywa to limit skanowań usługi zgodnie z twoim planem. Nie ma automatycznych ponownych prób.
3. Przeczytaj zwrócony raport. Sama udana odpowiedź HTTP nie oznacza czystego wyniku. Ten początkowy katalog pozwala na instalację tylko przy kompletnym raporcie `pass`, bez żadnych wykrytych problemów i bez naruszeń zasad. Odrzuć usuwa przygotowane pobranie bez instalowania go.
4. Zaakceptuj instalację osobno. VOOL zachowuje licencję, instaluje przez swój istniejący cykl życia wtyczek, weryfikuje dokładnie przejrzany pakiet i włącza umiejętność. Istniejące uprawnienia nadal decydują o każdej czynności, którą umiejętność mogłaby zasugerować.

Skanowanie to ocena konkretnych bajtów dokonana przez dostawcę usługi, a nie gwarancja bezpieczeństwa, zgodności ani jakości odpowiedzi. Umieszczenie w katalogu nie jest certyfikacją wydawcy.

## Granice

Zawartość pochodzi wyłącznie z publicznych repozytoriów GitHub w stałej, 40-znakowej rewizji, z plikami umiejętności i licencji przypiętymi przez SHA-256. W tej wersji nie ma obsługi prywatnych repozytoriów, rozpakowywania archiwów, skryptów instalacyjnych ani importera wtyczek wykonywalnych/MCP. Do Eyebrow wysyłany jest tylko samodzielny plik SKILL.md; lokalne czaty, pliki obszaru roboczego, poświadczenia i treść licencji nie są przesyłane do skanowania.

Pobrane pliki pozostają nieaktywne do czasu przeglądu i akceptacji. Lokalnie uwierzytelnione potwierdzenia wiążą raport z przypiętą zawartością. Instalacja odrzuca wygasłe przeglądy (24 godziny), zmodyfikowane przygotowane pliki i istniejące katalogi docelowe. Środowisko uruchomieniowe sprawdza stan cyklu życia i przejrzane bajty, zanim doda zarządzaną umiejętność do kontekstu modelu. Dodane pliki, dowiązania symboliczne lub zmieniona zawartość czynią ją niedostępną. Wskazówki są wstawiane w całości albo jawnie odnotowywane jako pominięte z powodu niewystarczającego budżetu promptu; zeskanowane instrukcje nie są po cichu obcinane.

Te kontrole nie izolują złośliwego kodu, który już działa jako użytkownik systemu operacyjnego. Nie obejmują wcześniej ręcznie zainstalowanych wtyczek. Potwierdzenie skanowania nie przyznaje żadnego nowego uprawnienia do wykonywania kodu ani uprawnień w portfelu.

## API i obsługa błędów

`GET /api/addons` zwraca katalog i stan lokalny bez testu połączenia i bez skanowania. `POST /api/addons` obsługuje `save_key`, `remove_key`, `test_key`, `scan`, `install` i `reject`. Operacje zmieniające stan przyjmują tylko żądania przez interfejs loopback, w formacie JSON i z przeglądarki z tego samego źródła; akceptowane są tylko pola właściwe dla danej operacji. Adresy URL źródeł i ścieżki podane przez wywołującego są odrzucane.

Klient korzysta z żądania Eyebrow v1, które przesyła źródło umiejętności w treści, pod adresem `/v1/scan`, z uwierzytelnianiem typu bearer, stałym źródłem HTTPS, bez przekierowań, z ograniczonym rozmiarem odpowiedzi i bez automatycznych ponownych prób. `/v1/version` służy do jawnego testu połączenia. Dokumentacja API: https://eyebrow.cc/api/reference .

Typowe błędy z określonym kodem to między innymi:

| Kod | Znaczenie / co zrobić |
| --- | --- |
| `eyebrow_key_missing` / `eyebrow_unauthorized` | Zapisz lub wymień klucz. |
| `eyebrow_quota` | Przed ponowną próbą sprawdź limit usługi. |
| `eyebrow_timeout` / `eyebrow_unreachable` | Brak automatycznej ponownej próby; skanowanie przerwane po przekroczeniu czasu może mimo to zużyć limit. |
| `eyebrow_invalid_report` / `eyebrow_no_coverage` | Dowody są niekompletne; nic nie zostaje włączone. |
| `addon_source_changed` / `addon_changed` | Przypięte lub przejrzane bajty się różnią; nie aktywuj ich. |
| `review_untrusted` / `review_expired` | Przed instalacją uzyskaj nowy przegląd. |
| `addon_already_installed` | Istniejące pliki zostały zachowane. Automatyczna wymiana nie jest zaimplementowana. |

## Obecne ograniczenia i weryfikacja

Nie ma automatycznych aktualizacji, importera dowolnych katalogów ani procesu naprawy lub ponownej instalacji na miejscu dla istniejącego, zmienionego pakietu. Odzyskiwanie oczekujących przeglądów po ponownym uruchomieniu aplikacji nie jest dostępne w interfejsie. Nowe teksty interfejsu mają obecnie zapasową wersję angielską; kompletnych tłumaczeń się nie deklaruje.

Testy automatyczne sprawdzają rzeczywisty cykl życia, ładowanie umiejętności, procedury obsługi API i udostępniany interfejs w przeglądarce, z jawnie zasymulowanymi publicznymi pobraniami i odpowiedziami Eyebrow. Dowodzą działania środowiska uruchomieniowego i interfejsu, a nie dostępności Eyebrow na żywo ani kompetencji modelu. Rzeczywiste skanowanie z prawdziwym kluczem i akceptacja natywnej spakowanej aplikacji pozostają kontrolami wydania. Do uruchomienia tych testów nie jest potrzebny żaden klucz ani płatne wywołanie usługi.
