---
translation: machine
source_sha256: 021137e6424e28cf4af2913b10198aa3e59fb7d63a002949eeea0837375436a8
---
# Verificações de complementos

Complementos mantém separados explorar, inspecionar e instalar. Uma verificação descreve bytes específicos;
não é uma garantia de segurança, um endosso do publicador, uma permissão de execução nem uma aprovação da carteira.

Explorar lista primeiro as habilidades do próprio VOOL, incluindo 75 habilidades do VOOL que vêm com o app (22 delas
usam ferramentas do VOOL, que só restringem o que o VOOL já oferece), depois os complementos revisados do catálogo. Códice de habilidades lista primeiro as habilidades da equipe do VOOL, acima das
escolhas editoriais e das habilidades em destaque.

## Habilidades feitas pelo VOOL: a verificação local

As 75 habilidades feitas pelo VOOL vêm dentro do app, cada uma fixada por SHA-256 no diretório do próprio VOOL e
revisada quando o VOOL é lançado. Para elas, **Adicionar esta habilidade** executa só a verificação local do VOOL: sem chave,
sem cota de verificação e sem nenhum pedido de rede.

1. O VOOL lê a habilidade e a licença da cópia do próprio app e as recusa, a menos que o SHA-256 delas
   corresponda ao valor fixado (*O arquivo desta habilidade do VOOL não corresponde à cópia que veio com o VOOL, por isso ela não foi adicionada.*).
2. A verificação local do VOOL lê a habilidade em busca de instruções arriscadas e mostra o resultado. Um resultado limpo
   pode ser adicionado com **Adicionar e ligar**; qualquer coisa que ela sinalizar precisa do seu OK separado e explícito para
   aquela versão exata. **Rejeitar** a descarta.
3. Depois, a instalação segue o mesmo ciclo de vida das outras habilidades: os arquivos exatos são instalados,
   verificados e ativados. A habilidade instalada é conferida com o seu SHA-256 fixado toda vez que é
   carregada; um arquivo alterado a torna indisponível.

A verificação local é uma leitura estática de bytes fixos, não uma garantia de segurança nem de qualidade das respostas.
Habilidades de outros criadores nunca seguem esse caminho.

## Habilidades públicas

Escolha uma habilidade pública do GitHub em uma revisão fixa e aprove o download do conteúdo dela. O VOOL a verifica
neste dispositivo. Sem um plugin verificador ativado, a página explica
que as verificações ficaram neste dispositivo. Nenhuma chave de verificador é necessária nesse caminho.

Um plugin verificador ativado também recebe o conteúdo da habilidade pública quando uma verificação é solicitada. Isso
pode consumir a cota do serviço dele. Conversas locais, arquivos privados do espaço de trabalho e outras credenciais não
são enviados. Salvar uma chave e abrir Complementos não fazem nenhum teste de conexão nem pedido de verificação. Não há
novas tentativas automáticas de pedidos.

Leia as descobertas e aceite a instalação separadamente. A validação do relatório exige cobertura completa
e descobertas válidas. Um relatório aprovado não tem descobertas nem violações de política. Uma aceitação de risco
oferecida separadamente vincula a versão revisada; ela nunca muda o relatório nem concede mais acesso.
Revisões expiradas, bytes preparados alterados, links e pastas de destino em conflito são recusados.
Os recibos vinculam a distribuição instalada completa. Arquivos alterados retiram a disponibilidade em execução.
As orientações de uma habilidade são inseridas inteiras ou omitidas explicitamente quando não cabem no orçamento de prompt.

## Verificadores opcionais

Um verificador remoto é opcional. Sem nenhum plugin verificador ativado, a verificação do próprio VOOL neste dispositivo
decide se uma habilidade pode ser instalada; versões anteriores exigiam uma verificação remota em vez disso.

Para adicionar um, instale um plugin verificador do criador dele pela seção Extensões, salve a sua própria
chave, revise o acesso e ative-o. A declaração dele indica um caminho para um teste de conexão explícito
e um caminho para verificações solicitadas, e o transporte nativo compartilhado de plugins faz esses pedidos.
O VOOL não tem cliente verificador embutido nem configuração central de chave de verificador, e não oferece nenhum plugin verificador
próprio. Uma chave do Eyebrow salva por uma versão antiga do VOOL continua neste computador e não é usada. Os
ids `vool-plugin-eyebrow` e `vool-plugin-usepod` são reservados; outro pacote que reivindique
um deles é recusado antes da revisão.

A verificação do próprio VOOL neste dispositivo sempre roda. O relatório de um plugin verificador é adicionado ao lado dela:
se qualquer uma das duas encontrar um problema, a instalação é bloqueada, e o relatório de um plugin precisa indicar o conteúdo exato
que foi verificado. Um plugin verificador nunca pode aprovar uma instalação sozinho.

Um verificador ativado com chave ausente ou rejeitada falha de forma clara. O VOOL nunca substitui em silêncio
essa verificação remota solicitada por um resultado local aprovado. Tempos esgotados, erros do serviço, redirecionamentos,
respostas grandes demais e relatórios incompletos deixam a instalação sem aceitação. Um pedido que esgotou o tempo
ainda pode ter consumido cota do serviço.

## Pacotes executáveis e revisão da loja

Os plugins usam uma revisão completa do pacote, de uma pasta local ou de um pacote público do GitHub fixado, separada do
importador de habilidades só de texto. A instalação não executa nenhum código do pacote e o mantém desativado. Plugins da loja
também exigem o recibo assinado existente e o resumo criptográfico da distribuição completa. A ativação exige uma
ficha de acesso separada que cubra ferramentas, efeitos, hosts exatos, espaços de credenciais e servidores incluídos.

O código executável recebe só as próprias chaves declaradas na invocação. Ele não pode ler os espaços de outro
plugin, as chaves de modelos pagos do proprietário nem tem autoridade sobre assinatura, limite, aprovação ou recibos da carteira.
A saída dele continua não confiável. Bytes alterados retiram o consentimento; desativar interrompe as chamadas. Um confinamento
não suportado recusa a execução. Veja [Criar plugins](../../../guides/plugin-authoring.md) para os limites de execução.

Se o VOOL não conseguir ler os registros de plugins dele, os pacotes sem registro deixam de oferecer habilidades, exceto
as que vêm com o VOOL, e o arquivo danificado é mantido ao lado do novo. Se ele não conseguir ler
as suas escolhas de ligar e desligar, todo plugin conta como desligado até você ligar algum de novo.

## API local e transporte

`GET /api/addons` lê o inventário. Alterações em `POST /api/addons` exigem acesso local do proprietário,
JSON, pedidos do navegador da mesma origem e campos específicos da ação. As portas de ligar/desligar e de ciclo de vida dos plugins
(`POST /api/plugins/enable`, `POST /api/plugins/lifecycle`) aplicam a mesma regra de JSON e de
mesma origem. Downloads de fontes públicas usam
revisões exatas e blobs verificados de tamanho limitado. Serviços nativos exigem hosts HTTPS públicos declarados,
validam TLS, fixam os endereços públicos resolvidos, recusam redirecionamentos, limitam as respostas e param quando
o plugin é desativado.

Serviços HTTPS locais simulados podem verificar o transporte e o tratamento de erros sem chamadas pagas. Eles não
comprovam a confiabilidade real do serviço, a cobrança, a competência do modelo nem a instalação dentro de outros
aplicativos. Isso exige uma verificação separada.
