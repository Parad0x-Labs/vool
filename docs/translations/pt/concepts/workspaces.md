---
description: Um espaço de trabalho é a pasta do projeto em que o VOOL trabalha. Fora dela, o VOOL pode ler as pastas visíveis da sua pasta pessoal e alterar arquivos somente em Mesa, Downloads e Documentos.
translation: machine
source_sha256: 33b648a0a55f99b1c96365553942911862672eafa55e94bd45f18ce566d5fcea
---

# Espaços de trabalho

Um espaço de trabalho é a pasta do projeto em que uma conversa trabalha. O VOOL lê e altera arquivos ali,
perguntando antes sempre que o seu modo de operação disser que deve (veja [Ferramentas](tools.md)).

## Adicionar e remover

Selecione uma pasta com **Escolha uma pasta de projeto…** na conversa, ou com **+ Projeto** na
barra lateral do chat. Excluir um projeto faz o VOOL esquecer o que aprendeu nele e devolve as conversas dele
para Geral. Os seus arquivos não são alterados.

## As suas outras pastas

Fora do espaço de trabalho, as ferramentas de arquivos do VOOL também podem acessar a sua pasta pessoal, com limites fixos:

| Pastas | O que o VOOL pode fazer |
| --- | --- |
| Mesa, Downloads, Documentos | Ler e alterar arquivos, perguntando antes conforme o seu modo de operação indicar |
| Imagens, Filmes (Vídeos no Linux), Música | Somente leitura: listar, procurar, abrir e medir, nunca alterar |
| As outras pastas visíveis da sua pasta pessoal, como `~/Projects` | Somente leitura |
| Pastas ocultas (como `~/.ssh`), dados de apps e do sistema (`Library`, `Applications`, `AppData`), a pasta de dados do próprio VOOL | Nunca |

O VOOL só olha nessas pastas quando um pedido precisa, por exemplo “encontre o meu PDF do imposto de renda” ou
“qual vídeo em Filmes é o maior”. Ele não indexa o seu disco em segundo plano.

## Dicas para delimitar o escopo

* Aponte o espaço de trabalho para um projeto, não para a sua pasta pessoal.
* Guarde arquivos de credenciais, material de chaves e cofres de senhas em um gerenciador de senhas ou em uma
  pasta oculta, não soltos em Mesa, Downloads, Documentos ou em uma pasta de projeto.
* Use um projeto separado para cada trabalho, para que o contexto de um não chegue a outro.

{% hint style="warning" %}
Tudo o que o VOOL lê, na pasta do projeto ou nas pastas da sua pasta pessoal, pode ser incluído no que
é enviado ao seu provedor quando um modelo na nuvem está selecionado. Delimite o escopo com cuidado.
{% endhint %}
