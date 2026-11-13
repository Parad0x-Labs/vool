---
description: VOOL išplėtimas įgūdžiais ir papildiniais.
translation: machine
source_sha256: 4dd2a317a7b10ee024616f6e0c27bd73087c44334f29e2c5f60f69773f4e54e8
---

# Įgūdžiai ir papildiniai

**Įgūdis** yra supakuotas instrukcijų rinkinys pasikartojančiai užduočiai. **Papildinys** prideda įrankių,
modelių tiekėją arba priedų skenerį. Papildiniuose taip pat gali būti įgūdžių.

## Įtaisyti ir įdiegti iš išorės

VOOL pateikiamas su **savąja įgūdžių biblioteka** — įgūdžiais, kurie ateina kartu su pačia programa, — ir,
dabartinėse versijose, papildiniu **VOOL Database**. Taip pat galite įdiegti savo įgūdžių ir papildinių.

| | Įtaisyti (pateikiami su VOOL) | Įdiegti iš išorės |
| --- | --- | --- |
| Kur jie yra | Programos viduje | Jūsų papildinių aplankas (valdomas iš papildinių lango VOOL, o ne iš Nustatymų) |
| Atnaujinimai | Kartu su programa | Kai atnaujinate paketą |
| Galima išjungti | Programos languose tokio mygtuko kol kas nėra | Taip — iš papildinių lango |
| Suteikia leidimus | **Ne** | **Ne** |

Abiem šaltiniams galioja dvi taisyklės:

* **Įgūdžiai moko; jie niekada nieko nesuteikia.** Įgūdžio deklaruoti įrankiai yra *siaurinantis* signalas
  (darbo eigos įrankių biudžetas), niekada ne kokio nors leidimo suteikimas. Leidimų valdiklis neskaito
  įgūdžių.
* **Vienas sąrašas.** Konsolėje rodomas katalogas ir vykdymo aplinkos įkeliami paketai yra tas pats
  sąrašas. Tai, kad įgūdis yra sąraše, neįrodo, kad jį galima įkelti — kiekvienas įrašas turi savo
  dabartinę būseną (prieinamas, išjungtas, trūksta būtinos sąlygos, netinkamas kontraktas) ir priežastį.

## Įgūdžių paieška

Skiltis Įgūdžiai ir papildiniai pirmiausia rodo paties VOOL įgūdžius. **Naršyti** pateikia 75 paruoštus
VOOL įgūdžius. Kiekvienas iš jų yra instrukcijų rinkinys, pridėtas prie programos, todėl Naršyti juos parodo
nieko neieškodamas internete. 22 iš jų naudoja VOOL įrankius (jūsų el. paštą, užrašus, failus, PDF, piniginę ir
kt.) tuose ėjimuose, kuriuose yra parenkami: įgūdžio įrankiai tik susiaurina tai, ką VOOL jau siūlo jūsų
užklausai, išjungtas įrankis lieka išjungtas, o kiekvienas iškvietimas klausia kaip visada. Jo
**Šaltinis ir techninė informacija** išvardija naudojamus įrankius. Kortelė **Įgūdžių kodeksas** pirmiausia rodo VOOL komandos
įgūdžius. Peržiūrėkite įgūdį prieš jį įjungdami.

Kaip įgūdis pridedamas iš Naršyti, priklauso nuo to, kas jį sukūrė:

* **VOOL sukurti įgūdžiai** (minėti 75): paspauskite **Pridėti šį įgūdį**. VOOL jūsų
  kompiuteryje patikrina, ar failas yra būtent tas, su kuriuo buvo pateiktas, ir perskaito jį ieškodamas rizikingų
  instrukcijų, tada parodo jums rezultatą. Rakto nereikia ir niekas niekur nesiunčiama.
  Jei failas nesutampa, VOOL jį atmeta ir apie tai praneša.
* **Kitų kūrėjų įgūdžiai** (kitų leidėjų arba iš jūsų įklijuotos GitHub nuorodos): paspauskite
  **Atsisiųsti ir nuskaityti**. VOOL atsisiunčia viešą įgūdį fiksuotos versijos ir patikrina jį jūsų
  kompiuteryje. Jei įjungėte skenerio papildinį, jis taip pat gauna įgūdžio turinį, o
  jo ataskaita pridedama šalia paties VOOL ataskaitos. Perskaitykite rezultatą, tada nuspręskite, ar pridėti
  įgūdį: jei bet kuri patikra praneša apie problemą, VOOL jį prideda tik jums priėmus tos
  versijos rizikas. Rakto nereikia, nebent jo prašo jūsų skenerio papildinys.

Savo įgūdį taip pat galite susikurti pokalbyje visai be rakto: žr.
[Susikurkite savo įgūdį](../../../guides/make-your-own-skill.md).

Taip pat galite paklausti bet kuriame pokalbyje: *kokius įgūdžius turiu?* juos išvardija, o *kaip pridėti
įgūdį?* paaiškina abu būdus.

## Įgūdžio įjungimas ir išjungimas

Atidarykite **Įgūdžiai ir papildiniai**, tada **Mano priedai**, tada **Įgūdžiai**. Prie kiekvieno įgūdžio rodoma **Įjungta** arba
**Išjungta** ir mygtukas **Įjungti** arba **Išjungti**. Išjungtas įgūdis nenaudojamas jokiame pokalbyje,
kol vėl jį įjungsite. Jo failai išsaugomi.

Kortelė **Built-in skills** skiltyje **Papildiniai** neturi savo mygtuko: jos įgūdžius įjunkite arba
išjunkite po vieną skiltyje **Įgūdžiai**.

## Kuris įgūdis paveikė atsakymą

Kai įgūdžio instrukcijos buvo dalis to, kaip VOOL parašė atsakymą, tai nurodo trumpa eilutė po
atsakymu, pavyzdžiui, **Panaudotas įgūdis: Browser**. Ji pasirodo iškart po atsakymo, kol
pokalbis atidarytas; vėliau iš istorijos įkeltuose atsakymuose jos kol kas nėra. Atsakymų stiliaus taisyklės,
lydinčios daugumą atsakymų, ten neįvardijamos.

## Įgūdžiai VOOL kūrimui

Bibliotekoje taip pat yra įgūdžiai, su kuriais VOOL komanda kuria VOOL: kodo peržiūra, testų paleidimas,
išleidimo patikros, konvejerio taisymas ir panašiai. Jie nerodomi pokalbyje ar įgūdžių puslapyje
ir nevaldo kasdienių atsakymų. Kūrėjai gali juos parodyti ir naudoti, paleisdami VOOL su
`VOOL_DEVELOPER_SKILLS=1`.

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

## Papildiniai Prieduose

Atidarykite **Priedai → Mano priedai → Papildiniai**. Peržiūrėkite vietinį aplanką arba viešą GitHub aplanką
tiksliame commit. Diegimas užfiksuoja visą paketą ir palieka jį išjungtą. Pasirašyti
parduotuvės papildiniai naudoja esamą registro grandinę ir taip pat įdiegiami išjungti. Atskirai peržiūrėkite prieigos
lapą prieš įjungdami įrankius ar paslaugas. Paslaugos rakto išsaugojimas nesiunčia jokios užklausos;
ryšio testai ir modelių sąrašo atnaujinimai yra aiškūs veiksmai.

Išjungimas pašalina įrankius ir sustabdo vykdomus iškvietimus. Pašalinimas pirmiausia išjungia, pašalina nuosavus failus
ir laikinąjį aplanką, pagal numatytąją nuostatą ištrina išsaugotus raktus ir išsaugo istoriją. Atnaujinimams ir pakartotiniams diegimams
reikia naujos peržiūros, ir jie pradedami išjungti. Atnaujinimas iš kitos saugyklos ar nuo kito leidėjo
neišsaugo išsaugotų raktų. UsePod ir Eyebrow nebėra VOOL dalis, ir VOOL
nesiūlo papildinio nė vienam iš jų. Išsaugotas UsePod ar Eyebrow raktas lieka šiame kompiuteryje, ir VOOL
jo nenaudoja; Priedai apie tai praneša, kol uždarysite pranešimą. Pokalbiuose, kuriuose buvo naudojamas UsePod modelis,
po atnaujinimo nėra pasirinkto modelio, ir Auto tuose pokalbiuose neperjungs į debesijos modelį;
pranešimas apie tai praneša, kol jį uždarysite.

Žr. [Papildinių kūrimas](../../../guides/plugin-authoring.md) ir [Priedų patikros](addon-security.md).

## Viso paketo patvirtinimas

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
Vykdymo aplinkos interpretatorius ir sistemos bibliotekos lieka patikimos platformos dalimi ir
nepateikiami kaip peržiūrėti trečiųjų šalių paketų baitai. Ši vientisumo patikra nesertifikuoja elgsenos,
nepatvirtina bet kokio kodo, atsisiųsto ar įkelto už patvirtintos distribucijos ribų, netarpininkauja
kiekvienam vidiniam poveikiui ir nepakeičia įtaisytojo piniginės patvirtinimo.

macOS sistemoje papildinių kodas vykdomas macOS smėlio dėžėje. Raktai doroklį pasiekia tik per jo privačią
įvestį, niekada per jo aplinką, nes bet kuri to paties naudotojo programa, įskaitant papildinių kodą,
Mac kompiuteryje gali perskaityti kitos programos pradinę aplinką. Ryšio serveris, kuriam raktas reikalingas jo
aplinkoje, veikia tik pasirenkamoje izoliuotoje Linux vykdymo aplinkoje, kurią parengiate patys;
VOOL niekada nepaleidžia virtualiosios mašinos ir neatsisiunčia atvaizdo. Be jos toks papildinys lieka išjungtas ir nurodo priežastį.
Linux naudoja bwrap, įskaitant privačią procesų vardų sritį ir `/proc`.

macOS sistemoje įrankis, deklaruojantis tinklą, gali pasiekti tik privatų tarpinį serverį, kurį VOOL paleidžia kiekvienam
iškvietimui, o tarpinis serveris jungiasi tik prie deklaruotų pagrindinių kompiuterių. Linux sistemoje ir izoliuotoje vykdymo aplinkoje
papildinių procesai neturi tinklo. Savųjų tiekėjų ir skenerių užklausos visose platformose naudoja savo atskirą
HTTPS transportą į deklaruotus pagrindinius kompiuterius.

## Įgūdžio rašymas

* Kaip sukurti įgūdį paprašius pokalbyje, žr. [Susikurkite savo įgūdį](../../../guides/make-your-own-skill.md).
* Kaip patiems parašyti, patikrinti linteriu, įdiegti ir išbandyti SKILL.md failą, žr. [Įgūdžio kūrimas ir testavimas](../../../guides/build-and-test-a-skill.md).
