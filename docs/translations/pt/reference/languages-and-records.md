---
description: Idiomas, acessibilidade, histórico de atividade, recibos e como as respostas dos modelos são verificadas.
translation: machine
source_sha256: 2e135f9673ae465cee9db53572ab89c93235805dfea713efd73d7f4244b6b1f5
---

# Idiomas, acessibilidade e registros

## Idiomas da interface

A interface do VOOL está disponível em vários idiomas. A escolha do idioma afeta só a
apresentação: decisões de permissão, autoridade de gastos e códigos de erro são
**independentes do idioma por construção** — uma mensagem de erro traduzida nunca pode ampliar nem
restringir o que a autoridade em inglês decidiu.

## Acessibilidade

* Atalhos de teclado para os fluxos principais (veja [Atalhos de teclado](shortcuts.md)).
* Apresentação clara e escura na documentação e nas telas do app, seguindo a preferência do seu sistema, com
  opção de troca manual.
* Os erros são indicados em texto, não só por cor; ações destrutivas sempre exigem uma
  confirmação explícita, em vez de um único clique errado.

## Atividade

A visão de atividade é o histórico completo do que o VOOL fez: chamadas de ferramentas com as entradas e os
resultados, aprovações que você concedeu ou recusou, e pagamentos com os recibos. Um turno que foi
parado por uma proteção mostra a parada e a proteção — uma recusa é um evento registrado, não uma
ausência.

## Recibos

Dinheiro e ações relevantes para gastos gravam **recibos**: o que foi proposto, o que foi aprovado,
o que foi enviado e o que foi liquidado. Resultados desconhecidos são registrados como desconhecidos — nunca
narrados como sucesso ou falha. É isso que torna possível seguir *“confira o estado dele antes de tentar de
novo”*: o recibo diz em que estado o pagamento realmente está.

## Status de verificação do modelo

O VOOL distingue o que um modelo **disse** do que o ambiente de execução **provou**:

* Os resultados das ferramentas são registrados com os registros de execução; as afirmações de uma resposta
  sobre arquivos, pagamentos ou dados ficam vinculadas a esses registros, e afirmações sem respaldo são retidas.
* Os recibos trazem um status de verificação; quando uma trilha é experimental ou não verificável nesta
  versão, o status diz isso em vez de dar a entender uma prova.

Veja [Recibos](../concepts/receipts.md) para o conceito e [Erros](errors.md) para saber como as
falhas são classificadas.
