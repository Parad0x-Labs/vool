---
description: Kaip VOOL apsaugo jūsų failus ir API raktus — darbo srities ribos, prisijungimo duomenų saugojimas ir ką gali pasiekti papildinys.
translation: machine
source_sha256: 4397ca0e7b494efacb90aa546eae10800b9a58558ba7c611c0131f4410c2fe3a
---

# Saugumo modelis

## Ribos

* **Aplankų apribojimai** riboja prieigą prie failų. VOOL skaito ir keičia failus pokalbio projekto
  aplanke. Už jo ribų VOOL gali skaityti matomus jūsų namų aplankus, o failus keisti tik aplankuose
  Desktop, Downloads ir Documents; paslėpti aplankai, programų ir sistemos duomenys bei paties VOOL
  duomenys niekada neskaitomi. Žr. [Darbo sritys](../concepts/workspaces.md).
* **Prisijungimo duomenų saugykla** laiko raktus. Raktai neįrašomi į konfigūracijos failus ar žurnalus.
* **Įrankių patvirtinimas** riboja pakeitimus. Režimu Rankinis dėl kiekvieno pakeitimo pirmiausia
  klausiama; režimu Automatinis kasdieniai redagavimai ir komandos gali būti vykdomi automatiškai, o dėl
  trynimo, perrašymo, diegimo, išlaidų ir nustatymų keitimo vis tiek klausiama. Žr.
  [Įrankiai](../concepts/tools.md).

## Jūsų vietinis tinklas

Pagal numatytuosius nustatymus VOOL klausosi tik šiame kompiuteryje. Kai nustatote, kad jis klausytųsi
jūsų vietiniame tinkle, tik šis kompiuteris laikomas jumis. Nustatymai, failai, raktai, įrankiai ir
privačios funkcijos atsako tik jam.

Kitas tinklo įrenginys gauna paprastą pokalbį. Jis taip pat gali vienu metu perskaityti vieną viešą
tinklalapį. Tas puslapis turi būti viešajame internete: adresai šiame kompiuteryje ar jūsų vietiniame
tinkle atmetami, o kiekvienas peradresavimas tikrinamas taip pat. Paieška žiniatinklyje, tyrimai,
naršyklė, kainos pasiūlymai, mokėjimai ir jūsų mokami raktai veikia tik šiame kompiuteryje.

## Discord tiltas

Discord tiltas atsako tik savo savininkui. Savininku tampa pirmasis Discord naudotojas, išsiuntęs jam
komandą. Norėdami patys pasirinkti savininką, nustatykite `VOOL_DISCORD_OWNER_USER_ID` į savo Discord
naudotojo ID; tai padarykite bet kuriam kanalui, kuriame gali rašyti kiti žmonės. Visiems kitiems vieną
kartą pranešama, kad šis VOOL užrakintas savo savininkui, o jų žinutės neapdorojamos. Net savininko
Discord žinutėms taikomi tie patys apribojimai kaip kitam tinklo įrenginiui.

## Raktai

VOOL jūsų tiekėjų raktus laiko šifruotame faile savo aplanke „runtime home“ šiame kompiuteryje
(žr. [Konfigūracija](../reference/configuration.md)), kurį gali perskaityti tik jūsų naudotojo paskyra.
Raktus, kuriuos VOOL jau laikė macOS Keychain Mac kompiuteryje, ten ir toliau galima perskaityti. Raktas
naudojamas iš jūsų kompiuterio jūsų tiekėjui pasiekti. Jis nesiunčiamas Parad0x Labs, ir užklausos kelyje
nėra jokio Parad0x valdomo galinio taško.

{% hint style="info" %}
Šifruotą failą saugo jūsų naudotojo paskyros failų leidimai, o ne atskiras slaptažodis. Laikykite
įjungtą disko šifravimą (FileVault Mac kompiuteryje), kad pamestas ar pavogtas diskas neatskleistų jūsų
raktų.
{% endhint %}

## Papildiniai

Papildinys veikia su vykdymo aplinkos prieiga. Būtent tai daro papildinius naudingus ir būtent dėl to juos
verta perskaityti prieš diegiant. Žr. [Įgūdžiai ir papildiniai](../guides/skills-and-plugins.md).

## Pranešimas apie problemą

Apie saugumo problemas praneškite privačiai per
[GitHub Security Advisories](https://github.com/Parad0x-Labs/vool/security/advisories/new),
niekada ne viešame pranešime. Nurodykite versiją, platformą ir veiksmus problemai atkartoti.

{% hint style="warning" %}
macOS programos pasirašytos ad hoc ir nėra notarizuotos; Linux tar archyvas nepasirašytas. Žr.
[Leidimų būsena](release-status.md).
{% endhint %}
