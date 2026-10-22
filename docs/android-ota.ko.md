<div lang="ko" dir="ltr">

# Android OTA: 일반 플레이어와 별도의 root 에이전트

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-ota.md" lang="en" dir="ltr">English</a> · <a href="android-ota.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-ota.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-ota.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-ota.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-ota.es.md" lang="es" dir="ltr">Español</a> · <a href="android-ota.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-ota.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-ota.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-ota.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-ota.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-ota.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-ota.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-ota.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-ota.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-ota.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-ota.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-ota.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-ota.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-ota.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-ota.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-ota.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-ota.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-ota.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

**k8a/fire8 및 다른 Capacitor 플레이어**에는 root 에이전트 명령 대신 일반 플레이어의 Wi-Fi 업데이트 기능을 사용하세요.

```sh
ott k8a update --variant api22
ott k8a update --version v1.1.58-beta.3 --variant api22
ott k8a update --variant api22 --prepare-only
ott k8a update status
```

`--version`을 생략하면 가장 최근에 게시된 베타를 선택합니다. Android 7+에서는 `api22` 대신 `full`을 사용하세요. CLI는 공식 릴리스를 찾고 매니페스트를 확인하며 APK를 준비하고 상태를 폴링한 뒤 한 번만 설치를 요청합니다. Android가 태블릿에서 확인을 요구할 수 있습니다. 유지보수 전에 키오스크를 명시적으로 끄고 보호 설정을 잠금 해제하세요. 이 명령은 해당 정책을 변경하지 않습니다. [전체 CLI 절차](cli.md#capacitor-apk-updates-over-wi-fi)를 참조하세요.

이미 설치된 `AppUpdate` 지원 APK, Wi-Fi, 실행 중이며 컨트롤러에 연결된 플레이어가 필요합니다. 일반적인 후속 업데이트에는 USB가 필요 없지만 오프라인 태블릿이나 멈춘 WebView에는 도달하지 못합니다. 최초 설치, 업데이트 기능 부재, 손상된 앱 또는 네트워크 상실에는 여전히 로컬 복구가 필요할 수 있습니다. 자동 릴리스 스케줄러나 범용 무인 설치 기능을 뜻하지 않습니다.

이후 내용은 준비된 Matricom/API19 `play.ott.kitkat` 설치에 한정된 **별도의 root 권한 a1 유지보수 에이전트**를 설명합니다. 일반 Fire/Capacitor 장치에 이미 설치되었거나 곧바로 사용할 수 있는 기능이 아닙니다.

한 번의 root USB 초기 설정 후 독립 Android 에이전트는 아웃바운드 HTTPS 연결로 서명된 업데이트를 받을 수 있습니다. 자체 업데이트에 정상 WebView가 필요하지 않습니다. 0.1.2 에이전트는 복구 파일을 저장하고 확정 전에 검증하는 APK와 시스템 터치 도우미의 조정된 번들도 지원합니다.

이 가이드는 전체 경로를 설명합니다. 정확한 매니페스트 필드, 제한과 하드웨어 요구 사항은 [네이티브 에이전트 참조](../native/android-agent/README.md#signed-updates-over-wi-fi)를 보세요.

## 업데이트 대상

![OTA 구조: 검토한 릴리스 바이트를 서명하고 직접 HTTPS로 게시한 뒤 독립 에이전트가 검증하고 설치 후 확인합니다. 웹 배포는 별도입니다.](images/ota-architecture.svg)

- **네이티브 에이전트:** `kind: agent`는 자신의 ARMv7 바이너리를 교체합니다. OTT가 비활성화되고 다른 HOME 앱을 쓰는 경우에도 동작합니다.
- **APK만:** `kind: apk`는 데이터를 보존하면서 래퍼를 교체합니다. OTT가 활성화되어 포그라운드에 있어야 합니다. 후보 APK의 내장 도우미는 설치된 시스템 도우미와 정확히 일치해야 합니다.
- **APK와 터치 도우미:** `kind: bundle`은 두 구성 요소를 한 쌍으로 갱신합니다. 구형 에이전트는 먼저 번들을 지원하는 에이전트 전용 업데이트를 받아야 합니다.
- **호스팅 플레이어:** 검증된 웹 산출물을 기존 오리진에 배포한 뒤 실행 중인 플레이어를 새로고침하고 확인합니다. APK 업데이트는 호스팅 JavaScript를 배포하지 않습니다. [릴리스 절차](release-workflow.md)를 참조하세요.
- **컨트롤러와 CLI:** 컨트롤러 배포와 전체 CLI 모듈 설치는 별도로 수행합니다. 어느 쪽 업데이트도 태블릿 코드를 교체하지 않습니다.

펌웨어, 커널, 시스템 WebView, root 브로커와 시작 스크립트는 이 OTA 형식의 범위 밖입니다. [네이티브 참조](../native/android-agent/README.md#bootstrap)는 초기 설정과 복구 경계를 설명합니다. 지원되는 OTA 작업에 네트워크 ADB나 임의 원격 셸은 필요하지 않습니다.

## 검토된 릴리스부터 설치 확인까지

1. **검증된 바이트 선택.** CI 통과 후 검토된 변경을 병합합니다. 원하는 릴리스 산출물을 받아 소스 리비전, 불변 릴리스 증거와 체크섬을 확인합니다. 네이티브 아카이브에서 `agent` 바이너리를 추출하세요. 아카이브 자체는 설치할 수 있는 에이전트 페이로드가 아닙니다.
2. **준비 및 서명.** 초기 설정 때 공개 키를 고정한 기존 Ed25519 개인 키를 사용합니다. 키와 장치 자격 증명은 비공개로 유지합니다.
3. **게시 및 전송 확인.** 페이로드와 `signed.json`만 불변 HTTPS 경로에 게시합니다. 둘 다 HTTP 200으로 바이트를 직접 반환해야 하며 업데이트 도구는 리디렉션을 거부합니다. 패키징 도구의 `verify` 명령을 실행합니다.
4. **업데이트 한 번 전송.** 요청 ID를 기록하고 응답 확인 후 상태를 검사합니다. 응답 상실이나 불확실함은 작업을 무작정 반복할 이유가 아닙니다.
5. **효과 확인.** 새로 읽은 설치 해시와 버전을 준비한 릴리스와 비교합니다. 플레이어 실행 중에는 키오스크 대상, 대기열과 재생 진행도 확인합니다. 실제 영상과 소리는 관찰이 필요합니다.

에이전트 릴리스 예시입니다. 선택한 버전과 실제 경로로 바꾸세요.

```sh
python3 native/android-agent/prepare_update.py prepare \
  --kind agent --version 0.1.2-beta.4 \
  --payload /private/release/agent \
  --url https://updates.example/0.1.2-beta.4/agent \
  --manifest-url https://updates.example/0.1.2-beta.4/signed.json \
  --key /private/update.key --output /private/ota-0.1.2-beta.4

# Publish the prepared agent and signed.json, then verify the published bytes.
python3 native/android-agent/prepare_update.py verify \
  --directory /private/ota-0.1.2-beta.4

ott --receipt --timeout 40 a1 android update \
  https://updates.example/0.1.2-beta.4/signed.json ENVELOPE_SHA256
ott --timeout 30 a1 android status
ott a1 android operation REQUEST_ID
```

`ENVELOPE_SHA256`은 페이로드 해시가 아니라 패키징 도구의 `envelope_sha256`입니다. `accepted`는 요청 접수이며 설치 성공을 의미하지 않습니다. 자체 교체 뒤 기존 작업 영수증이 `unknown`으로 남을 수 있습니다. 새 런타임, 예상 에이전트 버전과 일치하는 설치 해시가 설치 증거입니다. [CLI 참조](cli.md#independent-android-agent)는 독립 네이티브 라우팅과 영수증 처리를 설명합니다.

## APK/도우미 트랜잭션 복구 방식

![APK와 도우미 트랜잭션: 사전 확인, 이전 쌍 저장, 적용, 검증, 확정. 실패하면 이전 쌍을 복원하고 복구가 끝나지 않으면 앱 변경 차단을 유지합니다.](images/ota-transaction.svg)

번들은 정확히 `app.apk`와 `ott-touch-guard`를 포함합니다. 패키징 도구는 `--kind bundle --apk`로 서명된 래퍼 APK에서 바로 만들 수 있습니다. 내장 도우미를 추출하므로 전달하는 두 사본이 일치합니다. [번들 명령과 요구 사항](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates)을 참조하세요.

파일 변경 전 에이전트는 일치하는 설치 쌍, 충분한 공간, 응답하는 포그라운드 OTT, 읽기 전용 시스템 파티션과 APK 다운그레이드 지원 선언을 요구합니다. 잠긴 키오스크에서는 같은 TV 채널 ID나 순서 있는 VPortal ID를 확인합니다. 대기열 길이만 같은 것으로는 부족합니다. 지원하지 않는 잠긴 대기열은 거부합니다. 의도적으로 비활성화한 OTT도 거부하여 사용자의 현재 HOME 앱을 보존합니다.

앱 중지 전에 복구 파일과 미완료 트랜잭션을 영구 저장합니다. 에이전트는 앱과 이전 도우미의 종료를 기다리고 도우미를 원자적으로 교체한 뒤 `/system`을 읽기 전용으로 되돌리고 APK를 설치해 결과를 확인합니다. 적용이나 검증이 실패하면 이전 쌍을 복원합니다. 시작 시 복구는 OTT를 실행하거나 HOME을 차지하지 않고 파일을 복원합니다.

복구를 끝내지 못하면 `updates.recovery_failed`가 true가 됩니다. 진단과 서명된 에이전트 전용 수리는 계속 가능하지만 앱 변경과 재생 워치독은 차단됩니다. 설치를 강행하려고 저장된 트랜잭션을 삭제해서는 안 됩니다. 바이너리 복구는 앱 데이터 마이그레이션을 되돌리지 않으며 펌웨어가 다운그레이드를 거부할 수도 있습니다. [복구 제한](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates)을 참조하세요.

## 결과 및 인수 기록

![검증 차트: CLI 테스트 488개, 릴리스 도구 테스트 576개, OTA 패키징 테스트 6개. 기준 태블릿에는 검증된 새 에이전트가 실행되지만 APK/도우미 실기기 인수는 아직 남아 있습니다.](images/ota-validation.svg)

2026년 10월 14일 구현 감사에서 CLI 테스트 488개(하나 건너뜀), 릴리스 도구 테스트 576개, OTA 패키징 테스트 6개, 네이티브 Go 경합/정적 검사와 ARMv7 빌드가 통과했습니다. 이 수는 테스트 규모이며 커버리지나 성능이 아닙니다. 장애 주입 테스트는 잘못된 아카이브, 중단된 트랜잭션, 부분 복원, 대상 변경과 복구 격리를 다룹니다. 독립 검토에서도 불충분한 대기열 신원 검사와 호스트/장치 APK·도우미 검증 불일치를 찾아 수정했습니다.

2026년 10월 15일 확인 결과:

- OTA 구현은 [컨트롤러 PR #104](https://github.com/open-ott-play/ottplay-control-server/pull/104)에 병합되었고 [v0.1.2-beta.4](https://github.com/open-ott-play/ottplay-control-server/releases/tag/v0.1.2-beta.4)에 포함됩니다.
- 현재와 이전 서명 에이전트 패키지는 [OTA 전달 호스트](https://android-updates.ottplay.here.now/)에 게시되었습니다. 페이로드와 서명 봉투 바이트를 직접 HTTPS로 내려받아 성공적으로 비교했습니다.
- 기준 태블릿은 독립 OTA 채널을 통해 `0.1.1-beta.21`에서 `0.1.2-beta.4`로 갱신됐습니다. 실행 에이전트 버전과 설치 SHA-256이 선택한 릴리스와 일치했고 런타임은 바뀌었지만 OS 부팅 ID는 같았습니다. OTT 앱 프로세스는 여전히 없었고 앱 실행이나 HOME 선택 작업도 보내지 않았습니다.
- 네이티브 상태는 시스템 터치 도우미 해시, 대기 번들 없음과 복구 실패 없음을 보고했습니다. APK 메타데이터를 얻지 못했으므로 이 관찰은 설치 APK의 버전이나 해시를 확정하지 못합니다.
- 조정된 APK/도우미 트랜잭션은 호스트 측 검증을 받았지만 기준 태블릿의 실제 설치, 다운그레이드 복구, 영상과 음성은 아직 인수되지 않았습니다.

이후 각 배포에서는 릴리스 소스/체크섬, 준비된 서명 봉투 해시, 요청 ID, 전후 네이티브 상태와 별도의 실기기 인수 결과를 보존하세요. 다운로드 성공이나 명령 영수증은 이러한 관찰을 대신하지 못합니다.

</div>
