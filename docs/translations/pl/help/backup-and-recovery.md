---
description: Co możesz zapisać, przenieść i przywrócić w tej wersji VOOL: czaty, pamięć, klucze i portfele.
translation: machine
source_sha256: 3b2f11975a8cf5f5ab2e07e8b4a3083a9598de9e301965c493408005aa5893fa
---

# Kopia zapasowa i odzyskiwanie

Wszystko, co przechowuje VOOL, znajduje się na twoim komputerze, w jednym folderze profilu (gdzie — zobacz
[Konfiguracja](../reference/configuration.md)). Aktualizacja ani ponowna instalacja aplikacji
nigdy go nie dotyka. VOOL nie ma kopii twoich danych w chmurze ani kopii zapasowej całego profilu
wykonywanej jednym krokiem. Oto sposoby na zapisanie i przywrócenie tego, co ważne.

## Czaty

| Czego potrzebujesz | Czego użyć | Co dostajesz |
| --- | --- | --- |
| Kopia do przeczytania lub udostępnienia | **Eksportuj** w nagłówku czatu | Cały czat jako tekst lub Markdown. Załączone pliki są wymienione tylko z nazwy. |
| Kopia do ponownego wczytania do VOOL | Ustawienia, Kopia i odtwarzanie, **Wyeksportuj rozmowę** | Jeden czat jako plik `.voolsession`, podpisany przez ten komputer. |
| Przywrócenie czatu z tego pliku | Ustawienia, Kopia i odtwarzanie, **Zaimportuj rozmowę** | Czat, dodany obok pozostałych. |
| Twoje czaty z innych aplikacji AI | Ustawienia, Kopia i odtwarzanie, **Przenieś czaty z innych aplikacji AI** | Te czaty, z ich nazwami i wiadomościami. |

**Wyeksportuj rozmowę** przenosi wiadomości czatu, jego potwierdzenia i odwołania do dowodów, nigdy
wewnętrzne rozumowanie VOOL, twoje ustawienia, pamięć ani klucze. Gdy plik opuszcza ten komputer, chroń go
hasłem: eksport bez hasła najpierw prosi o potwierdzenie. Podgląd pokazuje dokładnie, co zostanie zapisane,
zanim cokolwiek zostanie zapisane.

**Zaimportuj rozmowę** pokazuje, co zawiera plik i kto go podpisał, zanim cokolwiek zostanie dodane. Plik
podpisany kluczem, którego VOOL nie zna, jest odrzucany, chyba że go zaakceptujesz, a czat pozostaje
oznaczony jako niezaufany. Import nigdy nie zmienia czatu, który już masz: jeśli ten sam czat już tu jest,
kopia pojawia się jako nowy czat.

**Przenieś czaty z innych aplikacji AI** odczytuje aplikację na tym komputerze albo plik eksportu danych.
Zaznaczasz czaty, które chcesz przenieść. Nic nie opuszcza twojego komputera, a pliki innej aplikacji nigdy
nie są zmieniane. VOOL przechowuje te czaty jako historię: nie uczy się z nich ani nie wykonuje niczego,
co w nich zapisano.

## Pamięć

Ustawienia, Pamięć i personalizacja, **Eksportuj profil** zapisuje to, co VOOL pamięta o tobie, jako
plik o nazwie `vool-memory-` z datą. Nigdy nie zawiera klucza ani hasła. To kopia do przeczytania
i zachowania: VOOL nie ma funkcji jej importu.

## Klucze dostawców modeli

Kluczy dostawców nie ma w żadnym eksporcie. Na nowym komputerze dodaj je ponownie w Ustawieniach.

## Portfele

* **Portfel VOOL utworzony dla sieci** pokazuje jedną kopię zapasową klucza prywatnego podczas konfiguracji
  i tylko wtedy. Przechowuj ją offline: każdy, kto ją ma, kontroluje portfel. Jeśli zapomnisz PIN-u lub hasła
  portfela, **Forgot PIN or password?** przy portfelu w Ustawieniach, Krypto przywraca dostęp za pomocą tej
  kopii zapasowej, a ty wybierasz nowy PIN lub hasło.
* **Starsza kieszeń** jest zabezpieczona swoją frazą odzyskiwania, pokazaną raz przy jej tworzeniu.
  **Restore from phrase…** w Ustawieniach, Krypto, Inne konta przywraca ją z tej frazy. Może ona też
  wyeksportować swój klucz prywatny do Phantom lub MetaMask za pomocą **Export private key**, co otwiera się
  tylko z Touch ID lub hasłem do Maca.
* Portfel, który tylko obserwujesz, albo portfel podłączony z innej aplikacji nie przechowuje w VOOL żadnego
  klucza: nie ma tu czego zabezpieczać kopią zapasową.

Pełny obraz przedstawia strona [Portfel](../guides/wallet.md).

## Gdy aktualizacja się nie powiedzie

Aktualizacja, która nie powiedzie się na dowolnym etapie, pozostawia aplikację w takim stanie, w jakim była.
Jeśli przeniesienie danych do nowej wersji się nie powiedzie, dane zostają przywrócone do poprzedniego stanu,
a VOOL uruchamia się w poprzedniej wersji. Zobacz [Aktualizacje i migracja](../reference/updates-migration.md).
