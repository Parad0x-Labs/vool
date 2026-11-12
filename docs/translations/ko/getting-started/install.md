---
description: AI 어시스턴트 VOOL을 릴리스 다운로드로 macOS(Apple Silicon 또는 Intel)나 Linux에 설치하거나, 소스에서 macOS, Windows, Linux에 설치하는 방법입니다. 시스템 요구 사항과 다운로드 검증 방법도 다룹니다.
translation: machine
source_sha256: e0025d89fc56caca91923de6f1e6afe25ba6f6383a300ec3df5a7f7e3f401e55
---

# 설치

VOOL 0.7.7-beta는 [v0.7.7-beta 릴리스](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.7-beta)에서
Apple Silicon Mac, Intel Mac, Linux x86_64용 앱을 내려받을 수 있습니다.
macOS, Linux, Windows에서는 부트스트랩 스크립트로 소스에서 설치할 수도 있습니다.
서명과 업데이트에 관한 자세한 내용은 [릴리스 상태](../../../trust/release-status.md)를 참고하세요.

## 요구 사항

| | 최소 | 여유 있게 |
| --- | ---: | ---: |
| 메모리 | 8 GB | 16 GB 이상 |
| 디스크 | 앱용 2 GB | 로컬 모델 포함 20 GB |
| macOS | 14 Sonoma, Apple Silicon 또는 Intel([제한 사항](#intel-mac)) | 14 Sonoma 이상, Apple Silicon |
| Windows | 10(64비트) | 11 |
| Linux | glibc 2.31 | Ubuntu 22.04 이상 |

로컬 모델은 앱 자체보다 훨씬 많은 메모리가 필요합니다. 크기 안내는
[로컬 모델 실행](../../../guides/local-models.md)에 있습니다.

## 앱 다운로드

| 내 컴퓨터 | 다운로드 |
| --- | --- |
| Apple Silicon Mac(M1 이상) | [`VOOL-0.7.7-beta-macos-arm64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/VOOL-0.7.7-beta-macos-arm64.dmg) |
| Intel 칩 Mac | [`VOOL-0.7.7-beta-macos-x86_64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/VOOL-0.7.7-beta-macos-x86_64.dmg) |
| Linux x86_64(Ubuntu 22.04 / 24.04) | [`vool-0.7.7-beta-linux-x86_64.tar.gz`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/vool-0.7.7-beta-linux-x86_64.tar.gz) |

Mac의 칩을 확인하려면 Apple 메뉴를 열고 **이 Mac에 관하여**를 선택하세요. 'Apple M…'이면
Apple Silicon이고, 'Intel'이면 Intel입니다. Apple Silicon용 앱은 Intel Mac에서 실행되지 않으며,
열면 그렇다고 알려 줍니다.

열기 전에 다운로드한 파일의 SHA-256을 확인하세요([다운로드 검증](#다운로드-검증) 참고).

### Mac에서 처음 열기

베타 앱은 아직 Apple 공증을 받지 않아서, 처음 열 때 macOS가 확인을 요청합니다.
예상된 동작이며 한 번만 나타납니다.

1. DMG를 열고 **VOOL**을 **응용 프로그램**으로 드래그합니다.
2. VOOL을 이중 클릭합니다. macOS가 앱에 악성 소프트웨어가 있는지 확인할 수 없다고 알립니다.
   메시지를 닫습니다.
3. **시스템 설정 → 개인정보 보호 및 보안**을 엽니다. 아래로 스크롤해 VOOL에 관한 메시지를 찾고
   **그래도 열기**를 클릭합니다. 확인을 위해 암호를 입력합니다.

macOS 14 Sonoma에서는 대신 응용 프로그램 폴더에서 VOOL을 Control-클릭하고 **열기**를 선택한 다음,
다시 **열기**를 클릭해도 됩니다.

### Linux

릴리스에서 tarball과 해당 `.sha256` 파일을 내려받아 확인한 다음, 함께 들어 있는 설치 프로그램을
실행합니다. sudo나 시스템 Python은 필요하지 않습니다.

```bash
sha256sum -c vool-0.7.7-beta-linux-x86_64.tar.gz.sha256
tar -xzf vool-0.7.7-beta-linux-x86_64.tar.gz
bash vool-0.7.7-beta-linux-x86_64/install.sh
~/.local/bin/vool
```

VOOL은 `~/.local/share/vool`에 설치되고, `vool` 명령을 `~/.local/bin`에 두며,
애플리케이션 메뉴에 항목을 추가합니다. 데스크톱 앱은 브라우저에서 채팅을 열며, 브라우저를 닫아도
VOOL은 멈추지 않습니다. VOOL을 제거하려면 `bash ~/.local/share/vool/current/uninstall.sh`를 실행하세요.
채팅과 설정은 프로필에 남습니다.

0.7.5부터 Linux 다운로드는 VOOL 안에서 업데이트됩니다. 새 버전은 기존 버전 옆에
설치됩니다. 새 버전이 제대로 시작되지 않으면 VOOL이 스스로 이전 버전으로 되돌아갑니다.

### 설치 후

로컬 모델이 답변하려면 먼저 **설정 → 모델과 공급자 → 로컬 모델**을 열고 해당 모델의 **Certify**를 한 번 누르세요. 내 컴퓨터에서 짧은 검사가 실행됩니다.

0.7.5부터 VOOL은 스스로 업데이트합니다. '스스로 온라인 연결'이 켜져 있으면 알림을 표시하고 한 번 눌러 새 버전을 설치합니다. Mac에서 0.7.4나 0.7.3에서 업데이트하는 경우: '스스로 온라인 연결'(설정 > 개인정보와 권한)을 켜고 '업데이트 확인'을 한 번 누르세요. Linux에서 0.7.4 이하, 또는 Mac에서 0.7.2 이하인 경우: 0.7.7을 내려받아 현재 버전 위에 설치하세요. 채팅과 설정은
앱 바깥의 프로필에 저장됩니다. 암호화폐 기능은 기본적으로 꺼져 있습니다.

## 소스에서 설치

사용하는 플랫폼에 맞는 부트스트랩 스크립트를 실행합니다.

```bash
curl -fsSLo bootstrap_vool.sh https://raw.githubusercontent.com/Parad0x-Labs/vool/v0.7.7-beta/installer/bootstrap_vool.sh
bash bootstrap_vool.sh --ref v0.7.7-beta
```

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/Parad0x-Labs/vool/main/installer/bootstrap_vool.ps1 -OutFile bootstrap_vool.ps1
powershell -ExecutionPolicy Bypass -File .\bootstrap_vool.ps1
```

macOS / Linux 명령은 부트스트랩 스크립트와 소스 다운로드를 모두 `v0.7.7-beta`로 고정합니다. 다른 릴리스를 고르려면 스크립트 URL의 태그와 `--ref`를 모두 바꾸세요. 부트스트랩은 브랜치 이름이나 릴리스 태그를 받으며, 커밋 해시는 받지 않습니다.

소스에서 설치하려면 Python 3.11 이상이 필요합니다. 찾지 못하면 설치 프로그램이
설치를 시도합니다. 앱 다운로드에는 자체 Python이 들어 있습니다.

설치 프로그램은 Python 환경을 준비하고, 하드웨어를 확인하고, 로컬 모델을
설정한 뒤 로컬 서비스를 시작합니다. 처음 모델을 내려받는 데 시간과 디스크 공간이 필요합니다.
설정 후에는 로컬 모델 추론이 오프라인에서 작동하며, 웹 도구와 클라우드 모델에는
연결이 필요합니다.

## Intel Mac

VOOL은 macOS 14 이상의 Intel Mac(예: 2020년형 13인치 MacBook Pro)에서
Intel 다운로드나 소스로 실행됩니다. 소스 설치 프로그램은 Intel CPU를 감지하고
모든 의존성의 Intel 빌드를 설치합니다. Intel에만 해당하는 제한이 두 가지 있습니다.

* **로컬 모델 학습 불가.** PyTorch가 2.3 이후로 Intel macOS용 빌드를 내놓지 않아서,
  설치 프로그램은 이를 건너뛰고 LoRA 학습은 사용할 수 없다고 표시합니다. 채팅, 메모리,
  도구, 클라우드 모델은 영향을 받지 않습니다.
* **로컬 모델은 CPU에서만 실행됩니다.** 로컬 모델 실행 프로그램은 Intel 그래픽용 GPU 가속을 지원하지 않아서,
  로컬 답변이 Apple Silicon보다 몇 배 느립니다. 8 GB Mac에서는 채팅에 클라우드 모델을
  사용하세요. 16 GB에서는 설치 프로그램의 기본 소형 로컬 모델이 작동하지만 느립니다.

Intel Mac에서는 로컬 모델이 프로세서에서 실행되어 느립니다. 메모리를 쓰는 긴 답변은 시간 초과될 수 있으니, 당분간은 클라우드 모델을 사용하세요. 로컬 모델 인증은 느린 프로세서를 감안합니다.

Intel Mac용 다운로드는 따로 있습니다: `VOOL-0.7.7-beta-macos-x86_64.dmg`. Apple Silicon용
다운로드는 Intel에서 실행되지 않으며, 시작할 때 그렇다고 알려 줍니다.

## 다운로드 검증

릴리스의 각 다운로드 옆에는 SHA-256 체크섬이 있습니다. 파일을 열기 전에
검증하세요.

```bash
shasum -a 256 <downloaded-artifact>    # macOS
sha256sum <downloaded-artifact>         # Linux
```

```powershell
Get-FileHash -Algorithm SHA256 <downloaded-artifact>
```

결과를 해당 릴리스와 함께 게시된 체크섬과 비교하세요. 서로 다르면
파일을 삭제하고 다시 내려받으세요.

## 다음

[처음 실행](first-run.md)으로 넘어가세요.
