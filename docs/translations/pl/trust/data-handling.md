---
description: Jakie dokładnie dane wysyła VOOL, kiedy i do kogo. Co zostaje na twoim komputerze, a co trafia do twojego dostawcy AI.
translation: machine
source_sha256: 4fd0aaa4d2480ae80b94aa7132c46c8263113450e4b02c5747388791e7bc8710
---

# Przetwarzanie danych

## Dwa stany

**Lokalnie** — model i twoje pliki zostają na tym komputerze. Bieżące informacje, takie jak pogoda czy ceny, pochodzą z publicznych usług internetowych tylko wtedy, gdy włączone jest „Wyszukiwanie w sieci” (po świeżej instalacji jest wyłączone), i nigdy w trybie „Tylko lokalnie”.

**Chmura** — wybrany kontekst jest wysyłany bezpośrednio do wybranego dostawcy.

Nagłówek czatu pokazuje, w który sposób zostanie wykonane następne żądanie — na przykład **AUTO** z
**Najpierw lokalnie**, gdy VOOL Auto odpowiada z tego komputera.

## Czego VOOL nie robi

* Nie przepuszcza twoich promptów przez infrastrukturę Parad0x Labs.
* Nie wysyła twojego obszaru roboczego do sieci.
* Nie wysyła twojego klucza nigdzie poza dostawcą, do którego należy.

## OpenRouter w szczególności

Twoje konto OpenRouter pozostaje twoje. VOOL wysyła żądania bezpośrednio z tego komputera
z użyciem twojego klucza. Parad0x nie pośredniczy w przesyłaniu twoich promptów ani nie płaci za twoje użycie. Rzeczywiste
użycie VOOL może pojawiać się w publicznych, zbiorczych statystykach VOOL w OpenRouter, ale pojedyncze prompty
i tożsamości nie są publikowane.

## Telemetria

VOOL nie wysyła telemetrii produktu. Raport o błędzie opuszcza twój komputer tylko wtedy, gdy go przejrzysz
i zdecydujesz się go wysłać; zobacz [Zgłaszanie błędów i prywatność](bug-reporting.md).

## Obowiązują warunki twojego dostawcy

W trybie chmury twoje dane trafiają do dostawcy na **jego** warunkach przechowywania i
trenowania, a nie na warunkach VOOL. Przeczytaj warunki dostawcy, którego wybierasz.
