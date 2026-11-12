---
description: A carteira opcional Crypto Pilot — redes, pagamentos x402, taxas DNA e recuperação, como esta versão realmente os oferece.
translation: machine
source_sha256: 46121a5ed26be175ddd3c66b269df356e074adbb57c8e9806461b80197613cc2
---

# Carteira

A carteira é **opcional e vem desativada por padrão**. Ela existe para que o VOOL possa guardar uma pequena
quantia de cripto para serviços de IA que cobram por pedido. Nada nela é necessário para usar o VOOL.

Esta página descreve o que a versão atual realmente suporta. A carteira está em fase **piloto**:
os recursos são adicionados trilha por trilha, e cada trilha abaixo diz exatamente o que faz.

## Ambientes de rede

Quando Cripto está ativado, o VOOL roda na **Mainnet**: Solana, Robinhood Chain, Base, Ethereum, BNB e
Arbitrum One.
A configuração normal nunca pede que você escolha um ambiente de rede.

* **Mainnet** — o padrão de toda instalação, incluindo instalações atualizadas que tinham contas antigas
  de redes de teste. Todos os limites de gastos, aprovações e recibos se aplicam como descrito abaixo.
* **Test networks** — Solana Devnet, Robinhood Chain Testnet e as outras trilhas de teste. Só para
  desenvolvedores: troque em **Configurações → Cripto → Developer options**.

Contas, saldos e recibos pertencem a um ambiente e nunca passam de um para o outro; as contas
do outro ambiente continuam listadas na própria rede. Um pedido para uma rede do outro
ambiente é recusado sem que nada seja criado, proposto ou assinado.

{% hint style="warning" %}
A documentação anterior dizia que a mainnet era impossível nesta versão. Isso era verdade antes do
piloto e não é mais: agora existem linhas de mainnet. O que continua verdade é mais restrito — veja as
seções de transferências e pagamentos abaixo para saber exatamente quais trilhas existem na mainnet.
{% endhint %}

## O que passa por cada trilha

* **Transferências movem moedas nativas** (por exemplo, SOL na Solana, ETH na Base, Ethereum ou
  Arbitrum One) e **USDT na Solana, Ethereum, BNB Smart Chain e Arbitrum One**.
  A cadeia faz parte de cada endereço de contato salvo e de cada aprovação. As transferências de tokens
  também precisam da moeda nativa daquela cadeia para as taxas; a cotação mostra os dois valores dentro dos
  seus limites.
* **USDC na Solana e na Base só está disponível para canais de pagamento.** Ele não é oferecido
  para envios comuns. A tela principal de saldo da carteira mostra as moedas nativas, não um portfólio
  completo de tokens.
* Cada transferência tem uma prévia (uma **cotação**: valor, teto de taxa, destinatário, rede) e precisa
  da sua aprovação explícita vinculada exatamente a essa prévia. Mude qualquer coisa e a aprovação fica
  anulada — nada é assinado.
* Um nome que corresponde a vários contatos ou endereços de carteira pede que você escolha. Revise o
  endereço e a cadeia exatos. Um endereço alterado ou um contato renomeado invalida a revisão anterior.
* Na mainnet, só envia um VOOL Wallet criado neste dispositivo. Uma carteira externa que você conectou
  ou um endereço somente leitura pode receber e mostrar o próprio saldo; um envio ou um pagamento por canal de
  pagamento a partir deles é recusado antes de qualquer aprovação ser pedida, e nada é assinado.
* Atividade registra os pagamentos feitos pelo VOOL e os recibos deles. Não é um histórico completo de
  pagamentos recebidos. Esta carteira não oferece nenhuma ação de swap ou de bridge.

## Como um pagamento é aprovado

1. O VOOL prepara uma proposta com os valores exatos e um teto de taxa.
2. Você a aprova com o seu PIN ou senha (ou com a presença no dispositivo, onde houver).
3. O pagamento é assinado e enviado, e um recibo é registrado.

As aprovações não passam de uma proposta para outra. Um PIN errado não assina nada. Tentativas erradas
demais bloqueiam a carteira por um tempo — de propósito, com uma espera informada.

## Pagamentos automáticos x402

x402 é um protocolo de pagamento por pedido que alguns sites e serviços de IA usam. **Sites que
pedem pagamento x402 em USDC na Base** (ou na Base Sepolia, em Developer options) podem ser pagos
pela sua carteira Crypto Pilot: uma aprovação com o seu PIN, dentro dos seus limites. O serviço de pagamento
do site cobre a taxa de rede. **Nenhum pagamento real na mainnet foi feito com isso ainda.** As outras redes
de teste (Solana Devnet, Ethereum Sepolia e BNB testnet) continuam em Developer options.
**Configurações → Cripto → Payment channels** lista todas as formas como a carteira pode pagar um site, cada
uma com o próprio botão liga/desliga; um canal desligado recusa antes de qualquer coisa ser assinada. Pagar
pelo Solana pay-kit não está incluído nos downloads, então esse canal aparece como não utilizável.
**Marketplace de modelos:** a opção de pagamento por chamada do marketplace de modelos nas Configurações pode
pagar pela carteira Crypto Pilot da Solana Mainnet em USDC ou SOL. Cada chamada espera a sua aprovação dentro
dos limites da sua carteira e mostra a taxa de 0,1% do VOOL. Esse caminho ainda não foi testado em uma cadeia
real.
Quando um recurso pago usa x402:

* Os pagamentos passam pelos mesmos limites e pelo mesmo fluxo de aprovação da carteira.
* Os pagamentos automáticos têm limite por pagamento; um recurso acima do limite **não é pago** (você pode
  aumentar o limite deliberadamente ou pagá-lo por meio de uma proposta explícita).
* Se um pagamento não pôde ser concluído, o resultado é informado como **desconhecido** — confira o status dele
  antes de tentar de novo; o VOOL não vai incentivar um pagamento duplicado às cegas.

## Taxa de serviço DNA

O caminho x402 de pagamento por chamada do marketplace de modelos cobra uma taxa de serviço de **0,1% (10
pontos-base)** sobre cada pagamento aceito a um provedor, mostrada separada do pagamento e separada da taxa de
rede. Nada mais gera essa taxa: envios comuns, criação de carteiras, inferência local e chamadas com a sua
própria chave de API (BYOK) **não** têm taxa de serviço. As taxas acumuladas são cobradas de forma econômica
(com um pagamento nativo posterior, com uma única aprovação, só quando o custo da cobrança é limitado) — nunca
por uma cobrança separada de surpresa.

## Recuperação

* A **frase de recuperação é mostrada exatamente uma vez**, na criação.
* A chave de backup de uma carteira piloto também é liberada uma única vez durante a configuração, e não há
  exportação posterior. Se você não a salvou, cancele a configuração e não envie fundos para esse endereço.
* Um VOOL Wallet mais antigo, criado antes do Crypto Pilot, pode exportar a chave privada uma vez para
  importá-la em outro app de carteira. A exportação só abre com o login do próprio Mac (Touch ID ou a senha do
  seu Mac), nunca com o PIN da carteira, e a chave é mostrada uma vez sem ser armazenada.
* Perdeu uma carteira? Recupere-a com a frase que você anotou na criação. Uma prova de recuperação que
  não corresponde exatamente a esta carteira é recusada antes de qualquer gravação.
