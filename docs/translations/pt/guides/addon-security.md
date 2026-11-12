---
translation: machine
source_sha256: 671f147e5d9649b6c2b7c2d391ea085120e783a8ace29f92ac8c3d5ecb4407eb
---
# Descobrir habilidades e verificações do Eyebrow

A página Habilidades e plugins tem quatro abas: **Explorar**, **Códice de habilidades**, **Os meus complementos** e **Segurança**. Explorar lista primeiro as habilidades do próprio VOOL, incluindo 53 habilidades do VOOL só de texto que vêm com o app, depois os complementos revisados do catálogo, e pode consultar um repositório público do GitHub em uma revisão fixada. Códice de habilidades lista primeiro as habilidades da equipe do VOOL, acima das escolhas editoriais e das habilidades em destaque. A pesquisa ignora maiúsculas e separadores comuns; os controles de categoria e de ordenação restringem a lista. Origem, licença, revisão e requisitos ficam visíveis antes do download.

## Primeiro uso

1. Salve uma chave de API do Eyebrow na aba Segurança, também disponível em Configurações → Chaves de API. A chave fica sob a guarda do armazenamento de credenciais existente. Salvar não testa a conexão nem verifica nada; Testar ligação contata o Eyebrow explicitamente.
2. Em Explorar, escolha uma habilidade e aprove baixar o conteúdo público dela e enviar esse conteúdo ao Eyebrow. Isso consome a cota de verificações do serviço conforme o seu plano. Não há novas tentativas automáticas.
3. Leia o relatório retornado. Uma resposta HTTP bem-sucedida, sozinha, não é um resultado limpo. Este catálogo inicial só permite a instalação com um relatório `pass` completo, sem descobertas e sem violações de política. Rejeitar descarta o download preparado sem instalá-lo.
4. Aceite a instalação separadamente. O VOOL preserva a licença, instala pelo ciclo de vida de plugins existente, verifica o pacote exato revisado e ativa a habilidade. As permissões existentes continuam controlando cada ação que ela possa sugerir.

Uma verificação é a avaliação de um fornecedor sobre bytes específicos, não uma garantia de segurança, compatibilidade ou qualidade das respostas. Estar no catálogo não é uma certificação do publicador.

## Limites

O conteúdo só vem de repositórios públicos do GitHub em uma revisão fixa de 40 caracteres, com os arquivos da habilidade e da licença fixados por SHA-256. Nesta versão não há repositórios privados, extração de arquivos compactados, script de instalação nem importador de plugins executáveis ou MCP. Só o SKILL.md independente é enviado ao Eyebrow; conversas locais, arquivos do espaço de trabalho, credenciais e o conteúdo das licenças não são enviados para verificação.

Os downloads ficam inertes até a revisão e a aceitação. Recibos autenticados localmente vinculam o relatório ao conteúdo fixado. A instalação recusa revisões expiradas (24 horas), arquivos preparados modificados e diretórios de destino existentes. O ambiente de execução verifica o estado do ciclo de vida e os bytes revisados antes de adicionar uma habilidade gerenciada ao contexto do modelo. Arquivos adicionados, links simbólicos ou conteúdo alterado a tornam indisponível. As orientações são inseridas inteiras ou registradas explicitamente como omitidas por falta de orçamento de prompt; as instruções verificadas não são truncadas em silêncio.

Essas verificações não isolam código malicioso que já esteja rodando como o usuário do sistema operacional. Elas não se estendem a plugins instalados manualmente antes. Um recibo de verificação não concede nenhuma nova permissão de execução nem autoridade sobre a carteira.

## API e tratamento de falhas

`GET /api/addons` retorna o catálogo e o status local sem teste de conexão nem verificação. `POST /api/addons` aceita `save_key`, `remove_key`, `test_key`, `scan`, `install` e `reject`. As operações que alteram algo exigem pedidos por loopback, em JSON e, a partir do navegador, da mesma origem; só são aceitos os campos específicos de cada operação. URLs de origem e caminhos fornecidos por quem chama são recusados.

O cliente usa o pedido do Eyebrow v1 com o código-fonte da habilidade embutido, em `/v1/scan`, com autenticação bearer, uma origem HTTPS fixa, sem redirecionamentos, com tamanho de resposta limitado e sem novas tentativas automáticas. `/v1/version` é usado para o teste de conexão explícito. Referência da API: https://eyebrow.cc/api/reference .

Falhas tipificadas comuns incluem:

| Código | Significado / recuperação |
| --- | --- |
| `eyebrow_key_missing` / `eyebrow_unauthorized` | Salve ou substitua a chave. |
| `eyebrow_quota` | Confira a cota do serviço antes de tentar de novo. |
| `eyebrow_timeout` / `eyebrow_unreachable` | Sem nova tentativa automática; uma verificação que esgotou o tempo ainda pode consumir cota. |
| `eyebrow_invalid_report` / `eyebrow_no_coverage` | As evidências estão incompletas; nada é ativado. |
| `addon_source_changed` / `addon_changed` | Os bytes fixados ou revisados são diferentes; não os ative. |
| `review_untrusted` / `review_expired` | Obtenha uma nova revisão antes da instalação. |
| `addon_already_installed` | Os arquivos existentes foram preservados. A substituição automática não está implementada. |

## Limitações atuais e verificação

Não há atualização automática, importador de catálogos arbitrários nem fluxo de reparo ou reinstalação no lugar para um pacote existente que mudou. A recuperação de uma revisão pendente depois de reiniciar o app não aparece na interface. Por enquanto, os novos textos da interface usam o inglês como alternativa; não se afirma que as traduções estejam completas.

Os testes automatizados exercitam o ciclo de vida real, o carregamento de habilidades, os handlers da API e a interface servida no navegador, com downloads públicos e respostas do Eyebrow explicitamente simulados. Eles comprovam o comportamento do ambiente de execução e da interface, não a disponibilidade real do Eyebrow nem a competência do modelo. Uma verificação real com chave e a aceitação no app empacotado nativo continuam sendo verificações de lançamento. Nenhuma chave nem chamada a serviço pago é necessária para rodar esses testes.
