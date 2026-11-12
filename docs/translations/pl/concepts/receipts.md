---
description: Potwierdzenie to zapis tego, co przebieg faktycznie zrobił.
translation: machine
source_sha256: caffc8be79cb1017a81f74baa2451e076db2e6660302b57db4b9d3ec92dab8b8
---

# Potwierdzenia

Potwierdzenie to trwały zapis przebiegu: jakie narzędzia zostały wywołane, z jakimi danymi wejściowymi
i co każde z nich zwróciło.

## Po co istnieją

Podsumowanie własnej pracy sporządzone przez agenta to tylko twierdzenie. Potwierdzenie to zapis, z którym
możesz to twierdzenie porównać.

## Co zawiera

| Pole | Znaczenie |
| --- | --- |
| `tool` | Która czynność została wykonana |
| `input` | Co otrzymała |
| `result` | Co zwróciła |
| `time` | Kiedy została wykonana |

Potwierdzenia są przechowywane w folderze „runtime home” VOOL na tym komputerze (zobacz
[Konfiguracja](../reference/configuration.md)), a nie w folderach twoich projektów. Potwierdzenie opuszcza
twój komputer tylko wewnątrz raportu o błędzie, który przejrzysz w podglądzie i zdecydujesz się wysłać (zobacz
[Zgłaszanie błędów i prywatność](../trust/bug-reporting.md)).
