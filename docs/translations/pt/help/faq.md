---
description: Perguntas comuns sobre o VOOL — se funciona offline, onde a sua chave de API fica guardada, se é gratuito e quais sistemas ele suporta.
translation: machine
source_sha256: 4366d0e907cc16c3206be768f984f5a525f4187024d16aae541a2ec8937e0aa0
---

# Perguntas frequentes

## O VOOL funciona offline?

Sim, com um modelo local, para as suas conversas e os seus arquivos. Os modelos na nuvem precisam de conexão
de rede, por definição. Fatos ao vivo, como clima ou preços, vêm de serviços web públicos somente enquanto Pesquisas na web estiver ativado (fica desativado em uma instalação nova), e nunca em Somente local.

## Preciso de uma chave da nuvem?

Não. Um modelo local basta para usar o app.

## Onde a minha chave fica guardada?

Em um arquivo criptografado na runtime home do VOOL no seu computador, que só a sua conta de
usuário consegue ler. As chaves que o VOOL já guardava no chaveiro do macOS em um Mac continuam lá. As chaves
ficam na sua máquina. Veja [Modelo de segurança](../trust/security.md#chaves).

## A Parad0x vê os meus prompts?

Não no modo local, e também não no modo nuvem — os pedidos à nuvem vão da sua máquina para
o seu provedor. Veja [Tratamento de dados](../trust/data-handling.md).

## Por que o meu sistema me avisa quando eu o abro?

Os apps para macOS têm assinatura ad hoc, mas ainda não são notarizados, então o macOS pede que você confirme a
primeira abertura. Os passos estão em [Instalação](../getting-started/install.md#primeira-abertura-em-um-mac). Veja o
[Status das versões](../trust/release-status.md).

## Posso usar o meu próprio modelo?

Sim. Qualquer ambiente de execução que exponha um endpoint local compatível com a OpenAI. Veja
[Modelos compatíveis](../reference/models.md).

## É gratuito?

O aplicativo é gratuito durante a beta. O uso de modelos na nuvem é cobrado pelo seu
provedor, na sua conta.
