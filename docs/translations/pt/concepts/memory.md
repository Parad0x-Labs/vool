---
description: Como o VOOL se lembra das coisas entre sessões e onde essa memória fica.
translation: machine
source_sha256: 65af63e755076fe33675d7a5d6afa7f0c5664b77f716ffc0dc1f0f358e67f667
---

# Memória

A memória é a forma como o VOOL leva fatos de uma sessão para outra. Ela fica guardada em arquivos na sua
máquina, na pasta de dados do VOOL, em um formato legível.

## Quão bem ela lembra

Em 150 perguntas nunca usadas antes do benchmark de conversas longas LoCoMo, a memória incluída no
VOOL 0.7 acertou 123 (82,0%). Na rodada de comparação direta com as mesmas perguntas, com o mesmo
modelo respondendo pelos três, o VOOL acertou 124 (82,7%), o OpenClaw 107 (71,3%) e o Hermes 94 (62,7%),
enviando de 3,5 a 5,4 vezes menos tokens por pergunta. Cada execução está publicada:
[benchmarks de memória](https://github.com/Parad0x-Labs/vool/tree/main/docs/benchmarks).

## Propriedades

* **No disco, à vista.** A memória são arquivos que você pode ler. `MEMORY.md` é a face
  legível; os fatos por trás dela ficam em um arquivo de entradas ao lado.
* **Local.** A memória não é sincronizada com nenhum servidor.
* **Um armazenamento por pasta de dados do VOOL.** A memória não é dividida por espaço de trabalho — ela é mantida
  para a instalação inteira, então não é uma barreira de isolamento por projeto.

## Como gerenciá-la

As notas que você mesmo adiciona ao `MEMORY.md` são preservadas. Os fatos que o VOOL registrou são apagados
pela operação de esquecer do VOOL, não excluindo o arquivo: esquecer reescreve o
armazenamento de entradas e o espelho em um só passo e relê os dois para confirmar que o texto
sumiu. Excluir só o `MEMORY.md` não é apagar — o arquivo de entradas ainda contém os
fatos, e o espelho é reconstruído a partir dele.

No modo nuvem, a memória relevante pode fazer parte do contexto enviado ao provedor, nos
mesmos termos que qualquer outro contexto. Veja [Local e nuvem](local-and-cloud.md).

Esquecer em uma conversa também apaga as palavras do histórico de execução dessa conversa no banco de dados do
ambiente de execução: os registros de turnos, os pontos de controle, as tentativas, os planos de tarefas, as
respostas finalizadas (com as cópias que o apagamento de respostas varre), o conhecimento candidato, os relatórios
de contexto, as notificações, os resultados de pesquisa que a conversa produziu e as mensagens fixadas que os
citavam. Os registros dos quais outros registros dependem continuam existindo com as palavras esquecidas em
branco, junto com qualquer resumo criptográfico guardado ao lado deles; os demais são excluídos. Um turno que ainda
estava rodando quando o pedido de esquecer chegou não consegue gravar as palavras de volta: os eventos dele ficam
em branco e, quando ele termina, os registros dele são apagados de novo. Em seguida, o log de escrita antecipada
do banco de dados é consolidado para que cópias antigas não fiquem no arquivo `-wal`. Os registros de outras
conversas não são alterados. Uma mensagem na fila que ainda não foi enviada é mantida, e então o pedido de esquecer
avisa que não pôde ser aplicado por completo. Uma tabela que nenhuma etapa de apagamento conhece é citada na
resposta, que avisa que tentar de novo não vai limpá-la.

As palavras são comparadas sem diferenciar maiúsculas de minúsculas, e um dígito escrito em outro sistema de
escrita conta como esse dígito (um “１２” de largura total é 12). Um número esquecido só corresponde onde aparece
como um número independente: as letras e os dígitos escritos junto com ele não podem conter nenhum outro
dígito, e ele não pode continuar através de `.`, `,`, `:`, `/`, `-` ou `_` até outro dígito. Esquecer “12” apaga
“código 12”, “12kg” e “B12”, mas não “quarto 312”, “3.12”, “12.5”, “12:30” nem “2026-10-12”, e esquecer
“200” não apaga “1,200”. No histórico de execução, um número solto que o ambiente de execução contou ou
cronometrou (como um valor de `elapsed_ms` ou `step_count`) não é considerado.

Os leitores de memória também verificam uma autoridade de esquecimento durável. Uma pasta de memória nova a cria
antes de aceitar gravações. Um armazenamento semântico mais antigo é atualizado no lugar só quando
nenhum apagamento anterior está registrado; as memórias que ele conserva continuam disponíveis. Uma autoridade
existente que não pode ser lida, uma autoridade ausente com uma testemunha persistida ou uma barreira de
esquecimento registrada fazem os leitores reterem a memória afetada. Excluir os arquivos da autoridade
não é uma forma de recuperar conteúdo esquecido.

Um diário de apagamento pendente mantém o seu token para a recuperação. Assim que a revogação
permanente e delimitada existe, a conclusão remove o token redundante do diário, mantendo
a identidade e o status da operação. A autoridade de revogação permanece.
