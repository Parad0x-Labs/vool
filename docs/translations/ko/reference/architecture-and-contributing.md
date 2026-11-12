---
description: VOOL이 만들어진 방식. 한 페이지로 보는 아키텍처, 개발자 설정, 기여하는 방법.
translation: machine
source_sha256: 7cb91029e16c6510821e1ca26b509da8cdfb63113111619d3a31e364a2f088d3
---

# 아키텍처와 기여

## 한 페이지로 보는 아키텍처

VOOL은 로컬 우선 데스크톱 런타임이며, 코어는 로컬 서버로 제공됩니다.

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

코드가 강제하는 원칙:

* **기본은 로컬, 클라우드는 내 키로.** 클라우드 레인은 내 자격 증명과 내 상한이 있을 때만
  존재합니다.
* **결정마다 권한 주체는 하나.** 권한, 지출, 네트워크 송신은 각각 하나의 소유 계약이 있으며,
  결함은 그것을 소유한 경계에서 분류됩니다.
* **무엇이 실패했는지 말하기.** 알 수 없는 결과는 알 수 없음으로 분류되고, 거부는 무엇이 일어나지
  않았는지 말하며, 고정된 모델은 절대 조용히 대체되지 않습니다.
* **모든 것을 기록.** 도구 호출, 승인, 결제, 업데이트는 프로필 아래에 영수증을 씁니다.

## 개발자 설정

```bash
git clone https://github.com/Parad0x-Labs/vool.git
cd vool
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests -q            # the suite
```

플랫폼별 참고 사항, 전체 도구 체인 구성, 릴리스 게이트는 저장소의 엔지니어링 문서(저장소 루트의
`docs/`. 깊은 내부 구조는 이 공개 문서 사이트에 포함되지 않습니다)에 있습니다.

## 문서: 하나의 정식 원본

이 문서는 **하나의** 원본, 즉 이 저장소의 `docs/` 폴더(GitBook 구성, 저장소 루트의 `.gitbook.yaml`)에서
생성됩니다. 공개 문서 사이트는 이 폴더로 빌드됩니다. 문서 작업을 하려면:

```bash
python3 tools/docs/build_docs.py --src . --out docs-build --base /docs
python3 tools/docs/check_docs_build.py docs-build /docs
python3 -m http.server -d docs-build 8000   # local preview
```

오류 참조(`ERROR_BOOK.md`)와 현지화 인계 매니페스트는 **런타임 자체의 레지스트리에서
생성됩니다**. 직접 편집하지 말고 다시 생성하세요.

```bash
python -m tools.generate_error_book
python -m tools.generate_localization_handoff
```

생성된 파일이 레지스트리와 어긋나면 전용 테스트가 빌드를 실패시킵니다.

## 기여

* 작고 검토된 변경. 문서의 모든 주장은 배포된 코드와 일치해야 합니다.
* 고정된 코드와 식별자를 유지하세요. 이들은 빌드보다 오래 남습니다.
* 권한을 절대 조용히 넓히지 마세요. 새 권한, 지출 경로, 송신 레인은 패치가 아니라 설계
  변경입니다.
* 베타의 제한 사항은 숨기지 않고 문서화합니다. 문서화되지 않은 제한을 발견하면, 그것은 문서의
  버그입니다.
