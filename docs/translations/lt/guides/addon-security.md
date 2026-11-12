---
translation: machine
source_sha256: 671f147e5d9649b6c2b7c2d391ea085120e783a8ace29f92ac8c3d5ecb4407eb
---
# Įgūdžių paieška ir Eyebrow patikros

Puslapis Įgūdžiai ir papildiniai turi keturias korteles: **Naršyti**, **Įgūdžių kodeksas**, **Mano priedai** ir **Sauga**. Naršyti pirmiausia rodo paties VOOL įgūdžius, įskaitant 53 tik tekstinius VOOL įgūdžius, pridėtus prie programos, tada peržiūrėtus katalogo priedus, ir gali ieškoti viešos GitHub repozitorijos prisegtoje revizijoje. Įgūdžių kodeksas pirmiausia rodo VOOL komandos įgūdžius, virš redaktorių pasirinkimų ir rekomenduojamų įgūdžių. Paieška nepaiso raidžių dydžio ir įprastų skyriklių; kategorijų ir rikiavimo valdikliai susiaurina sąrašą. Šaltinis, licencija, revizija ir reikalavimai matomi prieš atsisiunčiant.

## Pirmasis naudojimas

1. Išsaugokite Eyebrow API raktą kortelėje Sauga, kuri pasiekiama ir per Nustatymai → API raktai. Raktą valdo esama prisijungimo duomenų saugykla. Išsaugojimas netikrina ryšio ir nieko neskenuoja; su Eyebrow aiškiai susisiekia tik mygtukas Patikrinti ryšį.
2. Kortelėje Naršyti pasirinkite įgūdį ir patvirtinkite jo viešo turinio atsisiuntimą bei to turinio išsiuntimą Eyebrow. Tai naudoja paslaugos skenavimų limitą pagal jūsų planą. Automatiniai pakartotiniai bandymai nevykdomi.
3. Perskaitykite grąžintą ataskaitą. Vien sėkmingas HTTP atsakymas dar nėra švarus rezultatas. Šis pradinis katalogas leidžia diegti tik esant išsamiai `pass` ataskaitai, be jokių radinių ir be politikos pažeidimų. Atmesti pašalina paruoštą atsisiuntimą jo neįdiegdamas.
4. Diegimą priimkite atskirai. VOOL išsaugo licenciją, diegia per savo esamą papildinių gyvavimo ciklą, patikrina tiksliai peržiūrėtą paketą ir įjungia įgūdį. Kiekvieną veiksmą, kurį jis galėtų pasiūlyti, vis tiek valdo esami leidimai.

Skenavimas yra paslaugos teikėjo atliktas konkrečių baitų įvertinimas, o ne saugumo, suderinamumo ar atsakymų kokybės garantija. Įtraukimas į katalogą nėra leidėjo sertifikavimas.

## Ribos

Turinys gaunamas tik iš viešų GitHub repozitorijų fiksuotoje 40 simbolių revizijoje, o įgūdžio ir licencijos failai prisegti SHA-256 maiša. Šioje versijoje nėra privačių repozitorijų palaikymo, archyvų išpakavimo, diegimo scenarijų ar vykdomųjų/MCP papildinių importavimo. Eyebrow siunčiamas tik atskiras SKILL.md failas; vietiniai pokalbiai, darbo srities failai, prisijungimo duomenys ir licencijos turinys skenuoti neteikiami.

Atsisiųsti failai lieka neaktyvūs iki peržiūros ir priėmimo. Vietoje autentifikuoti kvitai susieja ataskaitą su prisegtu turiniu. Diegimas atmeta nebegaliojančias peržiūras (24 valandos), pakeistus paruoštus failus ir jau esamus paskirties katalogus. Prieš įtraukdama valdomą įgūdį į modelio kontekstą, vykdymo aplinka patikrina gyvavimo ciklo būseną ir peržiūrėtus baitus. Pridėti failai, simbolinės nuorodos ar pakeistas turinys padaro jį neprieinamą. Nurodymai įterpiami visi arba aiškiai pažymima, kad jie praleisti dėl nepakankamo užklausos biudžeto; nuskenuotos instrukcijos tyliai netrumpinamos.

Šios patikros neizoliuoja kenkėjiško kodo, kuris jau vykdomas operacinės sistemos naudotojo vardu. Jos netaikomos anksčiau rankiniu būdu įdiegtiems papildiniams. Skenavimo kvitas nesuteikia jokio naujo vykdymo leidimo ar įgaliojimų piniginėje.

## API ir nesėkmių tvarkymas

`GET /api/addons` grąžina katalogą ir vietinę būseną be ryšio patikros ir be skenavimo. `POST /api/addons` palaiko `save_key`, `remove_key`, `test_key`, `scan`, `install` ir `reject`. Būseną keičiančioms operacijoms reikia užklausų per loopback sąsają, JSON formatu ir iš tos pačios kilmės naršyklės; priimami tik konkrečiai operacijai skirti laukai. Kvietėjo pateikti šaltinio URL ir keliai atmetami.

Klientas naudoja Eyebrow v1 užklausą su įterptu įgūdžio šaltiniu adresu `/v1/scan`, su bearer autentifikavimu, fiksuota HTTPS kilme, be peradresavimų, su ribotu atsakymo dydžiu ir be automatinių pakartotinių bandymų. `/v1/version` naudojamas aiškiai ryšio patikrai. API dokumentacija: https://eyebrow.cc/api/reference .

Tipiškos tipizuotos nesėkmės:

| Kodas | Reikšmė / atkūrimas |
| --- | --- |
| `eyebrow_key_missing` / `eyebrow_unauthorized` | Išsaugokite arba pakeiskite raktą. |
| `eyebrow_quota` | Prieš bandydami dar kartą, peržiūrėkite paslaugos limitą. |
| `eyebrow_timeout` / `eyebrow_unreachable` | Automatinio pakartotinio bandymo nėra; skenavimas, kuriam baigėsi laikas, vis tiek gali sunaudoti limitą. |
| `eyebrow_invalid_report` / `eyebrow_no_coverage` | Įrodymai neišsamūs; niekas neįjungiama. |
| `addon_source_changed` / `addon_changed` | Prisegti arba peržiūrėti baitai skiriasi; jų neaktyvuokite. |
| `review_untrusted` / `review_expired` | Prieš diegdami gaukite naują peržiūrą. |
| `addon_already_installed` | Esami failai išsaugoti. Automatinis pakeitimas neįgyvendintas. |

## Dabartiniai apribojimai ir patikra

Nėra automatinio atnaujinimo, bet kokių katalogų importavimo ar esamo pakeisto paketo taisymo / pakartotinio diegimo vietoje proceso. Laukiančių peržiūrų atkūrimas po programos paleidimo iš naujo sąsajoje nepasiekiamas. Nauji sąsajos tekstai šiuo metu turi atsarginę anglišką versiją; išsamūs vertimai neteigiami.

Automatiniai testai išbando tikrą gyvavimo ciklą, įgūdžių įkėlimą, API doroklius ir pateikiamą naršyklės sąsają su aiškiai imituotais viešais atsisiuntimais ir Eyebrow atsakymais. Jie įrodo vykdymo aplinkos ir sąsajos elgseną, o ne realų Eyebrow pasiekiamumą ar modelio kompetenciją. Tikras skenavimas su raktu ir įtaisytos supakuotos programos priėmimas lieka išleidimo patikromis. Šiems testams paleisti nereikia jokio rakto ar mokamo paslaugos iškvietimo.
