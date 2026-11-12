---
description: Quais modelos de IA funcionam com o VOOL — modelos locais no seu próprio hardware e modelos na nuvem pela sua conta no provedor.
translation: machine
source_sha256: 1528dfca8e391868d09156c42c1dd51514770ffab17077d9260d248842c82060
---

# Modelos compatíveis

O VOOL não vem com nenhum modelo. Ele se conecta aos ambientes de execução e aos provedores que você escolhe.

## Ambientes de execução locais

Qualquer ambiente de execução que exponha um endpoint compatível com a OpenAI em localhost pode ser usado. A
detecção é automática para os mais comuns.

## Provedores na nuvem

Os modelos na nuvem são acessados com a sua chave, a partir da sua máquina. Selecionar um coloca o app no
modo nuvem (o indicador mostra o nome do provedor) — veja [Local e nuvem](../concepts/local-and-cloud.md).

O [OpenRouter](../guides/openrouter.md) é a conexão individual mais ampla: uma chave, muitos
provedores.

## Como escolher

| Trabalho | Escolha razoável |
| --- | --- |
| Arquivos privados, sem rede | Local, 7B ou maior |
| Programação do dia a dia | Modelo local de programação de 7–14B |
| Raciocínio longo | Um modelo maior na nuvem |
| Controle de custos | Local primeiro, nuvem quando valer a pena |
