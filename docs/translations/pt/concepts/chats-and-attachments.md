---
description: Conversas, chats privados e os arquivos e textos longos que você cola nelas: limites, o que é guardado e o que pode ser recusado.
translation: machine
source_sha256: 1497140fef69088f8e0210aeb8a7f6abba6ae82d5132841ce397fe60fa1bab99
---

# Conversas e anexos

Uma conversa é um diálogo com o VOOL. As conversas ficam guardadas no seu computador. Uma conversa pode ser
vinculada a uma pasta de projeto, para que as ferramentas dela trabalhem naquela pasta (veja
[Espaços de trabalho](workspaces.md)), e você pode levar arquivos para ela como anexos.

## Anexar arquivos

Use **Anexar** ao lado da caixa de mensagem, ou arraste arquivos para ela. Cada arquivo aparece como um chip e
vai junto com a próxima mensagem que você enviar. A linha abaixo da caixa mostra os limites:

* Até 8 arquivos por mensagem, 10 MB por arquivo e 25 MB por mensagem.
* Arquivos de texto e de código, e imagens PNG, JPEG, GIF e WebP. PDF, Word (DOCX), arquivos compactados como
  ZIP e vídeo se juntam a eles quando este computador tem o que precisa para lê-los. O seletor de arquivos
  só oferece o que este computador consegue ler.

O que o modelo recebe também tem limite: até 120.000 caracteres de um arquivo e 240.000 de todos os
arquivos de uma mensagem. Um arquivo mais longo é cortado no fim de uma linha, e o registro diz onde. Se
o modelo que você usa não consegue ler imagens, o chip avisa, e a imagem não é enviada a ele.

Antes de uma imagem ser guardada, os detalhes ocultos dela são removidos: dados da câmera e de localização (EXIF,
XMP, IPTC) e texto guardado dentro de arquivos PNG e WebP.

Um arquivo que você escolhe é usado só naquela mensagem. Quando a resposta termina, o conteúdo do arquivo é
apagado e um recibo dele é mantido.

## Textos longos colados

Cole 4.000 caracteres ou mais e o VOOL guarda o texto como um documento da conversa em vez de uma
mensagem, exatamente como foi colado. **Visualização** mostra o começo dele. Um documento fica com a conversa
até você apagá-lo ou apagar a conversa. Perguntas posteriores na mesma conversa podem usá-lo: o VOOL passa ao
modelo até 60.000 caracteres dos documentos anteriores da conversa, começando pelos mais recentes, e diz onde
cortou.

## Quando um arquivo é recusado

Um arquivo recusado nunca é descartado em silêncio. O chip dele continua lá e diz o motivo, e a mensagem
não pode ser enviada até você remover o arquivo ou tentar de novo: *Antes de enviar, remova ou repita o anexo
que falhou.* Motivos comuns:

* **Grande demais, ou arquivos demais.** Remova alguns ou envie-os em duas mensagens.
* **Não é um tipo que o VOOL lê, ou não é o que o nome diz.** Um arquivo chamado `.txt` que na verdade é uma
  imagem ou um programa, ou um PDF para o qual este computador não tem leitor, é recusado com o motivo.
* **Uma credencial dentro.** Um arquivo ou texto colado que parece conter uma chave ou um token válido, como
  um bloco de chave privada ou um token de acesso, é recusado e nada é salvo. Remova o segredo
  e depois anexe ou cole de novo.

Essas recusas têm códigos curtos próprios, mostrados com a mensagem. Elas não estão no
[Livro de erros](../ERROR_BOOK.md), que cobre os outros erros do app.

## Chats privados

Diga “private chat”, “don't record this” ou “off the record”, e a conversa deixa de ser salva a partir
daquela mensagem: nem as mensagens, nem um resumo, nem a memória. Um aviso mostra *Chat privado: nada é
guardado*, e cada mensagem privada leva um pequeno fantasma. Mensagens privadas não voltam
depois de recarregar a página, e o VOOL não consegue lembrá-las depois. Diga “turn recording back on” ou
pressione **Voltar a ativar a gravação** para salvar a conversa de novo a partir da próxima mensagem. Um chat
privado continua privado depois que o VOOL reinicia.

## Levar uma conversa com você

**Exportar** no cabeçalho da conversa salva a conversa inteira como texto ou Markdown, com todas as mensagens;
os arquivos anexados são listados pelo nome, nunca pelo conteúdo. Para mover uma conversa para outro computador
ou guardar uma cópia assinada, veja [Backup e recuperação](../help/backup-and-recovery.md).
