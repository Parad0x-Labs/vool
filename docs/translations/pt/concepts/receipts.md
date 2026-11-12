---
description: Um recibo é o registro do que uma execução realmente fez.
translation: machine
source_sha256: caffc8be79cb1017a81f74baa2451e076db2e6660302b57db4b9d3ec92dab8b8
---

# Recibos

Um recibo é o registro durável de uma execução: quais ferramentas foram chamadas, com quais entradas, e
o que cada uma retornou.

## Por que eles existem

O resumo que um agente faz do próprio trabalho é uma afirmação. O recibo é o registro com que você pode conferir
essa afirmação.

## O que há em um recibo

| Campo | Significado |
| --- | --- |
| `tool` | Qual ação rodou |
| `input` | O que ela recebeu |
| `result` | O que ela retornou |
| `time` | Quando ela rodou |

Os recibos ficam na pasta “runtime home” do VOOL neste computador (veja
[Configuração](../reference/configuration.md)), não nas pastas dos seus projetos. Um recibo só sai
da sua máquina dentro de um relatório de bug que você visualiza e escolhe enviar (veja
[Relato de bugs e privacidade](../trust/bug-reporting.md)).
