---
description: Como o VOOL se atualiza, o que acontece com os seus dados e a compatibilidade entre versões.
translation: machine
source_sha256: 3b6a950556f4669f116dfa3a911ffb5e07ad8bcfa04415d68cf5a3d1adf0fe65
---

# Atualizações e migração

## Como as atualizações funcionam

A partir da 0.7.5, o VOOL se atualiza sozinho: com “Ligar-se à internet por conta própria” ativado, ele mostra um aviso e instala uma nova versão com um clique. Para atualizar a partir da 0.7.4 ou 0.7.3 em um Mac: ative “Ligar-se à internet por conta própria” (Configurações > Privacidade e permissões) e pressione “Verificar atualizações” uma vez. A partir da 0.7.4 ou anterior no Linux, ou da 0.7.2 e anteriores em um Mac: baixe a 0.7.7 e instale-a por cima da sua versão atual. Suas conversas e configurações ficam no seu perfil, fora do app.

Com “Ligar-se à internet por conta própria” ativado, o VOOL procura uma nova versão ao iniciar e uma vez por
dia, e mostra um aviso sem que você precise pressionar “Verificar atualizações”. Com a opção desativada, o VOOL
não baixa nada e o cabeçalho mostra **Verificação de atualizações desativada**.

Um pacote de atualização assinado passa por um processo verificado com reversão automática:

1. **Download** — verificado em relação a um manifesto assinado.
2. **Instalação preparada** — a nova versão é instalada ao lado da antiga.
3. **Migração** — os seus dados são migrados para a nova versão do armazenamento. Se a migração falhar, os seus
   dados são restaurados para a versão anterior e o app reinicia nela: *“Your data was
   restored to the previous version.”*
4. **Verificação de integridade** — a nova versão precisa provar que consegue funcionar antes de a antiga ser
   aposentada.
5. **Troca** — atômica; a versão anterior é mantida como destino da reversão.

No Linux, a instalação pelo arquivo tar é atualizada da mesma forma. A nova versão é instalada ao lado
da que está rodando, e `current` passa para ela em um só passo. Se a nova versão não estiver
saudável, o VOOL volta sozinho para a anterior. No Mac e nas instalações a partir do código-fonte, você pode
voltar manualmente com **Voltar à versão** anterior, mas isso ainda não existe na instalação pelo arquivo tar do
Linux.

Se alguma etapa falhar, o app fica como estava — a mensagem de falha cita a etapa
(`verification_failed`, `insufficient_disk_space`, …) e cada etapa tem a própria frase
de recuperação.

## Compatibilidade dos dados

* O seu perfil, as conversas, os recibos e as configurações ficam fora do pacote do app, então substituir o
  app nunca substitui os seus dados.
* O armazenamento local se recusa a abrir **em uma versão anterior**: um banco de dados gravado por um VOOL mais
  novo não é lido em silêncio por um mais antigo — ele avisa e se recusa, porque adivinhar uma estrutura de
  armazenamento desconhecida o corromperia. Reinstale a versão mais nova para ler os dados novos.
* Os arquivos instalados pelas atualizações e o próprio estado da atualização são registrados no seu perfil com
  recibos, então a pergunta “qual versão mexeu nos meus dados” tem resposta depois.

## Canal beta

A linha de versões atual é uma **beta** (veja [Status das versões](../trust/release-status.md)).
As atualizações são assinadas pela chave fixada do publicador e verificadas antes de serem instaladas. O app
para macOS em si tem assinatura ad hoc e não é notarizado, então o macOS pode pedir que você confirme a primeira
abertura de uma nova versão.
