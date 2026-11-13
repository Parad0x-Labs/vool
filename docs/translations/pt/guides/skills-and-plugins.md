---
description: Amplie o VOOL com habilidades e plugins.
translation: machine
source_sha256: 4dd2a317a7b10ee024616f6e0c27bd73087c44334f29e2c5f60f69773f4e54e8
---

# Habilidades e plugins

Uma **habilidade** é um conjunto empacotado de instruções para uma tarefa recorrente. Um **plugin** adiciona
ferramentas, um provedor de modelos ou um verificador de complementos. Plugins também podem trazer habilidades.

## Incluídos versus instalados externamente

O VOOL vem com uma **biblioteca de habilidades nativas** — as habilidades que vêm com o próprio app — e,
nas versões atuais, com o plugin **VOOL Database**. Você também pode instalar habilidades e plugins
seus.

| | Incluídos (vêm com o VOOL) | Instalados externamente |
| --- | --- | --- |
| Onde ficam | Dentro do app | A sua pasta de plugins (gerenciada pela janela de plugins do VOOL, não pelas Configurações) |
| Atualizações | Com o app | Quando você atualiza o pacote |
| Podem ser desativados | Ainda não há botão nas janelas do app | Sim — pela janela de plugins |
| Concedem permissões | **Não** | **Não** |

Duas regras valem para as duas origens:

* **Habilidades ensinam; nunca concedem.** As ferramentas declaradas por uma habilidade são um sinal de
  *restrição* (o orçamento de ferramentas do fluxo de trabalho), nunca uma concessão de nada. O controlador de
  permissões não lê habilidades.
* **Um único inventário.** O catálogo que o console mostra e os pacotes que o ambiente de execução carrega são a
  mesma lista. Uma habilidade aparecer em uma lista não prova que ela pode ser carregada — cada entrada traz o seu
  estado atual (disponível, desativada, pré-requisito ausente, contrato inválido) e o motivo.

## Encontrar habilidades

Habilidades e plugins lista primeiro as habilidades do próprio VOOL. **Explorar** vem com 75 habilidades do VOOL
prontas. Cada uma é um conjunto de instruções incluído no app, então
Explorar as lista sem nenhuma consulta online. 22 delas usam ferramentas do VOOL (seus e-mails, notas, arquivos, PDFs, carteira e
mais) nos turnos em que são escolhidas: as ferramentas de uma habilidade só restringem o que o VOOL já oferece para
o seu pedido, uma ferramenta desligada continua desligada, e cada chamada pede permissão como sempre. A seção
**Fonte e detalhes técnicos** dela lista as ferramentas que ela usa. A aba **Códice de habilidades** lista primeiro as
habilidades da equipe do VOOL. Revise uma habilidade antes de ativá-la.

Como uma habilidade de Explorar é adicionada depende de quem a fez:

* **Habilidades feitas pelo VOOL** (as 75 acima): pressione **Adicionar esta habilidade**. O VOOL confere no seu
  computador que o arquivo é exatamente aquele com que ele foi distribuído e o lê em busca de instruções
  arriscadas; depois mostra o resultado. Nenhuma chave é necessária e nada é enviado para lugar nenhum.
  Se o arquivo não corresponder, o VOOL o recusa e avisa.
* **Habilidades de outros criadores** (outros publicadores, ou um link do GitHub que você cola): pressione
  **Descarregar e verificar**. O VOOL baixa a habilidade pública em uma versão fixa e a verifica no
  seu computador. Se você ativou um plugin verificador, ele também recebe o conteúdo da habilidade e
  o relatório dele é adicionado ao lado do relatório do VOOL. Leia o resultado e então escolha se vai adicionar a
  habilidade: se alguma das verificações relatar um problema, o VOOL só a adiciona depois que você aceitar os riscos daquela
  versão. Nenhuma chave é necessária, a menos que o seu plugin verificador peça uma.

Você também pode criar a sua própria habilidade no chat sem chave nenhuma: veja
[Crie a sua própria habilidade](../../../guides/make-your-own-skill.md).

Você também pode perguntar em qualquer chat: *quais habilidades eu tenho?* lista as habilidades, e *como adiciono uma
habilidade?* explica as duas formas.

## Ligar ou desligar uma habilidade

Abra **Habilidades e plugins**, depois **Os meus complementos** e depois **Habilidades**. Cada habilidade mostra **Ativado** ou
**Desativado** com um botão **Ativar** ou **Desativar**. Uma habilidade que você desliga não é usada em nenhum chat
até você ligá-la de novo. Os arquivos dela são mantidos.

O cartão **Built-in skills** em **Extensões** não tem botão próprio: ligue ou desligue as habilidades dele
uma a uma em **Habilidades**.

## Ver qual habilidade moldou uma resposta

Quando as instruções de uma habilidade fizeram parte de como o VOOL escreveu uma resposta, uma linha curta abaixo da
resposta informa isso, por exemplo **Habilidade usada: Browser**. Ela aparece logo depois da resposta, enquanto
o chat está aberto; respostas carregadas mais tarde do seu histórico ainda não a mostram. Regras de estilo de resposta
que acompanham a maioria das respostas não são citadas ali.

## Habilidades para construir o VOOL

A biblioteca também guarda as habilidades com que a equipe do VOOL constrói o VOOL: revisão de código, execução de testes,
verificações de lançamento, reparo do pipeline e afins. Elas não aparecem no chat nem na página de habilidades,
e não orientam as respostas do dia a dia. Desenvolvedores podem mostrá-las e usá-las iniciando o VOOL com
`VOOL_DEVELOPER_SKILLS=1`.

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

## Plugins em Complementos

Abra **Complementos → Os meus complementos → Extensões**. Revise uma pasta local ou uma pasta pública do GitHub em
um commit exato. A instalação captura o pacote completo e o mantém desativado. Plugins assinados
da loja usam a cadeia de registro existente e também são instalados desativados. Revise a ficha de acesso
separadamente antes de ativar ferramentas ou serviços. Salvar uma chave de serviço não gera nenhum pedido;
testes de conexão e atualizações da lista de modelos são ações explícitas.

Desativar remove as ferramentas e interrompe as chamadas em andamento. Desinstalar primeiro desativa, remove os arquivos próprios
e a área temporária, apaga por padrão as chaves salvas e mantém o histórico. Atualizações e reinstalações
exigem uma nova revisão e começam desativadas. Uma atualização de outro repositório ou de outro publicador
não mantém as chaves salvas. UsePod e Eyebrow não fazem mais parte do VOOL, e o VOOL
não oferece plugin para nenhum dos dois. Uma chave salva do UsePod ou do Eyebrow continua neste computador e o VOOL
não a usa; Complementos avisa isso até você fechar o aviso. Chats que usavam um modelo do UsePod
ficam sem modelo selecionado depois da atualização, e Auto não muda para um modelo na nuvem nesses
chats; um aviso informa isso até você fechá-lo.

Veja [Criar plugins](../../../guides/plugin-authoring.md) e [Verificações de complementos](addon-security.md).

## Aprovação completa de pacotes

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
ambiente externo. O interpretador do ambiente de execução e as bibliotecas do sistema continuam fazendo parte da plataforma
confiável, em vez de serem representados como bytes revisados de pacotes de terceiros. Esta verificação de
integridade não certifica o comportamento, não atesta código arbitrário baixado ou carregado de fora da
distribuição aprovada, não intermedeia todo efeito interno nem substitui a aprovação nativa da carteira.

No macOS, o código dos plugins roda no sandbox do macOS. As chaves só chegam a um handler pela entrada
privada dele, nunca pelo ambiente dele, porque qualquer programa do mesmo usuário, inclusive o código de plugins,
pode ler o ambiente de inicialização de outro programa em um Mac. Um servidor de conexão que precisa de uma chave no seu
ambiente só roda no ambiente de execução Linux isolado opcional, que você mesmo provisiona;
o VOOL nunca inicia uma VM nem baixa uma imagem. Sem ele, esse plugin fica desligado e explica o motivo.
O Linux usa bwrap, incluindo um namespace de processos privado e `/proc`.

No macOS, uma ferramenta que declara acesso à rede só consegue chegar a um proxy privado que o VOOL inicia para cada
chamada, e o proxy só se conecta aos hosts declarados. No Linux e no ambiente de execução isolado,
os processos de plugins não têm rede. Os pedidos nativos de provedores e verificadores usam o seu próprio
transporte HTTPS para hosts declarados em todas as plataformas.

## Escrever uma habilidade

* Para criar uma habilidade pedindo no chat, veja [Crie a sua própria habilidade](../../../guides/make-your-own-skill.md).
* Para escrever, verificar com lint, instalar e testar você mesmo um arquivo SKILL.md, veja [Crie e teste uma habilidade](../../../guides/build-and-test-a-skill.md).
