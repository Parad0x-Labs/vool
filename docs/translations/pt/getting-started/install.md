---
description: Como instalar o assistente de IA VOOL no macOS (Apple Silicon ou Intel) ou no Linux a partir dos downloads da versão, ou a partir do código-fonte no macOS, Windows ou Linux, incluindo os requisitos do sistema e como verificar um download.
translation: machine
source_sha256: e0025d89fc56caca91923de6f1e6afe25ba6f6383a300ec3df5a7f7e3f401e55
---

# Instalação

O VOOL 0.7.7-beta tem downloads do app para Macs com Apple Silicon, Macs com Intel e Linux x86_64 na
[versão v0.7.7-beta](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.7-beta).
Você também pode instalá-lo a partir do código-fonte com o script de bootstrap no macOS, Linux ou Windows.
Veja o [Status das versões](../trust/release-status.md) para detalhes de assinatura e atualização.

## Requisitos

| | Mínimo | Confortável |
| --- | ---: | ---: |
| Memória RAM | 8 GB | 16 GB ou mais |
| Disco | 2 GB para o app | 20 GB com modelos locais |
| macOS | 14 Sonoma, Apple Silicon ou Intel ([limites](#macs-com-intel)) | 14 Sonoma ou mais recente, Apple Silicon |
| Windows | 10 (64 bits) | 11 |
| Linux | glibc 2.31 | Ubuntu 22.04 ou mais recente |

Os modelos locais precisam de bem mais memória RAM do que o próprio app. As orientações de tamanho estão em
[Executar um modelo local](../guides/local-models.md).

## Baixar o app

| Seu computador | Download |
| --- | --- |
| Mac com Apple Silicon (M1 e posteriores) | [`VOOL-0.7.7-beta-macos-arm64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/VOOL-0.7.7-beta-macos-arm64.dmg) |
| Mac com chip Intel | [`VOOL-0.7.7-beta-macos-x86_64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/VOOL-0.7.7-beta-macos-x86_64.dmg) |
| Linux x86_64 (Ubuntu 22.04 / 24.04) | [`vool-0.7.7-beta-linux-x86_64.tar.gz`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/vool-0.7.7-beta-linux-x86_64.tar.gz) |

Para descobrir o chip do seu Mac, abra o menu Apple e escolha **Sobre Este Mac**. “Apple M…”
significa Apple Silicon; “Intel” significa Intel. O app para Apple Silicon não funciona em um Mac com Intel
e avisa isso quando você o abre.

Confira o SHA-256 do download antes de abri-lo (veja [Verificar um download](#verificar-um-download)).

### Primeira abertura em um Mac

O app beta ainda não é notarizado pela Apple, então o macOS pede que você confirme na primeira vez
que o abrir. Isso é esperado e acontece só uma vez.

1. Abra o DMG e arraste o **VOOL** para **Aplicativos**.
2. Clique duas vezes no VOOL. O macOS informa que não pode verificar se o app contém software malicioso.
   Feche a mensagem.
3. Abra **Ajustes do Sistema → Privacidade e Segurança**. Role até a mensagem sobre o VOOL
   e clique em **Abrir Mesmo Assim**. Digite a sua senha para confirmar.

No macOS 14 Sonoma, você também pode usar Controle + clique no VOOL em Aplicativos, escolher **Abrir**
e depois clicar em **Abrir** de novo.

### Linux

Baixe o arquivo tar e o arquivo `.sha256` dele na página da versão, confira-o e depois execute o
instalador incluído. Não é preciso sudo nem o Python do sistema.

```bash
sha256sum -c vool-0.7.7-beta-linux-x86_64.tar.gz.sha256
tar -xzf vool-0.7.7-beta-linux-x86_64.tar.gz
bash vool-0.7.7-beta-linux-x86_64/install.sh
~/.local/bin/vool
```

O VOOL é instalado em `~/.local/share/vool`, coloca o comando `vool` em `~/.local/bin` e adiciona
uma entrada no menu de aplicativos. O app para desktop abre o chat no seu navegador; fechar o navegador
não encerra o VOOL. Para remover o VOOL, execute `bash ~/.local/share/vool/current/uninstall.sh`.
Seus chats e configurações ficam no seu perfil.

A partir da 0.7.5, o download para Linux se atualiza dentro do VOOL. A nova versão é instalada ao lado
da antiga. Se ela não iniciar direito, o VOOL volta sozinho para a anterior.

### Depois de instalar

Antes que um modelo local possa responder, abra **Configurações → Modelos e provedores → Modelos locais** e pressione **Certify** para ele uma vez. Isso executa uma verificação rápida na sua máquina.

A partir da 0.7.5, o VOOL se atualiza sozinho: com “Ligar-se à internet por conta própria” ativado, ele mostra um aviso e instala uma nova versão com um clique. Para atualizar a partir da 0.7.4 ou 0.7.3 em um Mac: ative “Ligar-se à internet por conta própria” (Configurações > Privacidade e permissões) e pressione “Verificar atualizações” uma vez. A partir da 0.7.4 ou anterior no Linux, ou da 0.7.2 e anteriores em um Mac: baixe a 0.7.7 e instale-a por cima da sua versão atual. Seus chats e configurações
ficam no seu perfil, fora do app. Os recursos de cripto vêm desativados por padrão.

## Instalar a partir do código-fonte

Execute o script de bootstrap da sua plataforma:

```bash
curl -fsSLo bootstrap_vool.sh https://raw.githubusercontent.com/Parad0x-Labs/vool/v0.7.7-beta/installer/bootstrap_vool.sh
bash bootstrap_vool.sh --ref v0.7.7-beta
```

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/Parad0x-Labs/vool/main/installer/bootstrap_vool.ps1 -OutFile bootstrap_vool.ps1
powershell -ExecutionPolicy Bypass -File .\bootstrap_vool.ps1
```

Os comandos de macOS / Linux fixam tanto o script de bootstrap quanto o download do código-fonte em `v0.7.7-beta`. Para escolher outra versão, altere tanto a tag na URL do script quanto `--ref`. O bootstrap aceita um nome de branch ou uma tag de versão, não um hash de commit.

Instalar a partir do código-fonte requer Python 3.11 ou mais recente. Se nenhum for encontrado, o instalador tenta
configurar um. Os downloads do app incluem o próprio Python.

O instalador prepara o ambiente Python, verifica o seu hardware, configura o modelo
local e inicia os serviços locais. Reserve tempo e espaço em disco para o primeiro download do modelo.
A inferência dos modelos locais funciona offline depois da configuração; as ferramentas da web e os modelos na
nuvem precisam de conexão.

## Macs com Intel

O VOOL funciona em Macs com Intel e macOS 14 ou mais recente (por exemplo, um MacBook Pro de 13 polegadas de 2020),
com o download para Intel ou a partir do código-fonte. O instalador a partir do código-fonte detecta a CPU Intel e
instala as versões para Intel de todas as dependências. Dois limites são específicos do Intel:

* **Sem treinamento de modelos locais.** O PyTorch não publica nenhuma versão para macOS em Intel desde a 2.3,
  então o instalador o ignora e o treinamento LoRA informa que não está disponível. Chat, memória,
  ferramentas e modelos na nuvem não são afetados.
* **Os modelos locais rodam só na CPU.** Os executores de modelos locais não têm aceleração por GPU para os gráficos
  Intel, então as respostas locais são várias vezes mais lentas do que no Apple Silicon. Em um Mac com 8 GB, use um
  modelo na nuvem para o chat; com 16 GB, o pequeno modelo local padrão do instalador funciona, mas é lento.

Nos Macs com Intel, os modelos locais rodam no processador e são lentos. Uma resposta longa de memória pode estourar o tempo limite, então use por enquanto um modelo na nuvem nesses Macs. A certificação de modelos locais leva em conta processadores mais lentos.

Os Macs com Intel têm o próprio download, `VOOL-0.7.7-beta-macos-x86_64.dmg`. O download para Apple Silicon
não funciona no Intel e avisa isso ao iniciar.

## Verificar um download

Cada download da versão tem ao lado uma soma de verificação SHA-256. Verifique o arquivo antes
de abri-lo:

```bash
shasum -a 256 <downloaded-artifact>    # macOS
sha256sum <downloaded-artifact>         # Linux
```

```powershell
Get-FileHash -Algorithm SHA256 <downloaded-artifact>
```

Compare o resultado com a soma de verificação publicada com essa versão. Se forem diferentes,
apague o arquivo e baixe-o de novo.

## Próximo passo

Continue em [Primeira execução](first-run.md).
