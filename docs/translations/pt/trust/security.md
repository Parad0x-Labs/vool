---
description: Como o VOOL protege os seus arquivos e chaves de API — limites do espaço de trabalho, armazenamento de credenciais e o que um plugin pode acessar.
translation: machine
source_sha256: 4397ca0e7b494efacb90aa546eae10800b9a58558ba7c611c0131f4410c2fe3a
---

# Modelo de segurança

## Limites

* **Limites de pastas** restringem o acesso a arquivos. O VOOL lê e altera arquivos na pasta do projeto da
  conversa. Fora dela, o VOOL pode ler as pastas visíveis da sua pasta pessoal e alterar arquivos somente em
  Mesa, Downloads e Documentos; pastas ocultas, dados de apps e do sistema e os dados do próprio VOOL
  nunca são lidos. Veja [Espaços de trabalho](../concepts/workspaces.md).
* **O armazenamento de credenciais** guarda as chaves. As chaves não são gravadas em arquivos de configuração
  nem em logs.
* **A confirmação de ferramentas** restringe as alterações. No modo Manual, toda alteração pergunta antes; no
  modo Auto, as edições e os comandos do dia a dia podem rodar automaticamente, enquanto excluir, sobrescrever,
  instalar, gastar e alterar configurações continuam perguntando. Veja [Ferramentas](../concepts/tools.md).

## A sua rede local

Por padrão, o VOOL só escuta neste computador. Quando você o configura para escutar na sua rede
local, só este computador é tratado como você. Configurações, arquivos, chaves, ferramentas e recursos
privados só respondem a ele.

Outro dispositivo na rede recebe o chat simples. Ele também pode ler uma página pública da web por
vez. Essa página precisa estar na internet pública: endereços neste computador ou na sua rede
local são recusados, e cada redirecionamento é verificado da mesma forma. Buscas na web, pesquisas,
o navegador, cotações, pagamentos e as suas chaves pagas só funcionam neste computador.

## Ponte do Discord

A ponte do Discord só responde ao dono. O primeiro usuário do Discord que enviar um comando a ela
vira o dono. Para escolher você mesmo o dono, defina `VOOL_DISCORD_OWNER_USER_ID` com o seu
ID de usuário do Discord; faça isso em qualquer canal em que outras pessoas possam escrever. Todos os outros são
avisados uma vez de que este VOOL está bloqueado para o dono, e as mensagens deles não são processadas. Até as
mensagens do dono no Discord têm os mesmos limites que outro dispositivo na rede.

## Chaves

O VOOL guarda as suas chaves de provedor em um arquivo criptografado na runtime home dele neste computador
(veja [Configuração](../reference/configuration.md)), que só a sua conta de usuário consegue ler.
As chaves que o VOOL já guardava no chaveiro do macOS em um Mac continuam legíveis lá. Uma chave é usada
a partir da sua máquina para chegar ao seu provedor. Ela não é enviada à Parad0x Labs, e não há nenhum
endpoint operado pela Parad0x no caminho do pedido.

{% hint style="info" %}
O arquivo criptografado é protegido pelas permissões de arquivo da sua conta de usuário, não por uma senha
separada. Mantenha a criptografia do disco ativada (FileVault em um Mac) para que um disco perdido ou roubado não
exponha as suas chaves.
{% endhint %}

## Plugins

Um plugin roda com o acesso do ambiente de execução. É isso que torna os plugins úteis e o que faz valer a
pena lê-los antes de instalar. Veja [Habilidades e plugins](../guides/skills-and-plugins.md).

## Relatar um problema

Relate problemas de segurança de forma privada pelo
[GitHub Security Advisories](https://github.com/Parad0x-Labs/vool/security/advisories/new),
nunca em uma issue pública. Inclua a versão, a plataforma e os passos para reproduzir.

{% hint style="warning" %}
Os apps para macOS têm assinatura ad hoc e não são notarizados; o arquivo tar do Linux não é assinado. Veja o
[Status das versões](release-status.md).
{% endhint %}
