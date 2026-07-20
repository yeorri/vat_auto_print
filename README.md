# 부가세 신고자료 자동 출력 (vat_data_auto)

업체 명부 엑셀을 올리면, 홈택스 **세무대리/납세관리** 메뉴의 부가세 신고자료를
업체별로 자동 조회·출력(기본: 인쇄 / 옵션: PDF 저장)하는 프로그램.

- PDF 모드: 저장 폴더 아래 **업체명 폴더**를 만들고 그 안에 모든 자료 저장.
- 사업자번호가 틀리면(홈택스 alert) 그 업체의 남은 작업을 **전부 건너뛰고**
  다음 업체로 — 로그 + **결과 엑셀**(조회결과_시각.xlsx)에 기록.

## 작업 목록 (phase — 각각 켜고 끌 수 있음)

| # | KEY | 자료 | 상태 (2026-07-14) |
|---|-----|------|------|
| 1 | integrated | 부가세 신고자료 통합조회 | ✅ 구현 (실제 조회 검증) |
| 2·3 | hapgye_sum | (세금)계산서 신고용 합계표 — 전자세금계산서+전자계산서 × 매출·매입 | ✅ 하나의 작업으로 통합 (세트 조회) |
| 4 | card_sales | 신용카드/판매(결제)대행 + 엑셀 | ✅ 구현 (정리본 자동 생성 — Sheet1 상호별 정리·Sheet2 원본) |
| 5 | cash_sales | 현금영수증 매출총액 | ✅ 구현 |
| 7 | export_sales | 수출실적명세서 | ✅ 구현 (예정=분기·확정 O=분기·X=반기 선택) |

※ ⑥ 현금영수증 매입총액(cash_purchase)은 v1.0.5에서 제거 (사용자 불필요 확인 —
⑤와 셀렉터가 동일했음, 필요 시 git 이력에서 복원 가능).

### 신고시즌 × 업체별 예정신고 여부 (필수 열)

GUI에서 **신고시즌(확정신고/예정신고)**을 고르고, 업체별 구분은 명부의 '예정신고' 열이 결정:

| 시즌 | 예정신고 O·1 업체 | 예정신고 X·0 업체 |
|------|------|------|
| **확정신고** (7월·1월) | 확정만 — 합계표 "1기 확정" / 카드·현금 2분기만 | 예정+확정 — 합계표 "1기(예정+확정)" / 카드 1~2분기 / 현금 -전체- |
| **예정신고** (4월·10월) | '예정' — 합계표 "1기 예정" / 카드·현금 1분기만 | **업체 통째로 건너뜀** (결과 엑셀에 기록) |

예정신고 값이 없는 업체가 체크되어 있으면 시작 시 경고 후 중단. '직접 추가'에서도 필수.
현금영수증(⑤⑥)의 '-전체-'는 연간 누계(월별 표시)라 2기 확정신고의 X 업체에선 상반기가
섞여 보임 — 허용(월별 행으로 구분 가능), 필요 시 거래년월 파싱 검토.
사업자번호 열에 주민등록번호(13자리)가 온 업체는 ②③(수임사업자전환 모달)만 가능 —
나머지 phase는 "사업자번호 필요" 사유로 기록.

## 화면 정찰 결과 (2026-07-14, 로그인 상태 실DOM 확인)

직접 URL: `…index_pp.xml&tmIdx=06&tm2lIdx=0602000000&tm3lIdx={메뉴ID}`

| 메뉴ID | 화면 | 핵심 셀렉터 (mf_txppWframe_ 접두 생략) |
|--------|------|------|
| 0602190000 | ① 통합조회 | edtTxnrmY / selectHt / radioRtnClCd_input_0~2 / inputBsno / trigger113(조회) / trigger167(인쇄하기) / txtTxnrm(완료신호) |
| 0602120000 | ②③ 신고용 합계표 | radioEtxivClsfCd(세금계산서·계산서) / radio2(매출·매입) / selectbox3("1기 예정" 등) / trigger24(조회) / trigger301·trigger30(전송기간 내·외 명세서 조회) / trigger33(명세서 출력·OZ 새창) / 완료신호=소계 행 숫자 |
| ↳ 수임사업자전환 모달 | (프레임 `UTEETZZA21_wframe_`) | input18(상호) / selectbox5(사업자·주민등록번호) / txprDscmNoA(번호) / trigger85(조회) / `G_…grdResult___radio_chk_0`(행 선택) / btnProcess(확인) / btnClose(닫기) |
| 0602060000 | ④ 신용카드/판매대행 | edtTxprDscmNo1 / selectStlYr / selectQrtFrom·To / trigger163(조회) / trigger167(인쇄하기 → **Report 뷰어 팝업**(clipReport) → window.print()) / trigger164(판매대행 엑셀) / 자료 없으면 '조회된 결과가 없습니다' alert → 정상 생략 |
| 0602070000 | ⑤ 현금영수증 매출 | selectYr("2026년") / selectQrt(**-전체- 고정**, 사용자 확정) / txprDscmNo1~3 / trigger1(조회) / trigger12(인쇄 → Report 뷰어 팝업) / 완료신호=총합계 행 or 무자료 문구 |
| 0602150000 | ⑥ 현금영수증 매입 | ⑤와 완전 동일 |
| 0602130000 | ⑦ 수출실적명세서 | inputBsno / edtYear("2026") / shpnYmGubun_input_0~2(월·분기·반기 라디오) / edtQrt(1~4분기)·edtHt(상·하반기, 라디오에 따라 전환) / trigger93(조회) / trigger167(인쇄하기, 직접 인쇄형) / 완료신호='총 N건' 줄 변화 or 무자료 문구 |

- 사업자번호 오류 alert: "사업자등록번호를 확인하시기 바랍니다." → fatal 처리
- ④⑤⑥ 무자료: alert가 아니라 **화면 그리드에 '조회된 결과가 없습니다' 문구** (라이브 확인)
  → 화면 텍스트 감지로 처리, 출력 생략 후 정상 종료
- '새창' 인쇄(②④⑤⑥): Report 뷰어(clipReport)/OZ 팝업 → 팝업에 window.print() (kiosk-printing)
- ④ 엑셀은 서버가 구형 .xls(OLE)로 줌 — 정리본 토글 ON(기본)이면 상호별 정리본
  .xlsx(수식)로 재작성, OFF면 원본 .xls 그대로 (구 '월별 누계' 개념은 폐기 —
  홈택스가 처음부터 월별 누계로 제공)

## 구조

```
vat_data_auto/
  gui.py                    # Tkinter GUI — 명부·조건·phase 토글·로그
  browser_setup.py          # 첫 실행 시 Chromium 자동 다운로드
  automation/
    browser.py              # 영구 프로필 Chromium + 로그인 대기
    pipeline.py             # BrowserSession + 업체 loop × phase loop + 결과 엑셀
    hometax.py              # 공용 — URL, 버튼 폴백클릭, 인쇄→PDF, 엑셀 다운로드
    report.py               # 결과 엑셀 작성
    pdf_save.py / util.py / roster.py
    phases/                 # base + 5개 phase (+cash_common)
```

## 실행

```
pip install -r requirements.txt
python gui.py
```

첫 실행에서 홈택스에 직접 로그인(세무대리인 공동인증서)하면 `.profile`에 세션 보존.

## LIVE-TODO (라이브 세션에서 확인/채우기)

- [ ] 전 과정 통짜 검증 (인쇄/PDF 모두) — ②③은 출력 버튼(OZ viewer)만 미실행 상태
- [x] ②③ 매입 라디오 전환 시 표 초기화 → 조회로 채워짐 (사용자 스크린샷으로 확인)
- [ ] ③ 계산서 모드에서 소계/명세 버튼 id 동일한지 확인
- [ ] ②③ OZ viewer 새창 인쇄 동작 확인 (자동인쇄 or window.print() 폴백)
- [x] ④⑤⑥ 조회 완료 감지 — 합계/총합계 행 숫자 or '조회된 결과가 없습니다' 문구
- [x] Report 뷰어 인쇄: window.print()는 팝업 껍데기를 찍음(라이브 확인) →
      뷰어의 `button.report_menu_print_button` 클릭으로 교체 (프레임 폴링 탐색)
- [ ] ②④⑤⑥ 뷰어 인쇄 버튼 경유 출력물이 제대로 나오는지 재테스트
- [x] ④ 엑셀 토글 재정의 — 월별누계(홈택스 기본 제공이라 불필요) → 상호별 정리본 후가공(excel_summary)
- [x] ④ 분기 매핑 사용자 확정 — 예정신고 O: 2분기~2분기 / X: 1분기~2분기
- [x] ⑤⑥ 분기 사용자 확정 — 항상 '-전체-'(연간 누계, 상반기 합계 행 포함)
- [ ] 수임 미확인 업체 처리 — ①의 '수임사업자 조회'(trigger114) 분기

## 배포

검증되면 ingunbi_auto 패턴 그대로: PyInstaller `.spec` + `updater.py` + `version.json`.
