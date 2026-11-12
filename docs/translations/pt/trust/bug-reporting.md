---
description: Como relatar um bug com segurança — rascunhos higienizados, aprovação dos bytes exatos e o que nunca sai da sua máquina.
translation: machine
source_sha256: 4e64e6313c1f6a8be6784a1f35cedc87b81a5661fdb548c6821109f935db818f
---

# Relato de bugs e privacidade

## O relator de bugs integrado

Use o relator de bugs do app (ou a CLI `python -m apps.vool_cli bug-report`). Ele nunca envia nada
sozinho — o fluxo tem três etapas de propósito:

1. **Rascunho** — um relatório é montado localmente a partir de logs e diagnósticos recentes, higienizado
   enquanto é montado. *“Nothing is sent yet — a local draft is created first.”*
2. **Visualização** — você vê os **bytes exatos** que seriam enviados, com um hash do conteúdo e um resumo
   da higienização que lista o que foi removido. Os campos que você não quer enviar (logs, flags, notas de
   reprodução, anexos) podem ser removidos; qualquer edição zera a aprovação.
3. **Aprovar** — o envio exige a sua aprovação explícita desses bytes exatos. Se o conteúdo
   mudar, a aprovação deixa de valer e você precisa aprovar de novo.

Depois que você aprova, **Open on GitHub** abre no seu navegador uma página de relatório já preenchida no
repositório público [vool-reports](https://github.com/Parad0x-Labs/vool-reports). Leia-a lá mais uma vez e
pressione Submit. O próprio VOOL não envia nada, e nenhum token do GitHub é necessário.
**Copy report** e **Save a local copy** não precisam de aprovação nenhuma: eles entregam a você os mesmos bytes
higienizados, e nada sai da sua máquina. **My reports** lista os seus rascunhos e como cada um saiu.

## Relatar uma resposta errada

Pressione **Reportar** ou 👎 embaixo de qualquer resposta, ou destaque a parte errada e pressione **Reportar**. O
relatório cita só essa parte (ou a resposta inteira). A sua pergunta fica de fora, a menos que você marque a
caixa. O recibo da resposta é incluído: modelo, provedor, tokens, tempo e quais etapas rodaram, nunca
o conteúdo delas. Uma resposta que você pediu para o VOOL esquecer não pode ser reportada.

## O que é adicionado para ajudar a investigar

As características do seu computador: versão do sistema operacional, chip, memória, GPU e a versão do servidor
de modelos local. Também uma verificação rápida de funcionamento: se o servidor de modelos local responde, e o
espaço livre em disco como uma faixa. Nunca o nome do seu computador, o seu nome de usuário, números de série ou
endereços de rede. Você pode remover qualquer um desses dados na visualização.

## O que a higienização remove

De forma determinística, antes mesmo de você ver o rascunho: credenciais e cabeçalhos de autenticação, caminhos
da pasta pessoal e nomes de usuário, o nome do seu computador, e-mails, endereços IP, PINs e códigos de uso
único, números de cartão e de conta bancária (IBAN), números de telefone, endereços de carteira, os dados
pessoais salvos no seu perfil, segredos rotulados e blocos de alta entropia — tudo isso reforçado por uma passada
final de busca de segredos.
O VOOL não consegue reconhecer nomes ou fatos privados digitados em frases comuns, então leia a visualização. Os
stack traces são reconstruídos a partir de uma lista de permissões, não colados como estão.
As evidências de falhas nos relatórios trazem **somente códigos tipificados** — nunca contexto interno nem cadeias
de causas.

Cada valor removido é substituído por um marcador. Um e-mail, uma pessoa, uma pasta pessoal, um endereço IP,
um número de telefone, um endereço de carteira, o nome do seu computador ou um dado do seu perfil mantém um mesmo
marcador numerado em todo o relatório, como `[EMAIL_1]` e `[EMAIL_2]` para dois endereços diferentes,
para que o relatório continue fácil de ler.
Cada chave, senha, PIN, número de cartão ou de conta bancária removido aparece como `[SECRET_REMOVED]`, nunca
numerado. A lista que liga um marcador ao valor real fica só na memória RAM do VOOL enquanto você trabalha no
relatório, para que uma edição mantenha os mesmos marcadores. Ela nunca é salva nem enviada, e é esquecida
quando você exclui o relatório, depois de uma hora sem uso ou quando o VOOL reinicia.

## O que o VOOL nunca faz

* Nenhum relato externo automático, de nenhum tipo.
* Nenhum canal de telemetria esperando atrás de uma configuração.
* Problemas de segurança passam pelo canal privado da
  [política de segurança](https://github.com/Parad0x-Labs/vool/security/policy) do repositório — nunca por uma
  issue pública.

## Resumo de privacidade

* Com um modelo local selecionado, os prompts e os arquivos são processados na sua máquina.
* As chamadas para a nuvem usam a chave de API que você forneceu, armazenada localmente; as chaves nunca vão
  junto nos relatórios.
* Todos os detalhes do fluxo de dados estão em [Tratamento de dados](../trust/data-handling.md).
