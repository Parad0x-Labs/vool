---
description: Ką šioje VOOL versijoje galite išsaugoti, perkelti ir atkurti: pokalbius, atmintį, raktus ir pinigines.
translation: machine
source_sha256: 3b2f11975a8cf5f5ab2e07e8b4a3083a9598de9e301965c493408005aa5893fa
---

# Atsarginės kopijos ir atkūrimas

Viskas, ką laiko VOOL, yra jūsų kompiuteryje, viename profilio aplanke (kur — žr.
[Konfigūracija](../reference/configuration.md)). Programos atnaujinimas ar įdiegimas iš naujo jo niekada
neliečia. VOOL neturi jūsų duomenų kopijos debesyje ir neturi viso profilio atsarginės kopijos vienu
veiksmu. Štai būdai išsaugoti ir susigrąžinti tai, kas svarbu.

## Pokalbiai

| Ko norite | Ką naudoti | Ką gaunate |
| --- | --- | --- |
| Kopijos, kurią galima skaityti ar bendrinti | **Eksportuoti** pokalbio antraštėje | Visą pokalbį kaip tekstą arba Markdown. Prisegti failai išvardijami tik pavadinimais. |
| Kopijos, kurią galima grąžinti į VOOL | Nustatymai, Atsarginės kopijos ir atkūrimas, **Eksportuoti pokalbį** | Vieną pokalbį kaip `.voolsession` failą, pasirašytą šio kompiuterio. |
| Pokalbio iš to failo atgal | Nustatymai, Atsarginės kopijos ir atkūrimas, **Importuoti pokalbį** | Pokalbį, pridėtą šalia kitų. |
| Savo pokalbių iš kitų DI programėlių | Nustatymai, Atsarginės kopijos ir atkūrimas, **Perkelkite pokalbius iš kitų DI programėlių** | Tuos pokalbius su jų pavadinimais ir žinutėmis. |

**Eksportuoti pokalbį** perkelia pokalbio žinutes, kvitus ir nuorodas į įrodymus, bet niekada ne vidinius
VOOL samprotavimus, jūsų nustatymus, atmintį ar raktus. Kai failas palieka šį kompiuterį, apsaugokite jį
slaptafraze: eksportuojant be jos, pirmiausia paprašoma patvirtinimo. Peržiūra tiksliai parodo, kas bus
įrašyta, dar prieš ką nors įrašant.

**Importuoti pokalbį** parodo, ką failas turi ir kas jį pasirašė, dar prieš ką nors pridedant. Failas,
pasirašytas raktu, kurio VOOL nepažįsta, atmetamas, nebent jį priimate, ir pokalbis lieka pažymėtas kaip
nepatikimas. Importas niekada nekeičia jau turimo pokalbio: jei toks pat pokalbis jau yra, kopija
atsiranda kaip naujas pokalbis.

**Perkelkite pokalbius iš kitų DI programėlių** skaito programą šiame kompiuteryje arba duomenų eksporto
failą. Pažymite pokalbius, kuriuos norite perkelti. Niekas nepalieka jūsų kompiuterio, o kitos programos
failai niekada nekeičiami. VOOL šiuos pokalbius laiko kaip istoriją: jis iš jų nesimoko ir nevykdo nieko,
kas juose parašyta.

## Atmintis

Nustatymai, Atmintis ir asmeninimas, **Eksportuoti profilį** išsaugo tai, ką VOOL apie jus prisimena, kaip
failą, pavadintą `vool-memory-` ir data. Jame niekada nėra rakto ar slaptažodžio. Tai kopija skaityti ir
saugoti: VOOL jos importuoti negali.

## Modelių tiekėjų raktai

Tiekėjų raktų nėra jokiame eksporte. Naujame kompiuteryje vėl juos pridėkite Nustatymuose.

## Piniginės

* **Tinklui sukurta VOOL piniginė** sąrankos metu, ir tik tada, parodo vieną privačiojo rakto atsarginę
  kopiją. Laikykite ją neprisijungę prie interneto: kiekvienas, kas ją turi, valdo piniginę. Jei pamiršite
  piniginės PIN kodą ar slaptažodį, **Forgot PIN or password?** prie piniginės skiltyje Nustatymai,
  Kriptovaliutos atkuria prieigą su ta atsargine kopija, o jūs pasirenkate naują PIN kodą ar slaptažodį.
* **Senoji kišenė** saugoma jos atkūrimo fraze, parodyta vieną kartą ją kuriant.
  **Restore from phrase…** skiltyje Nustatymai, Kriptovaliutos, Kitos paskyros ją atkuria iš frazės. Ji
  taip pat gali eksportuoti savo privatųjį raktą į Phantom ar MetaMask mygtuku **Export private key**,
  kuris atsidaro tik su Touch ID arba jūsų Mac slaptažodžiu.
* Piniginė, kurią tik stebite, arba iš kitos programos prijungta piniginė VOOL programoje nelaiko jokio
  rakto: čia nėra ko kurti atsarginės kopijos.

Visas vaizdas pateiktas skyriuje [Piniginė](../guides/wallet.md).

## Jei atnaujinimas nepavyksta

Atnaujinimas, kuris nepavyksta bet kuriame žingsnyje, palieka programą tokią, kokia buvo. Jei jūsų duomenų
perkėlimas į naują versiją nepavyksta, jūsų duomenys grąžinami į ankstesnę būseną, o VOOL paleidžiamas su
ankstesne versija. Žr. [Atnaujinimai ir migracija](../reference/updates-migration.md).
