---
description: Opcjonalny portfel Crypto Pilot — sieci, płatności x402, opłaty DNA i odzyskiwanie w takiej postaci, w jakiej ta kompilacja je faktycznie dostarcza.
translation: machine
source_sha256: 46121a5ed26be175ddd3c66b269df356e074adbb57c8e9806461b80197613cc2
---

# Portfel

Portfel jest **opcjonalny i domyślnie wyłączony**. Istnieje po to, aby VOOL mógł przechowywać niewielką ilość
kryptowalut na usługi AI, które pobierają opłatę za każde żądanie. Do korzystania z VOOL nic z nim związanego
nie jest wymagane.

Ta strona opisuje, co obecna kompilacja faktycznie obsługuje. Portfel jest w fazie **pilotażowej**:
możliwości są dodawane ścieżka po ścieżce, a przy każdej ścieżce poniżej podano dokładnie, co robi.

## Środowiska sieciowe

Gdy Krypto jest włączone, VOOL działa na **Mainnet**: Solana, Robinhood Chain, Base, Ethereum, BNB i
Arbitrum One.
Zwykła konfiguracja nigdy nie prosi o wybór środowiska sieciowego.

* **Mainnet** — domyślne dla każdej instalacji, także instalacji uaktualnionych ze starszymi kontami
  w sieciach testowych. Każdy limit wydatków, zatwierdzenie i potwierdzenie działa tak, jak opisano poniżej.
* **Test networks** — Solana Devnet, Robinhood Chain Testnet i pozostałe ścieżki testowe. Tylko
  dla deweloperów: przełączasz w **Ustawienia → Krypto → Developer options**.

Konta, salda i potwierdzenia należą do jednego środowiska i nigdy nie przechodzą między środowiskami; konta
z drugiego środowiska pozostają widoczne we własnej sieci. Żądanie dotyczące sieci z drugiego
środowiska jest odrzucane i nic nie zostaje utworzone, zaproponowane ani podpisane.

{% hint style="warning" %}
Wcześniejsza dokumentacja mówiła, że mainnet jest w tej kompilacji niemożliwy. Było to prawdą przed
pilotażem i już nie jest: wiersze mainnetu teraz istnieją. Prawdą pozostaje coś węższego — sekcje
o przelewach i płatnościach poniżej mówią dokładnie, które ścieżki istnieją w mainnecie.
{% endhint %}

## Co przechodzi którą ścieżką

* **Przelewy przenoszą rodzime monety** (na przykład SOL w Solanie, ETH w Base, Ethereum lub
  Arbitrum One) oraz **USDT w Solanie, Ethereum, BNB Smart Chain i Arbitrum One**.
  Łańcuch jest częścią każdego zapisanego adresu kontaktu i każdego zatwierdzenia. Przelewy tokenów
  wymagają też rodzimej monety danego łańcucha na opłaty; wycena pokazuje obie kwoty w ramach twoich limitów.
* **USDC w Solanie i Base jest dostępne tylko dla kanałów płatności.** Nie jest oferowane
  przy zwykłych wysyłkach. Główny widok salda portfela pokazuje rodzime monety, a nie pełny
  zestaw tokenów.
* Każdy przelew jest najpierw pokazywany w podglądzie (**wycena**: kwota, pułap opłaty, odbiorca, sieć) i wymaga
  twojego wyraźnego zatwierdzenia, powiązanego dokładnie z tym podglądem. Zmień cokolwiek, a zatwierdzenie
  traci ważność — nic nie zostaje podpisane.
* Nazwa pasująca do kilku kontaktów lub adresów portfeli wymaga wybrania jednego z nich. Sprawdź dokładny
  adres i łańcuch. Zmieniony adres lub kontakt o zmienionej nazwie unieważnia wcześniejszy przegląd.
* W mainnecie wysyłać może tylko portfel VOOL utworzony na tym urządzeniu. Podłączony portfel zewnętrzny
  lub adres tylko do podglądu może odbierać środki i pokazywać saldo; wysyłka albo płatność przez kanał płatności
  z takiego portfela jest odrzucana, zanim padnie prośba o jakiekolwiek zatwierdzenie, i nic nie zostaje podpisane.
* Aktywność rejestruje płatności dokonane przez VOOL i ich potwierdzenia. Nie jest to pełna
  historia płatności przychodzących. Ten portfel nie oferuje operacji swap ani bridge.

## Jak zatwierdzana jest płatność

1. VOOL przygotowuje propozycję z dokładnymi kwotami i pułapem opłaty.
2. Zatwierdzasz ją swoim PIN-em lub hasłem (albo potwierdzeniem obecności na urządzeniu, tam gdzie jest dostępne).
3. Płatność zostaje podpisana i wysłana, a potwierdzenie zostaje zapisane.

Zatwierdzenia nie przechodzą między propozycjami. Błędny PIN niczego nie podpisuje. Zbyt wiele błędnych
prób blokuje portfel na pewien czas — celowo, z podanym czasem oczekiwania.

## Automatyczne płatności x402

x402 to protokół płatności za pojedyncze żądanie, którym posługują się niektóre strony internetowe i usługi AI.
**Strony internetowe, które żądają płatności x402 w USDC w sieci Base** (lub Base Sepolia, w sekcji Developer
options), mogą być opłacane z twojego portfela Crypto Pilot: jednym zatwierdzeniem PIN-em, w ramach twoich
limitów. Usługa płatności strony pokrywa opłatę sieciową. **Żadna płatność na żywo w mainnecie nie została
jeszcze w ten sposób wykonana.** Pozostałe sieci testowe (Solana Devnet, Ethereum Sepolia i BNB testnet)
pozostają w sekcji Developer options. **Ustawienia → Krypto → Payment channels** wymienia każdy sposób, w jaki
portfel może zapłacić stronie, każdy z własnym przełącznikiem wł./wył.; wyłączony kanał odmawia, zanim cokolwiek
zostanie podpisane. Płatności przez Solana pay-kit nie ma w plikach do pobrania, więc ten kanał jest pokazywany
jako niedostępny. **Marketplace modeli:** opcja płatności za wywołanie w marketplace modeli w Ustawieniach może płacić z portfela Crypto Pilot w Solana Mainnet
w USDC lub SOL. Każde wywołanie czeka na twoje zatwierdzenie w ramach limitów portfela
i pokazuje opłatę VOOL w wysokości 0,1%. Ta ścieżka nie była jeszcze testowana na prawdziwym łańcuchu.
Gdy płatny zasób używa x402:

* Płatności przechodzą przez te same limity portfela i ten sam proces zatwierdzania.
* Płatności automatyczne mają limit na jedną płatność; zasób powyżej limitu **nie zostaje opłacony** (możesz
  świadomie podnieść limit albo zapłacić przez wyraźną propozycję).
* Jeśli płatności nie udało się dokończyć, wynik jest zgłaszany jako **nieznany** — przed ponowną próbą
  sprawdź jej status; VOOL nie zachęci do zduplikowanej płatności na ślepo.

## Opłata serwisowa DNA

Ścieżka x402 z płatnością za wywołanie w marketplace modeli pobiera opłatę serwisową **0,1% (10 punktów bazowych)** od każdej zaakceptowanej
płatności na rzecz dostawcy, pokazywaną osobno od płatności i osobno od opłaty sieciowej. Nic innego
jej nie nalicza: zwykłe wysyłki, tworzenie portfela, lokalne wnioskowanie i wywołania z twoim własnym kluczem
API (BYOK) **nie** podlegają żadnej opłacie serwisowej. Naliczone opłaty są pobierane w ekonomiczny sposób
(przy późniejszej rodzimej płatności, w ramach jednego zatwierdzenia, tylko gdy koszt pobrania jest ograniczony)
— nigdy w postaci niespodziewanego osobnego obciążenia.

## Odzyskiwanie

* **Fraza odzyskiwania jest pokazywana dokładnie raz**, przy tworzeniu.
* Klucz kopii zapasowej portfela pilotażowego również jest ujawniany jeden raz podczas konfiguracji i nie ma
  późniejszego eksportu. Jeśli nie został zapisany, anuluj konfigurację i nie wysyłaj środków na ten adres.
* Starszy portfel VOOL, utworzony przed Crypto Pilot, może jeden raz wyeksportować swój klucz prywatny w celu
  importu do innej aplikacji portfela. Eksport otwiera się tylko przez logowanie na twoim Macu (Touch ID lub hasło
  do Maca), nigdy przez PIN portfela, a klucz jest pokazywany raz i nie jest przechowywany.
* Utracony portfel? Odzyskaj go z frazy zapisanej przy tworzeniu. Dowód odzyskiwania,
  który nie pasuje do dokładnie tego portfela, jest odrzucany przed jakimkolwiek zapisem.
