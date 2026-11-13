---
description: Kurie DI modeliai veikia su VOOL — vietiniai modeliai jūsų pačių aparatinėje įrangoje ir debesijos modeliai per jūsų tiekėjo paskyrą.
translation: machine
source_sha256: 2ce84ebed0545059de772cb175bd8f088ee2d22feb60d4256ee7800f20097824
---

# Palaikomi modeliai

VOOL nepateikiamas su modeliu — pirmasis vietinis modelis atsisiunčiamas, kai jį panaudojate pirmą kartą.
Jis jungiasi prie jūsų pasirinktų vykdymo aplinkų ir tiekėjų.

## Vietinės vykdymo aplinkos

Ollama, llama.cpp, vLLM ir MLX aptinkamos automatiškai. Kitas vykdymo aplinkas, kurios localhost adresu teikia
su OpenAI suderinamą galinį tašką, galima nurodyti rankiniu būdu.

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
