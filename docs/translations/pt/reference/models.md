---
description: Quais modelos de IA funcionam com o VOOL — modelos locais no seu próprio hardware e modelos na nuvem pela sua conta no provedor.
translation: machine
source_sha256: 2ce84ebed0545059de772cb175bd8f088ee2d22feb60d4256ee7800f20097824
---

# Modelos compatíveis

O VOOL não vem com nenhum modelo — o primeiro modelo local é baixado na primeira vez que você o
usa. Ele se conecta aos ambientes de execução e aos provedores que você escolhe.

## Ambientes de execução locais

Ollama, llama.cpp, vLLM e MLX são detectados automaticamente. Outros ambientes de execução que exponham um
endpoint compatível com a OpenAI em localhost podem ser indicados manualmente.

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
