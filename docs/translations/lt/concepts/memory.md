---
description: Kaip VOOL įsimena informaciją tarp sesijų ir kur ta atmintis saugoma.
translation: machine
source_sha256: 65af63e755076fe33675d7a5d6afa7f0c5664b77f716ffc0dc1f0f358e67f667
---

# Atmintis

Atmintis — tai būdas, kuriuo VOOL perneša faktus tarp sesijų. Ji saugoma kaip failai jūsų
kompiuteryje, VOOL duomenų aplanke, skaitomu formatu.

## Kaip gerai ji įsimena

Su 150 niekada anksčiau nenaudotų klausimų iš LoCoMo ilgų pokalbių lyginamojo testo VOOL 0.7 pateikta atmintis
teisingai atsakė į 123 (82,0 %). Tiesioginio palyginimo ture su tais pačiais klausimais, kai už visus tris
atsakė tas pats modelis, VOOL surinko 124 (82,7 %), OpenClaw — 107 (71,3 %), o Hermes — 94 (62,7 %),
be to, siųsdamas nuo 3,5 iki 5,4 karto mažiau žetonų vienam klausimui. Kiekvienas paleidimas paskelbtas:
[atminties lyginamieji testai](https://github.com/Parad0x-Labs/vool/tree/main/docs/benchmarks).

## Savybės

* **Diske, atvirai.** Atmintis yra failai, kuriuos galite perskaityti. `MEMORY.md` yra jos skaitomas
  pavidalas; už jo slypintys faktai laikomi šalia esančiame įrašų faile.
* **Lokaliai.** Atmintis nesinchronizuojama su jokiu serveriu.
* **Viena saugykla vienam VOOL duomenų aplankui.** Atmintis neskirstoma pagal darbo sritis — ji saugoma
  visam įdiegimui, todėl ji nėra izoliacijos tarp projektų riba.

## Kaip ją tvarkyti

Pastabos, kurias patys įrašote į `MEMORY.md`, išsaugomos. VOOL įrašyti faktai ištrinami naudojant
VOOL pamiršimo operaciją, o ne ištrinant failą: pamiršimas vienu žingsniu perrašo įrašų saugyklą ir jos
veidrodinę kopiją, tada abi perskaito iš naujo, kad patvirtintų, jog teksto nebeliko. Vien ištrynus
`MEMORY.md`, duomenys nesunaikinami — įrašų failas vis dar laiko faktus, o veidrodinė kopija iš jo
atkuriama.

Debesijos režimu aktuali atminties dalis gali patekti į kontekstą, siunčiamą tiekėjui, tomis pačiomis
sąlygomis kaip ir bet koks kitas kontekstas. Žr. [Vietoje ir debesijoje](local-and-cloud.md).

Pamiršimas pokalbyje taip pat ištrina žodžius iš to pokalbio vykdymo istorijos vykdymo aplinkos duomenų
bazėje: iš ėjimų įrašų, kontrolinių taškų, bandymų, užduočių planų, galutinių atsakymų (kartu su kopijomis,
kurias apima atsakymų ištrynimas), žinių kandidatų, konteksto ataskaitų, pranešimų, pokalbio sukurtų
tyrimo artefaktų ir prisegtų žinučių, kurios juos citavo. Įrašai, nuo kurių priklauso kiti įrašai, lieka,
bet pamiršti žodžiai juose išvalomi, kartu su bet kokia šalia jų saugoma maiša; kiti įrašai ištrinami.
Ėjimas, kuris pamiršimo metu dar vyko, negali įrašyti žodžių atgal: jo įvykiai išvalomi, o jam pasibaigus
jo įrašai ištrinami dar kartą. Tada duomenų bazės išankstinio rašymo žurnalas suliejamas, kad senesnės
kopijos neliktų `-wal` faile. Kito pokalbio įrašai nepaliečiami. Eilėje laukianti dar neišsiųsta žinutė
išsaugoma, ir tada pamiršimas praneša, kad jo nepavyko pritaikyti visiškai. Lentelė, kurios nežino nė vienas
ištrynimo žingsnis, įvardijama atsakyme, kuriame sakoma, kad pakartotinis bandymas jos neišvalys.

Žodžiai lyginami neatsižvelgiant į didžiąsias ir mažąsias raides, o kitu raštu užrašytas skaitmuo laikomas
tuo pačiu skaitmeniu (viso pločio „１２“ yra 12). Pamirštas skaičius sutampa tik ten, kur jis stovi
kaip atskiras skaičius: kartu su juo parašytose raidėse ir skaitmenyse negali būti jokio kito
skaitmens, ir jis negali tęstis per `.`, `,`, `:`, `/`, `-` ar `_` į kitą skaitmenį. Pamiršus „12“, ištrinama „kodas 12“, „12kg“ ir „B12“,
bet ne „kambarys 312“, „3.12“, „12.5“, „12:30“ ar „2026-10-12“, o pamiršus „200“, neištrinama
„1,200“. Vykdymo istorijoje plikas skaičius, kurį vykdymo aplinka suskaičiavo ar išmatavo (pavyzdžiui,
`elapsed_ms` ar `step_count` reikšmė), nesutapdinamas.

Atminties skaitytuvai taip pat tikrina patvarų pamiršimo registrą. Naujas atminties aplankas jį sukuria
prieš priimdamas įrašus. Senesnė semantinė saugykla atnaujinama vietoje tik tada, kai joje neužfiksuota
jokio ankstesnio ištrynimo; joje išsaugoti prisiminimai lieka prieinami. Esamas, bet neperskaitomas
registras, trūkstamas registras esant išsaugotam liudytojui arba užfiksuotas pamiršimo barjeras lemia,
kad skaitytuvai sulaiko paveiktą atmintį. Registro failų ištrynimas nėra būdas atkurti pamirštą turinį.

Laukiantis ištrynimo žurnalas atkūrimui išsaugo savo žetoną. Kai jau egzistuoja nuolatinis apibrėžtos
apimties atšaukimas, užbaigimas išvalo nebereikalingą žurnalo žetoną, išlaikydamas operacijos tapatybę
ir būseną. Atšaukimų registras lieka.
