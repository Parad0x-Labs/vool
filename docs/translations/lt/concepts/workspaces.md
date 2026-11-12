---
description: Darbo sritis yra projekto aplankas, kuriame dirba VOOL. Už jo ribų VOOL gali skaityti matomus jūsų namų aplankus, o failus keisti tik aplankuose Desktop, Downloads ir Documents.
translation: machine
source_sha256: 33b648a0a55f99b1c96365553942911862672eafa55e94bd45f18ce566d5fcea
---

# Darbo sritys

Darbo sritis yra projekto aplankas, kuriame dirba pokalbis. VOOL ten skaito ir keičia failus,
prieš tai paklausdamas visada, kai to reikalauja jūsų veikimo režimas (žr. [Įrankiai](tools.md)).

## Pridėjimas ir šalinimas

Pasirinkite aplanką pokalbyje mygtuku **Pasirinkite projekto aplanką…** arba pokalbių šoninėje juostoje
mygtuku **+ Projektas**. Ištrynus projektą, VOOL pamiršta, ko jame išmoko, o jo pokalbius perkelia
atgal į skiltį Bendra. Jūsų failai nepaliečiami.

## Kiti jūsų aplankai

Už darbo srities ribų VOOL failų įrankiai taip pat gali pasiekti jūsų namų aplanką, laikydamiesi nustatytų apribojimų:

| Aplankai | Ką gali VOOL |
| --- | --- |
| Desktop, Downloads, Documents | Skaityti ir keisti failus, prieš tai paklausdamas taip, kaip nurodo jūsų veikimo režimas |
| Pictures, Movies (Linux sistemoje Videos), Music | Tik skaityti: išvardyti, rasti, atidaryti ir matuoti, niekada nekeisti |
| Kiti matomi jūsų namų aplankai, pavyzdžiui, `~/Projects` | Tik skaityti |
| Paslėpti aplankai (pavyzdžiui, `~/.ssh`), programų ir sistemos duomenys (`Library`, `Applications`, `AppData`), paties VOOL duomenų aplankas | Nieko |

VOOL į šiuos aplankus žiūri tik tada, kai to reikia užklausai, pavyzdžiui, „rask mano mokesčių PDF“
arba „kuris vaizdo įrašas aplanke Movies didžiausias“. Jis neindeksuoja jūsų disko fone.

## Patarimai dėl apimties

* Darbo sritį nukreipkite į projektą, o ne į savo namų katalogą.
* Prisijungimo duomenų failus, raktų medžiagą ir slaptažodžių saugyklas laikykite slaptažodžių tvarkyklėje
  arba paslėptame aplanke, o ne išmėtytus aplankuose Desktop, Downloads, Documents ar projekto aplanke.
* Kiekvienam darbui naudokite atskirą projektą, kad vieno darbo kontekstas nepatektų į kitą.

{% hint style="warning" %}
Viskas, ką VOOL perskaito projekto aplanke ar jūsų namų aplankuose, gali patekti į tai, kas
siunčiama jūsų tiekėjui, kai pasirinktas debesijos modelis. Apimtį nustatykite apgalvotai.
{% endhint %}
