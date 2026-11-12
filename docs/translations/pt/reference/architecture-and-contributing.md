---
description: Como o VOOL é construído — a arquitetura em uma página, a configuração para desenvolvedores e como contribuir.
translation: machine
source_sha256: 7cb91029e16c6510821e1ca26b509da8cdfb63113111619d3a31e364a2f088d3
---

# Arquitetura e contribuição

## A arquitetura em uma página

O VOOL é um ambiente de execução para desktop que prioriza o local, com um núcleo servido:

```text
┌──────────────────────────────────────────────┐
│ Native window / browser console              │
│  (served pages; the console you use)         │
├──────────────────────────────────────────────┤
│ Served runtime (local HTTP API)              │
│  routing → tool execution → answers          │
├───────────────┬──────────────┬───────────────┤
│ Local models  │ Cloud lanes  │ Wallet (opt)  │
│ (Ollama etc.) │ (your keys)  │ pilot lanes   │
├───────────────┴──────────────┴───────────────┤
│ Records: receipts, ledgers, fault registry   │
│ Local storage (SQLite under your profile)    │
└──────────────────────────────────────────────┘
```

Princípios que o código impõe:

* **Local por padrão; nuvem com a sua chave.** As trilhas na nuvem só existem com as suas credenciais e
  os seus tetos.
* **Uma autoridade por decisão.** Permissões, gastos e saída de rede têm, cada um, um único
  contrato responsável; as falhas são classificadas na fronteira que as controla.
* **Diga o que falhou.** Resultados desconhecidos são tipificados como desconhecidos; recusas dizem o que não
  aconteceu; um modelo fixado nunca é substituído em silêncio.
* **Tudo registrado.** Chamadas de ferramentas, aprovações, pagamentos e atualizações gravam recibos no
  seu perfil.

## Configuração para desenvolvedores

```bash
git clone https://github.com/Parad0x-Labs/vool.git
cd vool
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests -q            # the suite
```

As notas de cada plataforma, a organização completa das ferramentas e os critérios de lançamento ficam na
documentação de engenharia do repositório (`docs/` na raiz do repositório; os detalhes internos mais profundos
não fazem parte deste site público de documentação).

## Documentação: uma única fonte canônica

Esta documentação é gerada a partir de **uma** fonte: a pasta `docs/` deste repositório (estrutura do GitBook,
`.gitbook.yaml` na raiz do repositório). O site público de documentação é construído a partir dela. Para
trabalhar na documentação:

```bash
python3 tools/docs/build_docs.py --src . --out docs-build --base /docs
python3 tools/docs/check_docs_build.py docs-build /docs
python3 -m http.server -d docs-build 8000   # local preview
```

A referência de erros (`ERROR_BOOK.md`) e o manifesto de entrega para localização são
**gerados a partir dos próprios registros do ambiente de execução** — gere-os de novo, nunca os edite à mão:

```bash
python -m tools.generate_error_book
python -m tools.generate_localization_handoff
```

Um teste específico faz o build falhar se os arquivos gerados se afastarem dos registros deles.

## Como contribuir

* Alterações pequenas e revisadas; cada afirmação na documentação precisa corresponder ao código como foi
  lançado.
* Preserve códigos e identificadores estáveis — eles duram mais que as versões.
* Nunca amplie uma autoridade em silêncio: uma nova permissão, um novo caminho de gasto ou uma nova trilha de
  saída de rede é uma mudança de projeto, não um patch.
* As limitações da beta são documentadas, não escondidas — se você encontrar uma não documentada, isso é um
  bug na documentação.
