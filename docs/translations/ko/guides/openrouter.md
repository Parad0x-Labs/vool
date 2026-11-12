---
description: OpenRouter를 VOOL에 연결해 API 키 하나로 여러 클라우드 AI 모델을 쓰는 방법.
translation: machine
source_sha256: 3a09fe6c636e2b8dd14a2d627c4653055dbb10bf8fd8221b7a3bcccf3044cb19
---

# OpenRouter 연결

OpenRouter는 키 하나로 여러 공급자에 접근할 수 있게 해 줍니다. VOOL은 OpenRouter 계정 연결을
지원합니다.

## 연결

OpenRouter 계정에서 키를 만들고 **설정 → API 키**에 붙여넣은 다음 **키 저장**을 누르세요. VOOL은
OpenRouter에 키를 한 번 확인한 뒤, 이 컴퓨터의 암호화된 파일에 보관합니다
([보안 모델](../trust/security.md#키) 참고).

## 요금과 사용량 귀속

{% hint style="info" %}
OpenRouter 계정은 계속 내 것입니다. VOOL은 내 키를 사용해 이 컴퓨터에서 바로 요청을 보냅니다.
Parad0x는 프롬프트를 중계하지도, 사용 요금을 대신 내지도 않습니다. 실제 VOOL 사용량은
OpenRouter에서 공개되는 VOOL의 집계 통계에 나타날 수 있지만, 개별 프롬프트와 신원은 공개되지
않습니다.
{% endhint %}

## 연결 해제

**설정 → API 키**에서 키를 제거하세요. 서버 쪽에서도 키를 무효로 만들려면 OpenRouter 계정에서도
키를 폐기하세요.
