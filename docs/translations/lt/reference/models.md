---
description: Kurie DI modeliai veikia su VOOL — vietiniai modeliai jūsų pačių aparatinėje įrangoje ir debesijos modeliai per jūsų tiekėjo paskyrą.
translation: machine
source_sha256: 1528dfca8e391868d09156c42c1dd51514770ffab17077d9260d248842c82060
---

# Palaikomi modeliai

VOOL nepateikiamas su modeliu. Jis jungiasi prie jūsų pasirinktų vykdymo aplinkų ir tiekėjų.

## Vietinės vykdymo aplinkos

Galima naudoti bet kurią vykdymo aplinką, kuri localhost adresu teikia su OpenAI suderinamą galinį tašką.
Populiariausios aptinkamos automatiškai.

## Debesijos tiekėjai

Prie debesijos modelių jungiamasi su jūsų raktu, iš jūsų kompiuterio. Pasirinkus vieną iš jų, programa
persijungia į debesijos režimą (indikatorius rodo tiekėjo pavadinimą) — žr. [Vietoje ir debesijoje](../concepts/local-and-cloud.md).

[OpenRouter](../guides/openrouter.md) yra plačiausias pavienis ryšys: vienas raktas, daug
tiekėjų.

## Pasirinkimas

| Darbas | Protingas pasirinkimas |
| --- | --- |
| Privatūs failai, be tinklo | Vietinis, 7B ar didesnis |
| Kasdienis programavimas | Vietinis 7–14B programavimo modelis |
| Ilgas samprotavimas | Didesnis debesijos modelis |
| Išlaidų kontrolė | Pirmiausia vietinis, debesija tada, kai ji verta savo kainos |
