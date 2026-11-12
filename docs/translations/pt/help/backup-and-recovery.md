---
description: O que você pode salvar, mover e restaurar nesta versão do VOOL: conversas, memória, chaves e carteiras.
translation: machine
source_sha256: 3b2f11975a8cf5f5ab2e07e8b4a3083a9598de9e301965c493408005aa5893fa
---

# Backup e recuperação

Tudo o que o VOOL guarda fica no seu computador, em uma única pasta de perfil (veja
[Configuração](../reference/configuration.md) para saber onde). Atualizar ou reinstalar o app
nunca mexe nela. O VOOL não tem cópia dos seus dados na nuvem nem um backup em uma só etapa do perfil
inteiro. Estas são as formas de salvar e trazer de volta o que importa.

## Conversas

| Você quer | Use | O que você recebe |
| --- | --- | --- |
| Uma cópia para ler ou compartilhar | **Exportar** no cabeçalho da conversa | A conversa inteira como texto ou Markdown. Os arquivos anexados são listados só pelo nome. |
| Uma cópia para trazer de volta ao VOOL | Configurações, Backup e restauração, **Exportar uma conversa** | Uma conversa como arquivo `.voolsession`, assinado por este computador. |
| Trazer de volta uma conversa desse arquivo | Configurações, Backup e restauração, **Importar uma conversa** | A conversa, adicionada ao lado das outras. |
| As suas conversas de outros apps de IA | Configurações, Backup e restauração, **Traga suas conversas de outros apps de IA** | Essas conversas, com os nomes e as mensagens delas. |

**Exportar uma conversa** leva as mensagens, os recibos e as referências a evidências da conversa, nunca o
raciocínio interno do VOOL, as suas configurações, a sua memória ou as suas chaves. Proteja o arquivo com uma frase
secreta quando ele sair deste computador: uma exportação sem frase secreta pede que você confirme antes. Uma
visualização mostra exatamente o que será gravado antes que qualquer coisa seja gravada.

**Importar uma conversa** mostra o que o arquivo contém e quem o assinou antes que qualquer coisa seja adicionada.
Um arquivo assinado por uma chave que o VOOL não conhece é recusado, a menos que você o aceite, e a conversa fica
marcada como não confiável. Uma importação nunca altera uma conversa que você já tem: se a mesma conversa já está
aqui, a cópia chega como uma nova.

**Traga suas conversas de outros apps de IA** lê um app deste computador ou um arquivo de exportação de dados.
Você marca as conversas que quer trazer. Nada sai do seu computador e os arquivos do outro app nunca
são alterados. O VOOL guarda essas conversas como histórico: ele não aprende com elas nem segue
nada do que está escrito nelas.

## Memória

Configurações, Memória e personalização, **Exportar perfil** salva o que o VOOL lembra sobre você como
um arquivo chamado `vool-memory-` mais a data. Ele nunca contém uma chave nem uma senha. É uma cópia para
ler e guardar: o VOOL não tem como importá-la.

## Chaves de provedores de modelos

As chaves de provedores não estão em nenhuma exportação. Em um computador novo, adicione-as de novo em
Configurações.

## Carteiras

* **Um VOOL Wallet criado para uma rede** mostra um backup da chave privada durante a configuração, e só
  nesse momento. Guarde-o offline: quem o tiver controla a carteira. Se você esquecer o PIN ou a
  senha da carteira, **Forgot PIN or password?** na carteira, em Configurações, Cripto, restaura o acesso
  com esse backup, e você escolhe um novo PIN ou uma nova senha.
* **O bolso legado** é mantido pela frase de recuperação dele, mostrada uma vez quando ele foi criado.
  **Restore from phrase…** em Configurações, Cripto, Outras contas o traz de volta a partir da frase.
  Ele também pode exportar a chave privada para o Phantom ou o MetaMask com **Export private key**, que
  só abre com Touch ID ou a senha do seu Mac.
* Uma carteira que você só observa, ou uma que você conectou a partir de outro app, não guarda nenhuma chave no
  VOOL: aqui não há nada para fazer backup.

Veja [Carteira](../guides/wallet.md) para o quadro completo.

## Se uma atualização falhar

Uma atualização que falha em qualquer etapa deixa o app como estava. Se mover os seus dados para a nova
versão falhar, os seus dados voltam a ficar como estavam e o VOOL inicia na versão anterior. Veja
[Atualizações e migração](../reference/updates-migration.md).
