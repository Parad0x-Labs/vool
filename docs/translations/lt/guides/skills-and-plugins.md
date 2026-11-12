---
description: VOOL išplėtimas įgūdžiais ir papildiniais.
translation: machine
source_sha256: a86860b8624e3c97c81e813ec92e3998c687a87d7bc43b7960c489cdcd309196
---

# Įgūdžiai ir papildiniai

**Įgūdis** yra supakuotas instrukcijų rinkinys pasikartojančiai užduočiai. **Papildinys** prideda naujų
įrankių į vykdymo aplinką.

## Įtaisyti ir įdiegti iš išorės

VOOL pateikiamas su **savąja įgūdžių biblioteka** — įgūdžiais, kurie ateina kartu su pačia programa, — ir,
dabartinėse versijose, papildiniu **VOOL Database**. Taip pat galite įdiegti savo įgūdžių ir papildinių.

| | Įtaisyti (pateikiami su VOOL) | Įdiegti iš išorės |
| --- | --- | --- |
| Kur jie yra | Programos viduje | Jūsų papildinių aplankas (nustatomas Nustatymuose) |
| Atnaujinimai | Kartu su programa | Kai atnaujinate paketą |
| Galima išjungti | Taip — kiekvieną atskirai | Taip — kiekvieną atskirai |
| Suteikia leidimus | **Ne** | **Ne** |

Abiem šaltiniams galioja dvi taisyklės:

* **Įgūdžiai moko; jie niekada nieko nesuteikia.** Įgūdžio deklaruoti įrankiai yra *siaurinantis* signalas
  (darbo eigos įrankių biudžetas), niekada ne kokio nors leidimo suteikimas. Leidimų valdiklis neskaito
  įgūdžių.
* **Vienas sąrašas.** Konsolėje rodomas katalogas ir vykdymo aplinkos įkeliami paketai yra tas pats
  sąrašas. Tai, kad įgūdis yra sąraše, neįrodo, kad jį galima įkelti — kiekvienas įrašas turi savo
  dabartinę būseną (prieinamas, išjungtas, trūksta būtinos sąlygos, netinkamas kontraktas) ir priežastį.

## Įgūdžių paieška

Skiltis Įgūdžiai ir papildiniai pirmiausia rodo paties VOOL įgūdžius. **Naršyti** pateikia 53 paruoštus
VOOL įgūdžius. Jie yra tik tekstiniai: kiekvienas iš jų yra instrukcijų rinkinys, pridėtas prie programos,
todėl Naršyti juos parodo nieko neieškodamas internete. VOOL įgūdžiai, kurie naudoja VOOL įrankius,
išvardyti kortelėje **Įgūdžių kodeksas**, kur pirmiausia rodomi VOOL komandos įgūdžiai. Peržiūrėkite įgūdį
prieš jį įjungdami.

## Prieinamumo būsenos

Įgūdis gali būti rodomas kaip neprieinamas, nurodant tipizuotą priežastį: trūksta būtinos sąlygos
(pavyzdžiui, duomenų bazės įgūdžiui reikia įjungto duomenų bazės papildinio), šiame kompiuteryje nėra
reikiamos galimybės, netinkamas kontraktas arba jį išjungėte jūs. Priežastys rodomos prie kiekvieno įrašo,
o išjungimas taikomas kiekvienam įrašui atskirai ir išlieka paleidus iš naujo.

## Pasikartojančios tapatybės

Jei du paketai deklaruoja tą pačią papildinio tapatybę (pavyzdžiui, įtaisytas paketas ir įdiegta kopija),
**deterministiškai** išlaikomas įtaisytasis, o atmesta kopija nurodoma kataloge — ji niekada nėra tyliai
užgožiama.

## Savų įgūdžių ir papildinių diegimas

Diekite iš programos katalogo arba nukreipkite VOOL į savo papildinių aplanką. Paketuose yra manifestas
(`plugin.json`), deklaruojantis jų įrankius; manifestas, kurio leidimų deklaracijos neatitinka deklaruotos
elgsenos klasės, įkeliant atmetamas.

{% hint style="danger" %}
Prieš patikrą peržiūrėkite visą paketą. Peržiūra yra įrodymas apie konkrečius baitus,
o ne saugumo sertifikatas ir ne leidimų suteikimas.
{% endhint %}

## Paketų patvirtinimas v0.7

Patikra apima kiekvieną paketo failą ir katalogą, įskaitant doroklių vykdomuosius failus, pridėtas
bibliotekas, priklausomybių užrakto failus, nuorodų failus, išteklius ir vykdymo režimo bitus.
Pridėjus, pašalinus, pervadinus ar pakeitus bet kurį iš jų, patvirtinimas nebegalioja. Simbolinės nuorodos,
kietosios nuorodos, specialieji failai, privilegijuoti režimai, nestabilūs skaitymai ir per didelės
distribucijos atmetami. Dabartinės užfiksavimo ribos yra 4 096 įrašai, 64 MiB vienam failui ir 128 MiB
vienam paketui. Paketo viduje nėra ignoruojamų podėlio ar būsenos katalogų.

Anksčiau išsaugoti tik metaduomenų patvirtinimai šioje versijoje paketų neaktyvuoja. Peržiūrėkite visą
distribuciją, dar kartą ją aiškiai patikrinkite, tada įjunkite. Atnaujinimai lieka išjungti, kol nėra
patikrinti ir įjungti; patvirtinimas niekada tyliai neperkeliamas. Valdomiems priedams taip pat reikia
naujos autentifikuotos parduotuvės peržiūros, kai senesnis kvitas neapima visos distribucijos; įprasta
patikra to kvito pakeisti negali.

`requires` nurodo įdiegtus papildinius, nuo kurių paketas priklauso. Patikra prisega jų rekursines
patvirtinimų maišas; kiekviena priklausomybė jau turi būti patikrinta ir įjungta. Pakeista, atnaujinta,
išjungta ar atšaukta priklausomybė padaro nuo jos priklausančius paketus neprieinamus, taip pat ir per
podėlyje laikomus įrankius. Įleidimas po maišų apskaičiavimo dar kartą patikrina viso užfiksuoto uždarinio
įjungimo ir atšaukimo būseną. Pakartotinė priklausomybės patikra tyliai nepatvirtina jos naujų baitų
tėviniam paketui: peržiūrėkite ir iš naujo patikrinkite ir kiekvieną tėvinį paketą. Kiekvienas
`approval_digest` susieja savo paketo maišą ir priklausomybių patvirtinimus, todėl pasikeitusi tranzityvinė
priklausomybė pakeičia tą patvirtinimą net tada, kai paties tėvinio paketo failai lieka identiški. Ciklinis
priklausomybių patvirtinimas negali būti aktyvuotas. Priklausomybių uždarinys apribotas iki 64 paketų.

Kiekvienas užregistruotas iškvietimas dar kartą patikrina gyvavimo ciklo būseną ir užfiksuoja privačias,
tik skaitymui skirtas paketo ir jo prisegto priklausomybių uždarinio kopijas. Įrankio kontraktas turi
atitikti užfiksuotą manifestą. Užregistruotas doroklis paleidžiamas iš tos kopijos; šaltinio redagavimas
po užfiksavimo negali pakeisti to doroklio ar jo pridėtų failų. Vykdymo stebėjimuose yra paketo,
rekursinio patvirtinimo ir priklausomybių maišos. Užregistruotiems paketams reikia tikros branduolio lygio
izoliacijos, net jei vietinis žemo lygio kvietėjas pasirinko silpnesnį nepaisymą.
Kompiuteriai be saugaus deskriptorių užfiksavimo nesekant nuorodų arba be branduolio lygio izoliacijos
atmeta užregistruotus paketus. Iškvietimų laukimo laikas apribotas 1–300 sekundžių; iškvietimui
priklausanti procesų grupė sustabdoma prieš pašalinant jo vykdymo kopiją.

Nuolatinę išvestį, podėlius ir žurnalus laikykite `PLUGIN_SCRATCH`, o ne šalia vykdomojo kodo.
Tik skaitymo įrankiai negauna rašymo teisės. Kiti įrankiai gali rašyti į laikinųjų duomenų katalogą, bet
negali keisti savo vykdymo kopijos. `VOOL_PLUGIN_DEPENDENCIES` yra JSON atvaizdis iš deklaruotų
priklausomybių tapatybių į užfiksuotus priklausomybių katalogus. Kitas trečiųjų šalių bibliotekas ir jų
užrakto failus pridėkite paketo viduje; vien užrakto failas nepatvirtina išorinės aplinkos.
Pagrindinio kompiuterio interpretatorius ir sistemos bibliotekos lieka patikimos platformos dalimi ir
nepateikiami kaip peržiūrėti trečiųjų šalių paketų baitai. Ši vientisumo patikra nesertifikuoja elgsenos,
nepatvirtina bet kokio kodo, atsisiųsto ar įkelto už patvirtintos distribucijos ribų, netarpininkauja
kiekvienam vidiniam poveikiui ir nepakeičia įtaisytojo piniginės patvirtinimo.

## Įgūdžio rašymas

Įgūdis yra aplankas su instrukcijų failu ir visais failais, į kuriuos jis nurodo:

```text
my-skill/
├── SKILL.md
└── templates/
```

Juodraštį parenkite programoje (`skill.create` jį paruošia nieko neaktyvuodamas), patikrinkite jo
tinkamumą, tada įdiekite, kad jį aktyvuotumėte — tai sąmoningai trys atskiri žingsniai, kad kūrimas
niekada nebūtų numanomas elgsenos pakeitimas. Katalogas ir kūrimo formatas dar baigiami rengti prieš
viešą išleidimą.
