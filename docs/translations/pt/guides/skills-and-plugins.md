---
description: Amplie o VOOL com habilidades e plugins.
translation: machine
source_sha256: a86860b8624e3c97c81e813ec92e3998c687a87d7bc43b7960c489cdcd309196
---

# Habilidades e plugins

Uma **habilidade** é um conjunto empacotado de instruções para uma tarefa recorrente. Um **plugin** adiciona
novas ferramentas ao ambiente de execução.

## Incluídos versus instalados externamente

O VOOL vem com uma **biblioteca de habilidades nativas** — as habilidades que vêm com o próprio app — e,
nas versões atuais, com o plugin **VOOL Database**. Você também pode instalar habilidades e plugins
seus.

| | Incluídos (vêm com o VOOL) | Instalados externamente |
| --- | --- | --- |
| Onde ficam | Dentro do app | A sua pasta de plugins (definida em Configurações) |
| Atualizações | Com o app | Quando você atualiza o pacote |
| Podem ser desativados | Sim — individualmente | Sim — individualmente |
| Concedem permissões | **Não** | **Não** |

Duas regras valem para as duas origens:

* **Habilidades ensinam; nunca concedem.** As ferramentas declaradas por uma habilidade são um sinal de
  *restrição* (o orçamento de ferramentas do fluxo de trabalho), nunca uma concessão de nada. O controlador de
  permissões não lê habilidades.
* **Um único inventário.** O catálogo que o console mostra e os pacotes que o ambiente de execução carrega são a
  mesma lista. Uma habilidade aparecer em uma lista não prova que ela pode ser carregada — cada entrada traz o seu
  estado atual (disponível, desativada, pré-requisito ausente, contrato inválido) e o motivo.

## Encontrar habilidades

Habilidades e plugins lista primeiro as habilidades do próprio VOOL. **Explorar** vem com 53 habilidades do VOOL
prontas. Elas são só de texto: cada uma é um conjunto de instruções incluído no app, então
Explorar as lista sem nenhuma consulta online. As habilidades do VOOL que usam ferramentas do VOOL aparecem na
aba **Códice de habilidades**, onde as habilidades da equipe do VOOL vêm primeiro. Revise uma habilidade antes
de ativá-la.

## Estados de disponibilidade

Uma habilidade pode aparecer como indisponível com um motivo tipificado: um pré-requisito ausente (por exemplo, a
habilidade de banco de dados precisa do plugin de banco de dados ativado), um recurso indisponível nesta máquina,
um contrato inválido ou desativada por você. Os motivos aparecem em cada entrada, e a desativação é
por entrada e persiste entre reinicializações.

## Identidades duplicadas

Se dois pacotes declaram a mesma identidade de plugin (um pacote incluído e uma cópia instalada, por exemplo),
o incluído é mantido de forma **determinística** e a cópia descartada é informada no
catálogo — nunca ocultada em silêncio.

## Instalar os seus

Instale pelo catálogo do app ou aponte o VOOL para uma pasta de plugins sua. Os pacotes trazem
um manifesto (`plugin.json`) que declara as ferramentas deles; um manifesto cujas declarações de permissão não
correspondem à classe de comportamento declarada é recusado ao carregar.

{% hint style="danger" %}
Revise o pacote inteiro antes da verificação. Uma revisão é uma evidência sobre bytes específicos,
não um certificado de segurança nem uma concessão de permissões.
{% endhint %}

## Aprovação de pacotes na v0.7

A verificação cobre todos os arquivos e diretórios do pacote, incluindo executáveis de handlers, bibliotecas de
terceiros incluídas no pacote, arquivos de lock de dependências, referências, recursos e bits de modo executável.
Adicionar, remover, renomear ou alterar qualquer um deles invalida a aprovação. Links simbólicos, hard links,
arquivos especiais, modos privilegiados, leituras instáveis e distribuições grandes demais são recusados. Os
limites de captura atuais são 4.096 entradas, 64 MiB por arquivo e 128 MiB por pacote. Não há diretórios de cache
ou de estado ignorados dentro de um pacote.

Aprovações armazenadas antes que contêm só metadados não ativam pacotes nesta versão.
Revise a distribuição completa, verifique-a de novo explicitamente e depois ative-a. As atualizações continuam
desativadas até serem verificadas e ativadas; a aprovação nunca é migrada em silêncio.
Complementos gerenciados também precisam de uma nova revisão autenticada da loja quando um recibo antigo
não vincula a distribuição completa; a verificação comum não pode substituir esse recibo.

`requires` indica as dependências de plugins instaladas. A verificação fixa os resumos criptográficos de
aprovação recursivos delas; cada dependência já precisa estar verificada e ativada. Uma dependência alterada,
atualizada, desativada ou revogada torna indisponíveis os pacotes que dependem dela, inclusive por meio de
ferramentas em cache. A admissão verifica de novo o estado de ativação e de revogação de todo o fechamento
capturado depois do cálculo dos hashes. Verificar de novo uma dependência não aprova em silêncio os novos bytes
dela para o pacote pai: revise e verifique de novo cada pai também. Cada `approval_digest` vincula o resumo do seu
pacote e as aprovações das dependências, então uma dependência transitiva alterada muda essa aprovação mesmo
quando os arquivos do próprio pai continuam idênticos. Uma aprovação com dependências cíclicas não pode ser
ativada. O fechamento de dependências é limitado a 64 pacotes.

Cada invocação registrada verifica de novo o estado do ciclo de vida e captura cópias privadas somente leitura
do pacote e do fechamento de dependências fixado. O contrato da ferramenta precisa corresponder ao manifesto
capturado. O handler registrado é iniciado a partir dessa captura; editar o código-fonte depois da captura não
pode substituir esse handler nem os arquivos incluídos com ele. As observações de execução incluem os resumos do
pacote, da aprovação recursiva e das dependências. Pacotes registrados exigem confinamento real do kernel mesmo
que um chamador local de baixo nível tenha escolhido uma substituição mais fraca.
Hosts sem captura segura de descritores que não segue links (no-follow) ou sem confinamento do kernel recusam
pacotes registrados. Os tempos limite das invocações ficam limitados a 1–300 segundos; o grupo de processos da
própria invocação é encerrado antes de a cópia de execução ser removida.

Mantenha a saída persistente, os caches e os logs em `PLUGIN_SCRATCH`, não ao lado do código executável.
Ferramentas somente leitura não recebem permissão de gravação. As outras ferramentas podem gravar na área
temporária, mas não podem modificar a própria cópia de execução. `VOOL_PLUGIN_DEPENDENCIES` é um mapeamento JSON
das identidades de dependência declaradas para os diretórios de dependências capturados. Inclua as outras
bibliotecas de terceiros e os arquivos de lock delas dentro do pacote; um arquivo de lock sozinho não atesta um
ambiente externo. O interpretador do host e as bibliotecas do sistema continuam fazendo parte da plataforma
confiável, em vez de serem representados como bytes revisados de pacotes de terceiros. Esta verificação de
integridade não certifica o comportamento, não atesta código arbitrário baixado ou carregado de fora da
distribuição aprovada, não intermedeia todo efeito interno nem substitui a aprovação nativa da carteira.

## Escrever uma habilidade

Uma habilidade é uma pasta com um arquivo de instruções e os arquivos a que ele faz referência:

```text
my-skill/
├── SKILL.md
└── templates/
```

Crie um rascunho no app (`skill.create` o prepara sem ativar nada), valide-o e depois instale-o para ativá-lo —
três etapas separadas de propósito, para que criar uma habilidade nunca seja uma mudança de comportamento
implícita. O catálogo e o formato de criação ainda estão sendo finalizados para o lançamento público.
