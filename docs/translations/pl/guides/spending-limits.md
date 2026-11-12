---
description: Limity wydatków, zabezpieczenia cenowe i to, co się dzieje, gdy płatne wywołanie zostanie przerwane.
translation: machine
source_sha256: a4a5246059942702330cbfc18e9872dc5b0f46192fa5b852bfc9060ca987eae6
---

# Limity wydatków i zabezpieczenia cenowe

VOOL nigdy nie wydaje pieniędzy w twoim imieniu bez wyraźnego pułapu, nad którym masz kontrolę. Ta strona
opisuje warstwy chroniące płatne wywołania modeli i płatne zasoby oraz to, co się dzieje, gdy
płatne wywołanie zostanie przerwane w trakcie.

## Dwie ceny, jedna zasada

* **Cena katalogowa** — cena, którą dostawca obecnie podaje dla modelu. VOOL ją wykrywa; jest
  informacją, a nie pozwoleniem.
* **Twoja zaakceptowana cena** — pułap zatwierdzony przez ciebie dla tego dostawcy i modelu w Ustawieniach.
  Wykryta cena powyżej twojego pułapu jest odrzucana **przed** kontaktem z dostawcą. Odmowa
  mówi dokładnie to: model nie został uruchomiony, nic nie zostało wysłane, nie pobrano żadnej opłaty.

## Limity wydatków

W przypadku płatnych wywołań VOOL przed każdym wywołaniem rezerwuje środki w ramach limitów:

| Limit | Znaczenie |
| --- | --- |
| Na wywołanie | Pułap przewidywanego kosztu pojedynczego wywołania. |
| Na zadanie | Najwyższa kwota, jaką jedno zadanie może wydać na wszystkie swoje wywołania. |
| Dzienny / miesięczny | Pułapy płatnego użycia dla całego okresu. |

Wywołanie, które przekroczyłoby limit, jest odrzucane przed wysłaniem. Odmowa wskazuje limit, który ją
spowodował (`per_task_spend_cap_exceeded` i pokrewne kody), a jej sformułowanie celowo dotyczy chwili
przed wysłaniem: *„running it would exceed your spend cap”* — nigdy nie twierdzi, że nastąpił kontakt z dostawcą.

## Przypięty model nigdy nie jest po cichu zastępowany

Jeśli wyraźnie wybierzesz płatny model, a zabezpieczenie go odrzuci, VOOL **nie** odpowie po cichu
tańszym modelem. Tura kończy się niepowodzeniem z podaniem wybranego przez ciebie modelu i faktycznej blokady
oraz ze zdaniem *„no other model was substituted.”* Sam wybierz inny model albo świadomie podnieś
limit.

## Zapłacone, ale odpowiedź nie dotarła

Jeśli za płatne wywołanie pobrano opłatę, ale jego odpowiedź nie dotarła, wynik jest **nieznany** — VOOL
nie będzie udawać, że wywołanie się nie powiodło ani że się powiodło:

* W jego miejsce nie jest wymyślana żadna odpowiedź i nie zakłada się zwrotu pieniędzy.
* Płatność pozostaje w zapisach jako nierozstrzygnięta.
* Ustawienia wymieniają ją w sekcji **Paid calls waiting for an answer**, gdzie **Resume** prosi o
  tę samą odpowiedź jeszcze raz, **bez ponownego płacenia**.

Dlatego w takich przypadkach nigdy nie sugeruje się ponawiania na ślepo: zwykłe ponowne wysłanie mogłoby zapłacić
dwa razy. Resume to jedyna obsługiwana droga.

## Automatyczne płatności z portfela (x402)

Strony internetowe, które żądają płatności x402 w USDC w sieci Base lub Base Sepolia, mogą być opłacane z twojego
portfela Crypto Pilot, jednym zatwierdzeniem PIN-em w ramach tych limitów; żadna płatność na żywo w mainnecie
nie została jeszcze w ten sposób wykonana. Opcja płatności za wywołanie w marketplace modeli w Ustawieniach płaci
z portfela w Solana Mainnet, jednym zatwierdzeniem na wywołanie w ramach tych limitów, i nie była jeszcze
testowana na prawdziwym łańcuchu.
Zasoby opłacane z portfela (zobacz [Portfel](../guides/wallet.md)) mają własny limit automatycznych płatności.
Zasób, który żąda więcej niż ten limit, po prostu nie zostaje opłacony; możesz świadomie podnieść limit
albo zapłacić za zasób przez wyraźną propozycję z twoim zatwierdzeniem.
