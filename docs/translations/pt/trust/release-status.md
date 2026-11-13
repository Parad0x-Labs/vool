---
description: Em que fase o VOOL está, dito sem rodeios.
translation: machine
source_sha256: ad4e68eba0b8f45c35cf4ce8b87f79ee04e3aaee23c83f2ff6d794fa35d56c06
---

# Status das versões

Esta página é a referência mantida sobre o status das versões. Outros documentos citam fatos sobre as versões
de passagem; quando discordarem desta página, vale esta página.

## Fase atual

O VOOL está em **beta**. A versão atual é a
[**v0.7.7-beta**](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.7-beta), uma versão do GitHub
compilada a partir de `main` em `8af687c`. Ela tem downloads do app para três plataformas,
cada um com uma soma de verificação SHA-256 publicada. A instalação a partir do código-fonte com o script de
bootstrap continua suportada no macOS, no Linux e no Windows; veja [Instalação](../getting-started/install.md).

| Download | Para |
| --- | --- |
| `VOOL-0.7.7-beta-macos-arm64.dmg` | Macs com Apple Silicon, macOS 14 ou mais recente |
| `VOOL-0.7.7-beta-macos-x86_64.dmg` | Macs com Intel, macOS 14 ou mais recente |
| `vool-0.7.7-beta-linux-x86_64.tar.gz` | Linux x86_64, testado no Ubuntu 22.04 e 24.04 |

Os registros de versão deste repositório cobrem o canal do GitHub. Outros canais de download são
operados separadamente; verifique qualquer download com a soma de verificação publicada na versão
do GitHub.

| | Status |
| --- | --- |
| Downloads públicos | DMG para macOS com Apple Silicon, DMG para macOS com Intel e arquivo tar para Linux x86_64 na versão v0.7.7-beta do GitHub |
| Somas de verificação publicadas | SHA-256 de cada download, na página da versão |
| Assinatura de código | Os apps para macOS têm assinatura ad hoc, não Developer ID. A assinatura será adicionada depois desta beta e não é condição para ela |
| Notarização do macOS | Não notarizado. O macOS pede que você confirme a primeira abertura (veja abaixo) |
| Windows | Não há download para Windows nesta versão; no Windows, a instalação é a partir do código-fonte e é experimental |
| Atualizações | A partir da 0.7.5, o VOOL se atualiza sozinho: com “Ligar-se à internet por conta própria” ativado, ele mostra um aviso e instala uma nova versão com um clique. Para atualizar a partir da 0.7.4 ou 0.7.3 em um Mac: ative “Ligar-se à internet por conta própria” (Configurações > Privacidade e permissões) e pressione “Verificar atualizações” uma vez. No Linux, ou a partir da 0.7.2, 0.7.1 ou 0.7.0: baixe a 0.7.7 e instale-a por cima da sua versão atual. Suas conversas e configurações ficam no seu perfil, fora do app. Veja [Atualizações e migração](../reference/updates-migration.md) |
| Estabilidade da API | Muda entre versões |

## O que isso significa para você

* Na primeira abertura de um app para Mac, o macOS se recusa a abri-lo porque ele não é notarizado.
  Isso é esperado nesta fase, não um sinal de download danificado. Confira a soma de verificação
  e depois abra **Ajustes do Sistema → Privacidade e Segurança** e clique em **Abrir Mesmo Assim**. No
  macOS 14, você também pode usar Controle + clique no app e escolher **Abrir**.
* Escolha o download para Mac que corresponde ao seu chip. O app para Apple Silicon não funciona em um
  Mac com Intel e avisa isso ao iniciar.
* Os formatos de configuração podem mudar entre versões.
* Os recursos de cripto ficam desligados até você ligá-los.

## Limitações da beta e do piloto, ditas explicitamente

| Recurso | Estado nesta versão |
| --- | --- |
| Cripto e carteira | Desativados por padrão. Quando você os ativa, o VOOL usa a mainnet; as redes de teste ficam em Developer options. Todo pagamento passa pela carteira do VOOL, com os limites dela, a sua aprovação e um recibo. Veja [Carteira](../guides/wallet.md). |
| Transferências na mainnet | Moedas nativas, e USDT na Solana, Ethereum, BNB Smart Chain e Arbitrum One, enviados de um VOOL Wallet criado neste dispositivo. USDC na Solana e na Base é só para canais de pagamento. Uma conta conectada ou somente leitura pode receber e mostrar o próprio saldo, mas não pode enviar pelo VOOL. |
| Pagar com x402 | Sites que pedem pagamento x402 em USDC na Base ou na Base Sepolia podem ser pagos pela sua carteira Crypto Pilot: uma aprovação com o seu PIN, dentro dos seus limites. O serviço de pagamento do site cobre a taxa de rede. **Configurações → Cripto → Payment channels** tem um botão liga/desliga para cada forma como a carteira pode pagar um site; um canal desligado recusa antes de qualquer coisa ser assinada. Nenhum pagamento real na mainnet foi feito com isso ainda. Pagar pelo Solana pay-kit não está incluído nos downloads; esse canal aparece como não utilizável. |
| X | De qualquer conversa: pesquise posts e contas, Spaces, tendências e DMs, e consulte calls anteriores sobre um token. Qualquer coisa que aja na sua conta espera o seu OK. |
| Ditado | Indisponível nesta versão beta. |
| Modo de pergunta “rajada” na nuvem | A confirmação por chamada ainda não está ligada nesta versão. |
| Modelos locais | Antes que um modelo local possa responder, abra **Configurações → Modelos e provedores → Modelos locais** e pressione **Certify** para ele uma vez. |
| Macs com Intel | Os modelos locais rodam no processador e são lentos. A certificação leva em conta processadores mais lentos, mas uma resposta longa de memória pode estourar o tempo limite, então use por enquanto um modelo na nuvem nesses Macs. |
| Chat privado | Diga “private chat”, “don't record this” ou “off the record”, e nada daquela conversa é salvo. Os comandos funcionam em todos os 17 idiomas do app. |
| Esquecer | Esquecer apaga a memória do VOOL e o histórico de execução correspondente, para que o item não seja lembrado nem listado de novo, e apaga as palavras esquecidas do arquivo de memória local do VOOL e do índice de busca dele. O VOOL guarda as palavras que você pediu para esquecer em uma lista curta no seu computador, para poder continuar recusando-as. Depois que você esquece algo em uma conversa, o VOOL não aceita de volta esse mesmo fato naquela conversa; comece uma conversa nova para contá-lo de novo. |
| PDFs digitalizados | Lidos com o reconhecimento de texto integrado do Mac, então só em Macs. |
| Bichinho de mesa | O bichinho vive na janela do VOOL. Arrastá-lo para fora da janela não o leva para a área de trabalho. |
| Códice de habilidades | Explorar o códice de habilidades mostra as habilidades listadas para a sua versão exata do VOOL, e você revisa uma habilidade antes de ela ser instalada. Publicar no códice de habilidades verifica uma habilidade que você criou no seu computador e depois abre o site do Skill Codex com ela já preenchida; você entra lá e pressiona Send, e o próprio VOOL não envia nada. Só o texto da habilidade vai junto, então uma habilidade com arquivos extras ainda não pode ser compartilhada. |

A assinatura com Developer ID e a notarização para o macOS, e somas de verificação assinadas para o Linux, são as
próximas etapas do caminho de lançamento; nenhuma delas é afirmada hoje.
