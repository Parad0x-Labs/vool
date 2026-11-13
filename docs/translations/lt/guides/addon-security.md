---
translation: machine
source_sha256: 021137e6424e28cf4af2913b10198aa3e59fb7d63a002949eeea0837375436a8
---
# Priedų patikros

Priedai atskiria naršymą, patikrą ir diegimą. Nuskaitymas aprašo konkrečius baitus;
tai nėra saugumo garantija, leidėjo rekomendacija, vykdymo leidimas ar piniginės patvirtinimas.

Naršyti pirmiausia rodo paties VOOL įgūdžius, įskaitant 75 VOOL įgūdžius, pateikiamus su programa (22 iš jų
naudoja VOOL įrankius, kurie tik susiaurina tai, ką VOOL jau siūlo), tada peržiūrėtus katalogo priedus. Įgūdžių kodeksas pirmiausia rodo VOOL komandos įgūdžius, virš redaktorių atrinktų
ir rekomenduojamų įgūdžių.

## VOOL sukurti įgūdžiai: vietinė patikra

75 VOOL sukurti įgūdžiai pateikiami programos viduje; kiekvienas yra prisegtas pagal SHA-256 paties VOOL kataloge ir
peržiūrimas, kai išleidžiamas VOOL. Jiems **Pridėti šį įgūdį** paleidžia tik VOOL vietinę patikrą: be rakto,
be nuskaitymų kvotos ir be jokių tinklo užklausų.

1. VOOL perskaito įgūdį ir licenciją iš pačios programos kopijos ir juos atmeta, jei jų SHA-256
   nesutampa su prisegtuoju (*Šio VOOL įgūdžio failas nesutampa su kopija, gauta kartu su VOOL, todėl jis nepridėtas.*).
2. VOOL vietinė patikra perskaito įgūdį ieškodama rizikingų instrukcijų ir parodo rezultatą. Švarų rezultatą
   galima pridėti mygtuku **Pridėti ir įjungti**; viskam, ką ji pažymi, reikia jūsų atskiro, aiškaus sutikimo
   būtent tai versijai. **Atmesti** jį išmeta.
3. Tada diegimas vyksta tuo pačiu gyvavimo ciklu kaip ir kitų įgūdžių: tikslūs failai įdiegiami,
   patikrinami ir įjungiami. Įdiegtas įgūdis tikrinamas pagal prisegtą SHA-256 kiekvieną kartą, kai yra
   įkeliamas; pakeistas failas padaro jį neprieinamą.

Vietinė patikra yra statinis fiksuotų baitų skaitymas, o ne saugumo ar atsakymų kokybės garantija.
Kitų kūrėjų įgūdžiai niekada neina šiuo keliu.

## Vieši įgūdžiai

Pasirinkite viešą GitHub įgūdį fiksuotoje revizijoje ir patvirtinkite jo turinio atsisiuntimą. VOOL patikrina
jį šiame įrenginyje. Jei įjungto skenerio papildinio nėra, puslapis paaiškina,
kad patikros liko šiame įrenginyje. Šiam keliui skenerio rakto nereikia.

Įjungtas skenerio papildinys, kai paprašoma nuskaitymo, taip pat gauna viešo įgūdžio turinį. Tai
gali naudoti jo paslaugos kvotą. Vietiniai pokalbiai, privatūs darbo erdvės failai ir kiti prisijungimo duomenys
nesiunčiami. Rakto išsaugojimas ir Priedų atidarymas neatlieka nei ryšio testo, nei nuskaitymo užklausos.
Automatinių užklausų kartojimų nėra.

Radinius skaitykite ir diegimą priimkite atskirai. Ataskaitos patvirtinimui reikia visiškos aprėpties
ir galiojančių radinių. Sėkminga ataskaita neturi radinių ir politikos pažeidimų. Atskirai siūlomas
rizikos priėmimas susiejamas su peržiūrėta versija; jis niekada nekeičia ataskaitos ir nesuteikia daugiau prieigos.
Pasibaigusios peržiūros, pakeisti parengto atsisiuntimo baitai, nuorodos ir konfliktuojantys tiksliniai aplankai atmetami.
Kvitai susieja visą įdiegtą distribuciją. Pakeisti failai atšaukia prieinamumą vykdymo metu.
Įgūdžio nurodymai įterpiami visi arba aiškiai praleidžiami, kai netelpa į raginimo biudžetą.

## Pasirenkami skeneriai

Nuotolinis skeneris yra pasirenkamas. Kai joks skenerio papildinys neįjungtas, ar įgūdį galima įdiegti,
sprendžia paties VOOL patikra šiame įrenginyje; ankstesnės versijos vietoj to reikalavo nuotolinio nuskaitymo.

Norėdami jį pridėti, įdiekite skenerio papildinį iš jo kūrėjo per skiltį Papildiniai, išsaugokite savo
raktą, peržiūrėkite prieigą ir jį įjunkite. Jo deklaracija nurodo kelią aiškiam ryšio testui
ir kelią užsakytam nuskaitymui, o šias užklausas atlieka bendras savasis papildinių transportas.
VOOL neturi įtaisyto skenerio kliento ar centrinio skenerio rakto nustatymo ir nesiūlo savo
skenerio papildinio. Iš senesnio VOOL išsaugotas Eyebrow raktas lieka šiame kompiuteryje ir nenaudojamas.
Identifikatoriai `vool-plugin-eyebrow` ir `vool-plugin-usepod` yra rezervuoti; kitas paketas, kuris pretenduoja
į vieną iš jų, atmetamas dar prieš peržiūrą.

Paties VOOL patikra šiame įrenginyje vykdoma visada. Skenerio papildinio ataskaita pridedama šalia jos:
jei bet kuri iš jų randa problemą, diegimas blokuojamas, o papildinio ataskaita turi nurodyti tikslų
patikrintą turinį. Skenerio papildinys niekada negali pats patvirtinti diegimo.

Įjungtas skeneris su trūkstamu ar atmestu raktu aiškiai baigiasi klaida. VOOL niekada tyliai nepakeičia
užsakyto nuotolinio nuskaitymo sėkmingu vietiniu rezultatu. Skirtojo laiko pabaigos, paslaugos klaidos, peradresavimai,
per dideli atsakymai ir neišsamios ataskaitos palieka diegimą nepriimtą. Užklausa, kuriai baigėsi skirtasis laikas,
vis tiek galėjo sunaudoti paslaugos kvotą.

## Vykdomieji paketai ir parduotuvės peržiūra

Papildiniams taikoma visa vietinio aplanko arba prisegto viešo GitHub paketo peržiūra, atskira nuo
tik tekstinių įgūdžių importavimo. Diegimas nevykdo jokio paketo kodo ir palieka jį išjungtą. Parduotuvės papildiniams
taip pat reikia esamo pasirašyto kvito ir visos distribucijos maišos. Įjungimui reikia
atskiro prieigos lapo, apimančio įrankius, poveikius, tikslius pagrindinius kompiuterius (hostus), prisijungimo duomenų lizdus ir pridėtus serverius.

Vykdomasis kodas iškvietimo metu gauna tik savo deklaruotus raktus. Jis negali skaityti kito
papildinio lizdų, savininko mokamų modelių raktų ar piniginės pasirašymo, limitų, patvirtinimo ar kvitų įgaliojimų.
Jo išvestis lieka nepatikima. Pakeisti baitai atšaukia sutikimą; išjungimas sustabdo iškvietimus. Nepalaikoma
izoliacija atsisako vykdyti. Vykdymo aplinkos ribos aprašytos puslapyje [Papildinių kūrimas](../../../guides/plugin-authoring.md).

Jei VOOL negali perskaityti savo papildinių įrašų, paketai, apie kuriuos jis neturi įrašo, nustoja siūlyti įgūdžius, išskyrus
tuos, kurie pateikiami su VOOL, o sugadintas failas paliekamas šalia naujojo. Jei jis negali perskaityti
jūsų įjungimo ir išjungimo pasirinkimų, kiekvienas papildinys laikomas išjungtu, kol vėl jį įjungsite.

## Vietinė API ir transportas

`GET /api/addons` skaito sąrašą. Pakeitimams per `POST /api/addons` reikia savininko vietinės prieigos,
JSON, tos pačios kilmės naršyklės užklausų ir veiksmui būdingų laukų. Papildinių įjungimo / išjungimo ir gyvavimo ciklo
durys (`POST /api/plugins/enable`, `POST /api/plugins/lifecycle`) taiko tą pačią JSON ir
tos pačios kilmės taisyklę. Viešų šaltinių atsisiuntimai naudoja
tikslias revizijas ir ribotus patikrintus blobus. Savosioms paslaugoms reikia deklaruotų viešų HTTPS pagrindinių kompiuterių;
jos tikrina TLS, prisega išspręstus viešus adresus, atmeta peradresavimus, riboja atsakymus ir sustoja, kai
papildinys išjungiamas.

Vietinės netikros HTTPS paslaugos gali patikrinti transportą ir klaidų tvarkymą be mokamų iškvietimų. Jos
neįrodo realios paslaugos patikimumo, atsiskaitymo, modelių kompetencijos ar diegimo kitose
programose. Tam reikia atskiro patikrinimo.
