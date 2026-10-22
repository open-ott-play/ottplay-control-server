# OTT-play 컨트롤러 문서

<!-- language-navigation:start -->
<p dir="ltr">
<a href="Home.md" lang="en" dir="ltr">English</a> · <a href="Home.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="Home.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="Home.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="Home.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="Home.es.md" lang="es" dir="ltr">Español</a> · <a href="Home.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="Home.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="Home.he.md" lang="he" dir="rtl">עברית</a> · <a href="Home.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="Home.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="Home.it.md" lang="it" dir="ltr">Italiano</a> · <a href="Home.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="Home.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="Home.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="Home.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="Home.pt.md" lang="pt" dir="ltr">Português</a> · <a href="Home.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="Home.th.md" lang="th" dir="ltr">ไทย</a> · <a href="Home.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="Home.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="Home.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="Home.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="Home.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

버전 관리되는 이 문서 색인은 위키 방식 탐색의 원본입니다. 이를 편집해도 GitHub Wiki 페이지가 자동으로 게시되지 않습니다.

- [플레이어 시작 및 연결](../../README.ko.md)
- [터미널 CLI: 설치 및 명령 참조](../cli.md)
- [신뢰할 수 있는 제어: 저널, Local/Home 재개, 정확한 검증 및 인계](../reliable-control.ko.md)
- [통신 프로토콜/API 계약](../api.md)
- [인증, 운영자 권한 범위 및 전송 보안](../security-design.md)
- [런타임 진단](../remote-diagnostics.md)
- [공유 Android USB 관리자: 설치, 복구 및 마이그레이션](../android-usb-manager.ko.md)
- [워크벤치 및 저장된 증거](../workbench.md)
- [배포 및 영구 저장소](../deployment.md)
- [개발 및 테스트](../development.md)
- [릴리스 절차](../release-workflow.md)

컨트롤러는 정해진 제한 내에서 명령을 전달합니다. 공급자 자격 증명, 카탈로그 식별 정보 및 재생 정책은 플레이어가 관리합니다. 요청 성공 확인서는 실제 장치의 영상과 소리를 확인하는 일을 대신하지 못합니다.
