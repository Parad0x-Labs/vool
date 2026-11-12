---
description: Pokalbiai, privatūs pokalbiai ir failai bei ilgi įklijuoti tekstai, kuriuos į juos įkeliate: ribos, kas išsaugoma ir kas gali būti atmesta.
translation: machine
source_sha256: 1497140fef69088f8e0210aeb8a7f6abba6ae82d5132841ce397fe60fa1bab99
---

# Pokalbiai ir priedai

Pokalbis — tai vienas pasikalbėjimas su VOOL. Pokalbiai saugomi jūsų kompiuteryje. Pokalbį galima susieti
su projekto aplanku, kad jo įrankiai veiktų tame aplanke (žr. [Darbo sritys](workspaces.md)), o failus
į jį galite įkelti kaip priedus.

## Failų prisegimas

Naudokite mygtuką **Prisegti** šalia žinutės laukelio arba nuvilkite failus ant jo. Kiekvienas failas rodomas
kaip kortelė ir išsiunčiamas su kita jūsų siunčiama žinute. Eilutė po laukeliu rodo ribas:

* Iki 8 failų vienoje žinutėje, 10 MB vienam failui ir 25 MB vienai žinutei.
* Teksto ir kodo failai bei PNG, JPEG, GIF ir WebP vaizdai. PDF, Word (DOCX), archyvai, pavyzdžiui,
  ZIP, ir vaizdo įrašai prie jų prisideda, kai šiame kompiuteryje yra tai, ko reikia jiems perskaityti.
  Failų parinkiklis siūlo tik tai, ką šis kompiuteris gali perskaityti.

Ribota ir tai, ką gauna modelis: iki 120 000 simbolių iš vieno failo ir 240 000 iš visų žinutės
failų. Ilgesnis failas nukerpamas eilutės pabaigoje, o įraše nurodoma, kurioje vietoje. Jei
naudojamas modelis negali skaityti vaizdų, kortelėje tai parašoma, ir vaizdas jam nesiunčiamas.

Prieš išsaugant vaizdą, iš jo pašalinama paslėpta informacija: fotoaparato ir vietos duomenys (EXIF,
XMP, IPTC) bei tekstas, laikomas PNG ir WebP failuose.

Pasirinktas failas naudojamas tik tai vienai žinutei. Kai atsakymas paruoštas, failo turinys ištrinamas,
o jo kvitas išsaugomas.

## Ilgi įklijuoti tekstai

Įklijuokite 4 000 ar daugiau simbolių, ir VOOL tai išsaugos kaip pokalbio dokumentą, o ne kaip žinutę,
tiksliai taip, kaip įklijuota. **Peržiūra** rodo jo pradžią. Dokumentas lieka pokalbyje, kol ištrinate
jį arba pokalbį. Vėlesni klausimai tame pačiame pokalbyje gali jį naudoti: VOOL perduoda modeliui iki
60 000 simbolių iš ankstesnių pokalbio dokumentų, pradedant naujausiais, ir nurodo, kur nukirpo.

## Kai failas atmetamas

Atmestas failas niekada nedingsta be žinios. Jo kortelė lieka ir nurodo priežastį, o žinutės
negalima išsiųsti, kol nepašalinate failo arba nebandote jo dar kartą: *Prieš siųsdami pašalinkite
nesėkmingą priedą arba bandykite dar kartą.* Dažniausios priežastys:

* **Per didelis failas arba per daug failų.** Pašalinkite dalį arba išsiųskite juos dviem žinutėmis.
* **Ne tas tipas, kurį VOOL skaito, arba ne tai, ką rodo pavadinimas.** Failas, pavadintas `.txt`, kuris
  iš tikrųjų yra vaizdas ar programa, arba PDF, kuriam šiame kompiuteryje nėra skaitytuvo, atmetamas
  nurodant priežastį.
* **Viduje yra prisijungimo duomenys.** Failas ar įklijuotas tekstas, kuriame, atrodo, yra veikiantis raktas
  ar žetonas, pavyzdžiui, privataus rakto blokas ar prieigos žetonas, atmetamas, ir niekas neišsaugoma.
  Pašalinkite slaptą informaciją, tada prisekite arba įklijuokite dar kartą.

Šie atmetimai turi savo trumpus kodus, rodomus kartu su pranešimu. Jų nėra
[Klaidų knygoje](../ERROR_BOOK.md), kurioje aprašytos kitos programos klaidos.

## Privatūs pokalbiai

Pasakykite „privatus pokalbis“, „neišsaugokite to“ arba „tik tarp mūsų“, ir pokalbis nuo tos žinutės
nustoja saugoti: nei žinučių, nei santraukos, nei atminties. Pranešimo juostoje rodoma *Privatus pokalbis:
niekas neišsaugoma*, o prie kiekvienos privačios žinutės matomas mažas vaiduoklis. Perkrovus puslapį,
privačios žinutės nebegrįžta, ir VOOL vėliau negali jų prisiminti. Pasakykite „vėl įjungti įrašymą“
arba paspauskite **Vėl įjungti įrašymą**, kad pokalbis vėl būtų saugomas nuo kitos žinutės. Privatus
pokalbis lieka privatus ir iš naujo paleidus VOOL.

## Pokalbio pasiėmimas su savimi

**Eksportuoti** pokalbio antraštėje išsaugo visą pokalbį kaip tekstą arba Markdown, su kiekviena žinute,
o prisegtus failus išvardija tik pavadinimais, niekada su turiniu. Norėdami perkelti pokalbį į kitą
kompiuterį arba išsaugoti pasirašytą kopiją, žr. [Atsarginės kopijos ir atkūrimas](../help/backup-and-recovery.md).
