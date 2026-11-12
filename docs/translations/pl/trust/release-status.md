---
description: Na jakim etapie jest VOOL, powiedziane wprost.
translation: machine
source_sha256: bf79dfc262c1aca9c489c8474ad2de5fa633b0b60a515f2835f52954c66ad73f
---

# Stan wydań

Ta strona jest utrzymywanym, rozstrzygającym źródłem informacji o stanie wydań. Inne dokumenty podają fakty
o wydaniach przy okazji; gdy nie zgadzają się z tą stroną, rozstrzyga ta strona.

## Obecny etap

VOOL jest w fazie **beta**. Obecne wydanie to
[**v0.7.7-beta**](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.7-beta), wydanie w GitHub
zbudowane z `main` w `8af687c`. Zawiera pliki aplikacji do pobrania dla trzech platform, każdy z opublikowaną
sumą kontrolną SHA-256. Instalacja ze źródeł za pomocą skryptu startowego nadal jest obsługiwana w macOS,
Linuksie i Windows; zobacz [Instalacja](../getting-started/install.md).

| Plik do pobrania | Dla |
| --- | --- |
| `VOOL-0.7.7-beta-macos-arm64.dmg` | Maki z Apple Silicon, macOS 14 lub nowszy |
| `VOOL-0.7.7-beta-macos-x86_64.dmg` | Maki z procesorem Intel, macOS 14 lub nowszy |
| `vool-0.7.7-beta-linux-x86_64.tar.gz` | Linux x86_64, testowane na Ubuntu 22.04 i 24.04 |

Zapisy wydań w tym repozytorium obejmują kanał GitHub. Inne kanały pobierania są prowadzone osobno; każdy
pobrany plik sprawdzaj względem sumy kontrolnej opublikowanej w wydaniu w GitHub.

| | Stan |
| --- | --- |
| Publiczne pliki do pobrania | DMG dla macOS na Apple Silicon, DMG dla macOS na procesory Intel i archiwum tar dla Linuksa x86_64 w wydaniu v0.7.7-beta w GitHub |
| Opublikowane sumy kontrolne | SHA-256 dla każdego pliku do pobrania, w wydaniu |
| Podpisywanie kodu | Aplikacje na macOS są podpisane ad hoc, a nie certyfikatem Developer ID. Podpisywanie zostanie dodane po tej becie i nie jest warunkiem jej wydania |
| Poświadczenie notarialne macOS | Brak poświadczenia notarialnego. macOS prosi o potwierdzenie pierwszego otwarcia (zobacz niżej) |
| Windows | W tym wydaniu nie ma pliku do pobrania dla Windows; na Windows VOOL instaluje się ze źródeł i jest to wsparcie eksperymentalne |
| Aktualizacje | Od wersji 0.7.5 VOOL aktualizuje się sam: gdy włączone jest „Samodzielne łączenie z internetem”, pokazuje powiadomienie i instaluje nową wersję jednym naciśnięciem. Aktualizacja z 0.7.4 lub 0.7.3 na Macu: włącz „Samodzielne łączenie z internetem” (Ustawienia > Prywatność i uprawnienia) i raz naciśnij „Sprawdź aktualizacje”. Na Linuksie albo z 0.7.2, 0.7.1 lub 0.7.0: pobierz 0.7.7 i zainstaluj ją na obecnej wersji. Twoje czaty i ustawienia są przechowywane w twoim profilu, poza aplikacją. Zobacz [Aktualizacje i migracja](../reference/updates-migration.md) |
| Stabilność API | Zmienia się między kompilacjami |

## Co to oznacza dla ciebie

* Przy pierwszym uruchomieniu aplikacji na Maca macOS odmawia jej otwarcia, ponieważ nie jest poświadczona
  notarialnie. Na tym etapie jest to oczekiwane i nie świadczy o uszkodzonym pliku. Sprawdź sumę kontrolną,
  a potem otwórz **Ustawienia systemowe → Prywatność i ochrona** i kliknij **Otwórz mimo to**. W
  macOS 14 możesz zamiast tego kliknąć aplikację, trzymając naciśnięty klawisz Control, i wybrać **Otwórz**.
* Wybierz plik dla Maca pasujący do twojego procesora. Aplikacja dla Apple Silicon nie działa na Macu
  z procesorem Intel i informuje o tym przy uruchomieniu.
* Formaty konfiguracji mogą się zmieniać między kompilacjami.
* Funkcje kryptowalutowe są wyłączone, dopóki ich nie włączysz.

## Ograniczenia bety i pilotażu, wymienione wprost

| Funkcja | Stan w tej kompilacji |
| --- | --- |
| Krypto i portfel | Domyślnie wyłączone. Gdy je włączysz, VOOL używa mainnetu; sieci testowe są w sekcji Developer options. Każda płatność przechodzi przez portfel VOOL, z jego limitami, twoim zatwierdzeniem i potwierdzeniem. Zobacz [Portfel](../guides/wallet.md). |
| Przelewy w mainnecie | Rodzime monety oraz USDT w Solanie, Ethereum, BNB Smart Chain i Arbitrum One, wysyłane z portfela VOOL utworzonego na tym urządzeniu. USDC w Solanie i Base służy tylko do kanałów płatności. Konto podłączone lub tylko do podglądu może odbierać środki i pokazywać saldo, ale nie może wysyłać z VOOL. |
| Płacenie przez x402 | Strony internetowe, które żądają płatności x402 w USDC w sieci Base lub Base Sepolia, mogą być opłacane z twojego portfela Crypto Pilot: jednym zatwierdzeniem PIN-em, w ramach twoich limitów. Usługa płatności strony pokrywa opłatę sieciową. **Ustawienia → Krypto → Payment channels** ma przełącznik wł./wył. dla każdego sposobu, w jaki portfel może zapłacić stronie; wyłączony kanał odmawia, zanim cokolwiek zostanie podpisane. Żadna płatność na żywo w mainnecie nie została jeszcze w ten sposób wykonana. Płacenia przez Solana pay-kit nie ma w plikach do pobrania; ten kanał jest pokazywany jako niedostępny. |
| X | Z dowolnego czatu: wyszukiwanie postów i kont, Spaces, trendów i wiadomości prywatnych (DM) oraz przeglądanie wcześniejszych typowań dotyczących tokena. Wszystko, co działa na twoim koncie, czeka na twoją zgodę. |
| Dyktowanie | Tymczasowo niedostępne w tej kompilacji beta; wróci w jednej z aktualizacji. |
| Tryb chmury „burst” z pytaniem | Pytanie przed każdym wywołaniem nie jest jeszcze podłączone w tej kompilacji. |
| Modele lokalne | Zanim model lokalny będzie mógł odpowiadać, otwórz **Ustawienia → Modele i dostawcy → Modele lokalne** i raz naciśnij dla niego **Certify**. |
| Maki z procesorem Intel | Modele lokalne działają na procesorze i są powolne. Certyfikacja uwzględnia wolniejsze procesory, ale długa odpowiedź korzystająca z pamięci może przekroczyć limit czasu, więc na razie używaj tam modelu w chmurze. |
| Prywatny czat | Powiedz „prywatny czat”, „nie zapisuj tego” albo „tylko między nami”, a nic z tego czatu nie zostanie zapisane. Polecenia działają we wszystkich 17 językach aplikacji. |
| Zapominanie | Zapominanie czyści pamięć VOOL i pasującą historię przebiegów, więc dany element nie jest ponownie przywoływany ani wyświetlany, oraz usuwa zapomniane słowa z lokalnego pliku pamięci VOOL i jego indeksu wyszukiwania. VOOL przechowuje słowa do zapomnienia na krótkiej liście na twoim komputerze, aby mógł nadal je odrzucać. Gdy zapomnisz coś w czacie, VOOL nie przyjmie z powrotem tego samego faktu w tym czacie; aby powiedzieć mu to ponownie, zacznij nowy czat. |
| Zeskanowane pliki PDF | Odczytywane za pomocą wbudowanego w Maca rozpoznawania tekstu, więc tylko na Macach. |
| Zwierzak na pulpicie | Zwierzak mieszka w oknie VOOL. Przeciągnięcie go poza okno nie przenosi go na pulpit. |
| Kodeks umiejętności | „Przeglądaj kodeks umiejętności” pokazuje umiejętności dostępne dla twojej dokładnej wersji VOOL, a każdą umiejętność przeglądasz przed jej instalacją. „Opublikuj w kodeksie umiejętności” sprawdza umiejętność utworzoną na twoim komputerze, a potem otwiera stronę internetową kodeksu umiejętności z wypełnionymi danymi tej umiejętności; logujesz się tam i naciskasz Send, a sam VOOL niczego nie wysyła. Przekazywany jest tylko tekst umiejętności, więc umiejętności z dodatkowymi plikami nie można jeszcze udostępniać. |

Podpisywanie certyfikatem Developer ID i poświadczanie notarialne dla macOS oraz podpisane sumy kontrolne dla
Linuksa to kolejne kroki na drodze do wydania; dziś nie deklaruje się żadnego z nich.
