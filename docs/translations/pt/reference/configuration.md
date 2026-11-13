---
description: Onde o VOOL guarda a configuração e o que as configurações significam.
translation: machine
source_sha256: 628a20ac7c08e03575690eb673731afc27ec1196b1514bf1ed75dfb7c21e3daf
---

# Configuração

## Locais

O VOOL guarda as configurações, as conversas, a memória e os registros em uma única pasta, a “runtime home”:

| Instalação | Runtime home |
| --- | --- |
| App para macOS | `~/Library/Application Support/VOOL/runtime` |
| Arquivo tar para Linux e instalações a partir do código-fonte | `~/.vool_runtime` |
| Windows (experimental, a partir do código-fonte) | `%LOCALAPPDATA%\VOOL` |

Definir `VOOL_HOME` antes de iniciar o VOOL faz com que ele use essa pasta. O comando `vool` mostra a
pasta em uso.

As chaves **não** são gravadas em arquivos de configuração nem em logs. Elas ficam criptografadas em um arquivo
de credenciais separado nesta runtime home, que só a sua conta de usuário consegue ler; as chaves que o VOOL
já guardava no chaveiro do macOS em um Mac continuam lá. Veja
[Modelo de segurança](../trust/security.md#chaves).

## Configurações

Altere as configurações no app, em **Configurações**. As que decidem o que o VOOL pode fazer:

* **Modelo** — o modelo ativo, que decide se um turno é LOCAL ou NUVEM.
* **Espaço de trabalho** — a pasta do projeto em que uma conversa trabalha; veja
  [Espaços de trabalho](../concepts/workspaces.md) para as outras pastas que o VOOL pode acessar.
* **Modo de operação** — escolhido no menu ao lado da caixa de mensagem, não aqui. Manual pergunta
  antes de toda alteração; Auto deixa rodar as edições e os comandos do dia a dia, enquanto excluir,
  sobrescrever, instalar, gastar e alterar configurações continuam perguntando. Veja
  [Permissões](../concepts/permissions.md).
* **Acesso à web e cripto** — os dois ficam desligados até você ligá-los.

O VOOL não tem nenhuma configuração de telemetria de produto porque não envia telemetria de produto.

## Redefinir

Encerre o VOOL e tire a runtime home do lugar. Os arquivos do espaço de trabalho não são alterados; o VOOL
inicia de novo com um perfil novo. As suas chaves salvas saem do lugar junto com ela, então adicione-as de novo
em **Configurações → Chaves de API**. As chaves guardadas no chaveiro do macOS continuam lá até você removê-las no
Acesso às Chaves.
