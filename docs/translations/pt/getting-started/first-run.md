---
description: O que o assistente de IA VOOL pergunta na primeira vez que você o abre, e o que ele não faz.
translation: machine
source_sha256: cedf20410d47a2d277e5522bb3e55169854a91a267d71193a762e072e7f8e47e
---

# Primeira execução

A primeira execução faz quatro perguntas curtas. Você pode pular qualquer uma delas e mudar todas as respostas
depois. Nada é enviado a lugar nenhum enquanto você responde, exceto a verificação de uma chave da nuvem com o
provedor dela, se você adicionar uma.

## O que acontece

1. **Onde ele deve pensar?** Neste computador com um modelo local, ou online com uma chave de um
   provedor que você paga. Veja [Conectar um modelo](connect-a-model.md).
2. **Escolha uma pasta.** A pasta do projeto em que o VOOL trabalha. Veja
   [Espaços de trabalho](../../../concepts/workspaces.md).
3. **O que ele pode fazer sozinho?** Com que frequência o VOOL para e pergunta a você antes de mudar qualquer
   coisa. Veja [Ferramentas](../../../concepts/tools.md).
4. **Como ele deve chamar você?** Opcional. Um nome ou apelido basta, e ele fica neste
   computador.

Em seguida, o indicador de modo mostra Local, Auto ou o nome do provedor na nuvem, e reflete o modelo
selecionado no momento.

**Certifique um modelo local uma vez.** Antes que um modelo local possa responder, abra
**Configurações → Modelos e provedores → Modelos locais** e pressione **Certify** para ele. Isso executa uma
verificação rápida na sua máquina.

## O que o VOOL não faz na primeira execução

* Ele não cria uma conta.
* Ele não exige uma chave da nuvem. Um modelo local basta para usar o app.
* Ele não indexa o seu disco. Ele só lê arquivos quando um pedido precisa deles; veja
  [Espaços de trabalho](../../../concepts/workspaces.md) para saber quais pastas ele pode acessar.

{% hint style="info" %}
Você pode escolher outra pasta de projeto a qualquer momento com **+ Projeto** na barra lateral do chat.
{% endhint %}

## Próximo passo

Continue em [Conectar um modelo](connect-a-model.md).
