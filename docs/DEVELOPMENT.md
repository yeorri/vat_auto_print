# 개발 기록 — 부가세 신고자료 자동 출력 (VatAutoPrint)

2026-07-14 하루 동안 설계→구현→라이브 검수 9회→v1.0.2 배포까지 진행한 기록.
다음 개발 세션(기능 추가·버그 수정)의 참고 문서. 셀렉터·화면 사양은 README의
"화면 정찰 결과" 표와 함께 볼 것.

---

## 1. 개요

- **목적**: 업체 명부(엑셀/직접입력)를 등록하면 홈택스 **세무대리/납세관리** 메뉴의
  부가세 신고자료를 업체별로 자동 조회·출력(기본 프린터 인쇄 또는 PDF 저장).
- **처리 자료 7종 / phase 6개**: ①통합조회 / ②③(세금)계산서 신고용 합계표
  (hapgye_sum 하나로 통합 — 전자세금계산서·전자계산서 × 매출·매입) /
  ④신용카드·판매(결제)대행(+정리본 엑셀) / ⑤현금영수증 매출 / ⑥매입 /
  ⑦수출실적명세서(export_sales).
- **스택**: Python + Playwright(영구 프로필 Chromium) + Tkinter + PyInstaller
  + openpyxl/xlrd. incometax_printing / ingunbi_auto에서 검증된 패턴 재사용.
- **전 phase 라이브 검증 완료** (실업체 8곳, 2026년 1기 확정신고 조건).

## 2. 아키텍처

```
gui.py                  Tkinter GUI. 세션 이벤트루프 스레드 + queue 폴링(100ms).
browser_setup.py        첫 실행 시 Chromium 자동 설치(ms-playwright 공용 위치).
updater.py              GitHub raw version.json 체크 → 새 버전 알림.
automation/
  browser.py            launch(persistent context, kiosk-printing), 로그인 감지,
                        공유 프로필(%LOCALAPPDATA%\HometaxAutoShared\.profile)
  pipeline.py           BrowserSession(GUI 수명 유지) + 업체 loop × phase loop
                        + fatal(사업자번호 오류) 시 업체 건너뜀 + 결과 엑셀
  hometax.py            공용 헬퍼 — 아래 "핵심 패턴" 참조
  pdf_save.py           저장 다이얼로그 pywinauto 자동화 (원조 검증 코드 + 파라미터화)
  report.py             결과 엑셀(업체×작업 성공/실패/건너뜀/사유/산출물)
  roster.py             명부 가져오기 (업체명/사업자번호(10 또는 주민13)/예정신고 O·X)
  phases/               base(Inputs/PhaseResult/effective_report_type) + 5개 phase
tools/peek*.py          CDP(localhost:9222)로 실행 중 브라우저 상태 덤프 — 진단 필수 도구
```

- **phase 인터페이스**: `KEY / LABEL / async run(ctx, client, inp, emit, dialogs, stop_check)`
  → `PhaseResult(ok, outputs, reason, fatal, skipped)`.
- **dialogs**: 세션 전역 JS alert/confirm 메시지 누적 리스트(자동 수락) —
  "사업자등록번호를 확인하시기 바랍니다"(→fatal), "조회결과가 없습니다" 등 분기 검출.

## 3. 신고구분 로직 (핵심 도메인 규칙)

- 명부의 **예정신고 여부(O·1 / X·0)가 필수** — 값 없는 업체가 있으면 시작 차단.
- GUI에서 **신고시즌(확정/예정)** 선택. `effective_report_type(client, inp)`:

| 시즌 | O(예정신고 함) | X(안 함) |
|---|---|---|
| 확정 | "확정" — 합계표 "1기 확정", 카드·현금 2분기, 수출 분기(2분기) | "예정+확정" — 합계표 "1기(예정+확정)", 카드 1~2분기, 현금 -전체-, 수출 반기(상반기) |
| 예정 | "예정" — 1분기(수출도 분기 1분기) | **업체 통째로 건너뜀**(pipeline, 결과 엑셀 기록) |

(2기는 각각 4분기/3~4분기/하반기로 평행 이동 — 각 phase의 quarter/period 함수 참조)

- ⑤⑥ 현금영수증에서 확정 업체를 해당 분기만 조회하는 이유: 홈택스 분기 필터로
  예정분(1~3월)이 인쇄물에 섞이는 것과 불필요 출력을 자연 차단.
- 미해결 엣지: 2기 확정신고의 X 업체는 현금영수증 -전체-에 상반기가 섞임(월별 행으로
  구분 가능해 허용) — 필요 시 거래년월 파싱 검토.

## 4. 핵심 기술 패턴 (hometax.py)

### 4.1 WebSquare 입력/클릭 — 레이스와 스크롤 흔들림 대응
- **모든 클릭/입력은 JS 우선**: Playwright locator 조작은 클릭 전 스크롤을 유발해
  고정 헤더/모달과 싸우며 화면이 흔들리고 timeout까지 남(라이브 확인).
  - `js_click(getElementById().click())` / `js_fill(value+input/change 이벤트)` /
    `js_select(옵션 텍스트 매칭+change)` / `check_radio(JS→check→force 3단 폴백)`
  - `click_button(css_id, value_text)`: id 클릭 → JS 클릭 → value 텍스트 폴백
    (trigger 번호 변경 대비).
- **`ws_set_value`**: `WebSquare.util.getComponentById(id).setValue(v)` — DOM이 아닌
  내부 모델에 직접 기록. WebSquare가 입력칸 재초기화로 값을 지우는 레이스(모달에서
  복불복 발생)를 원천 회피. 가장 확실한 입력 수단.

### 4.2 화면 이동
- 직접 URL 패턴: `…index_pp.xml&tmIdx=06&tm2lIdx=0602000000&tm3lIdx={메뉴ID}`
  (메뉴ID = 상단 메뉴 앵커 `menuAtag_06xxxxxxxx`). 메뉴 호버 클릭 자동화 불필요.
- `goto_url(ready=핵심셀렉터)`: 1.5초 + ready 대기(1차 10초/2차 20초) + 0.5초 안정화,
  실패 시 1회 재이동. 로그인 직후 첫 진입이 빈 화면으로 빠지는 현상 대응.
- 탭 선택은 `index_pp.xml` 포함 페이지 우선(공지 팝업 오인 방지).

### 4.3 인쇄 파이프라인 (두 유형)
- **직접 인쇄형(①)**: 인쇄 버튼 → kiosk-printing이 sticky 프린터로 즉시 처리.
- **Report 뷰어 팝업형(②④⑤⑥)**: 팝업(popup.html → iframe reportFrame_0 =
  sesw…/clipreport.do) → 뷰어 인쇄 버튼(`button.report_menu_print_button`,
  id는 랜덤 해시라 클래스로) → 일부 뷰어(②④)는 '인쇄방식(PDF)/인쇄범위' 패널이
  열림 → 패널 [인쇄] 클릭. ⑤⑥ 뷰어는 패널 없이 바로 인쇄.
  - `window.print()`는 뷰어 껍데기를 찍음 — 금지 (최후 폴백만).
  - 뷰어 스크립트 초기화 전 클릭은 씹힘 → 버튼 발견 후 1초 안정화, 클릭 후
    4초간 패널/저장다이얼로그 신호 감시, 무신호면 재클릭(최대 3회, pdf 모드만 —
    `dialog_seen` 콜백이 pdf_save 로그 "다이얼로그 잡음"을 감지해 패널 없는 뷰어의
    이중 인쇄 방지). print 모드는 신호를 알 수 없어 재클릭 안 함.
  - WebSquare는 같은 popupID 창을 **재사용** → "새 창 없음 ≠ 팝업 없음",
    `_wait_report_page`(새 창 폴링 3초 → popup.html/clipreport URL 재탐색).
- **PDF 저장**: kiosk-printing sticky = 'Microsoft Print to PDF'(launch 전 Preferences
  주입) → 저장 다이얼로그를 pywinauto로 백그라운드 처리(pdf_save). 저장 전
  `prepare_target`으로 기존 파일 삭제(잠기면 시각 붙인 새 이름) — 덮어쓰기 확인창과
  OneDrive 잠금('인쇄 실패') 회피. overwrite 감시 0.5초로 단축.

### 4.4 완료 감지 & 무자료 판단
- alert가 아니라 **화면 문구** "조회된 결과가 없습니다"(④⑤⑥) — `no_result_visible`
  / 섹션 개수는 `no_result_count`(자기 텍스트 노드만 세어 중복 방지).
- 그리드 완료: '합계'/'소계'/'총합계' 행(`rows_starting_with`)이 **조회 전 스냅샷과
  달라짐**을 신호로. ⑤⑥은 조회 전에도 '총합계 0' 행이 미리 있어 "숫자 존재" 판정은
  결과 도착 전 0으로 오판함(실사고). `row_total`로 0원 건너뜀.
- 합계표 명세 로딩: 건수 표시 `txtTotal` 변화 감지(상한 3초).
- 무자료 건너뜀 전 `NO_RESULT_PAUSE = 0.5`초 — 사용자가 화면 확인할 짬.

### 4.5 판매대행 엑셀 후가공 (agency_excel.py)
- ④ 다운로드 직후 `excel_summary` 토글(기본 ON)이면 원본 .xls(xlrd로 읽음)를
  재작성: **Sheet1 정리본** — 제목 `< 1기확정 - 업체명 >`, 이중 헤더(병합),
  상호 가나다순 그룹(그룹 내 승인년월순, 통합 연번), 상호별 금액 3열 소계,
  상단 합계·하단 총합계. **Sheet2 원본** 보존. 산출물이 진짜 .xlsx가 됨.
- 합계는 값이 아닌 **살아있는 수식**: 소계 `=SUM(D7:D12)`, 합계/총합계는
  소계 셀들의 합(`=D13+D17+…`), 건수는 `=SUM(C구간)`(소계 행 C는 빈칸이라 안전).
- 형식은 사용자가 수작업으로 만들던 정리본(2026-01 예시 파일)을 그대로 재현.
- 홈택스 '엑셀'의 실체는 구형 .xls(OLE) — download_excel이 suggested_filename
  확장자 반영 + PK 시그니처 검사로 형식을 자동 교정(확장자 불일치로 엑셀이
  열기를 거부하던 문제의 해결).

### 4.6 수임사업자전환 모달 (②③, 프레임 `UTEETZZA21_wframe_`)
- 구분 select(사업자/주민등록번호 — 번호 자릿수 10/13으로 자동) → 번호
  `ws_set_value` → 조회 → **행을 순번이 아닌 사업자번호 매칭으로 선택**
  (`grdResult_cell_{i}_{j}` 텍스트 스캔) — 필터 미적용으로 전체 목록이 떠도 엉뚱한
  업체를 절대 선택하지 않음(실사고: 첫 행 무조건 클릭 → 다른 업체 선택될 뻔).
- 입력→값검증→조회→결과검증 3회 재시도. 전환 후 화면 상단
  "[ 현재 조회되는 사업자는 … ]" 번호 일치 검증(최종 방어선).
- 수임사업자는 세션에 유지 → 같은 업체면 전환 생략(③은 분류 라디오만 전환).

## 5. 문제 해결 기록 (시간순)

| # | 증상 | 원인 | 해결 |
|---|---|---|---|
| 1 | ① 입력란 30초 timeout | 로그인 직후 첫 메뉴 진입이 빈 화면 | goto_url ready 대기+재이동, 로그인 후 0.5초 안정화 |
| 2 | 모달 확인 클릭 실패, 스크롤바 흔들림 | locator 클릭의 스크롤 액션성 검사 vs WebSquare | 모달 내 전부 JS 클릭, 전역 3단 폴백 체계 |
| 3 | 명세서 PDF가 팝업 껍데기로 저장 | window.print() 폴백이 팝업 페이지를 인쇄 | 뷰어 `report_menu_print_button` 클릭으로 교체 |
| 4 | 모달이 엉뚱한 업체(목록 첫 행) 선택 | JS 입력이 WebSquare 모델에 미반영→전체 목록→첫 행 클릭 | 번호 일치 행 선택 + 전환 검증 + (후에) ws_set_value |
| 5 | '인쇄 실패' 다이얼로그 | 같은 파일 존재 + OneDrive 잠금 의심 | prepare_target 사전 삭제/개명 |
| 6 | 뷰어 인쇄 눌렀는데 멈춤 | 인쇄 버튼이 '인쇄방식 패널'을 열 뿐 | 패널 [인쇄] 버튼 추가 클릭 |
| 7 | ⑤⑥ 자료 있는데 "총합계 0 — 생략" | 조회 전 미리 그려진 '총합계 0' 행을 완료로 오판 | 스냅샷 변화 기준 완료감지 (④에도 적용) |
| 8 | 합계표 검색 복불복 실패(행 10개) | 구분 선택 후 입력값을 WebSquare가 지우는 레이스 | ws_set_value + 값검증 + 3회 재시도 |
| 9 | 판매대행 엑셀 안 열림 | 서버가 진짜 구형 .xls(OLE)를 주는데 .xlsx로 저장 | suggested_filename 확장자 반영 + PK 시그니처 검사 |
| 10 | 속도 최적화 후 합계표 인쇄 60초 timeout | 뷰어 초기화 전 클릭이 씹혀 패널 안 열림 | 1초 안정화 + 신호 감시 + 재클릭(dialog_seen 가드) |
| 11 | PDF 저장 후 팝업 닫힘 지연 | 덮어쓰기 확인창 5초 고정 감시(불필요해짐) | overwrite_wait_sec 파라미터화 → 0.5초 |

교훈: **홈택스 WebSquare는 (a) DOM 조작을 지우는 재초기화 레이스, (b) 미리 그려진
0값 행, (c) 재사용되는 팝업, (d) 화면 문구식 오류 표시**가 함정. 완료감지는 반드시
"조회 전 스냅샷과의 변화"로, 입력은 모델 API로, 선택은 값 매칭으로.

## 6. GUI 사양 요약

- 업체 명부: 인라인 추가줄(업체명/사업자번호/예정 O·X/+추가, Enter 지원),
  체크박스 없음 — **목록 = 전부 실행 대상**, 행 클릭 선택은 삭제용(선택/전체 삭제).
- 조회 조건: 과세기간 [년][1기|2기][확정|예정] 한 줄. 신고구분 UI 없음(예정신고 열이 결정).
- 서류 처리: [인쇄|PDF 저장] — 항상 '인쇄'로 시작(저장 안 함). 저장 폴더는 PDF 모드
  또는 신용카드 phase(판매대행 엑셀) 켜면 필수.
- 로그: 진행/결과만 표시(업체 헤더 연보라 굵게, ✓/⚠/💾, 내부 동작 숨김) —
  **원본 전체는 `logs/run_*.log`에 기록**(진단은 이 파일로). 경과 타이머 ⏱.
- 결과 엑셀: `조회결과_시각.xlsx` — 업체×작업 성공/실패/건너뜀/사유/산출물.
- 산출물: PDF는 `저장폴더\업체명\업체명_자료명_2026년1기.pdf`,
  판매대행 엑셀은 `업체명_판매결제대행 매출자료조회.xls`(인쇄 모드면 폴더 루트).

## 7. 배포 (2026-07-14)

| 버전 | 내용 |
|---|---|
| v1.0.0 | 첫 배포 — 자료 6종(phase 5개), 예정신고 여부 기반 신고구분, 결과 엑셀 |
| v1.0.1 | 판매대행 엑셀 정리본(수식), 뷰어 인쇄 재클릭 안정화, 속도 최적화(신호 감지), 로그 간소화+파일 기록, 경과 타이머, 신고시즌, 명부 UI 개편 |
| v1.0.2 | ⑦ 수출실적명세서 추가 (최신) |

- repo: https://github.com/yeorri/vat_auto_print (public, main) — clients.json/
  settings.json/logs/.profile은 .gitignore(PII). git identity는 repo-local 설정.
- 빌드: `pyinstaller vat_auto_print.spec --noconfirm` → `dist/VatAutoPrint/` →
  dist_readme.txt와 함께 zip(~60MB) → `gh release create vX.Y.Z zip --notes-file …`
  (⚠ 한글 노트를 PowerShell here-string으로 직접 넘기면 글롭 오류 — 파일로 넘길 것).
- Chromium 미동봉 — 첫 실행 때 browser_setup이 공용 위치(ms-playwright)에 설치.
  frozen exe가 브라우저를 못 찾던 ingunbi v1.1.0 버그의 수정(gui.py 상단
  PLAYWRIGHT_BROWSERS_PATH 선점)이 처음부터 포함됨.
- 로그인/저장된 비밀번호는 **공유 프로필** `%LOCALAPPDATA%\HometaxAutoShared\.profile`
  — 다른 홈택스 자동화 프로그램과 공유 가능(동시 실행 금지). 기존 프로필 자동 이관.
- 업데이트 배포 절차: updater.CURRENT_VERSION↑ → version.json 갱신 → push →
  빌드/zip → `gh release create vX.Y.Z zip`.

## 8. 남은 과제

- [ ] 판매대행 정리본 — 상호가 여러 곳인 실데이터 라이브 확인
      (로직은 3개 상호 예시 파일로 검증했으나 자동 실행 경로는 단일 상호만 거침)
- [ ] 2기 시즌(내년 1월) 전: 합계표 selectbox3에 "2기 …" 라벨 노출 확인,
      현금영수증 X 업체 -전체- 조회 시 상반기 섞임 처리(거래년월 파싱) 검토
- [ ] 예정신고 시즌(4월/10월) 모드 실사용 검증 (로직만 구현됨)
- [ ] Inputs.report_type 전역 폴백은 사실상 미사용 — 정리 여지
- [ ] (선택) 인건비/양도세도 공유 프로필(HometaxAutoShared)로 전환 — 사용자 요청 시
