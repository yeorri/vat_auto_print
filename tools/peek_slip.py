"""개발용 — 신고내역 조회(접수증·납부서) 모달 라이브 정찰.

실행 중인 자동화 Chromium(9222)에 붙어 사업자번호 조회를 실제로 수행하고
알림 모달·그리드·보기 버튼 구조를 덤프한다.
사용: python tools/peek_slip.py <사업자번호> [--view]
      --view: 최신 행의 납부서 [보기]까지 눌러 팝업 URL 확인 (인쇄는 안 함)
"""
import io
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright

PRE = "mf_txppWframe_UTERNAAZ0Z31_wframe_"
BIZNO_INPUT = f"{PRE}input_txprRgtNo_UTERNAAZ31"
BTN_SEARCH = f"{PRE}trigger70_UTERNAAZ31"


def dump_notice(page):
    """보이는 알림 레이어 구조 덤프."""
    return page.evaluate(
        """() => {
            const out = [];
            for (const el of document.querySelectorAll('div, span, p, td')) {
                if (!el.offsetParent) continue;
                const own = [...el.childNodes].filter(c => c.nodeType === 3)
                    .map(c => c.textContent).join('').trim();
                if (!own) continue;
                if (own.includes('완료') || own.includes('확인하세요')
                    || own.includes('없습니다')) {
                    let root = el, hops = 0;
                    while (root.parentElement && hops < 10) {
                        const cls = String(root.className || '');
                        if (cls.includes('w2window') || cls.includes('w2alert')
                            || cls.includes('w2messageBox')) break;
                        root = root.parentElement; hops++;
                    }
                    const btns = [...root.querySelectorAll('a,button,input')].map(
                        b => (b.tagName + '#' + (b.id || '?') + '['
                              + (b.value || b.innerText || '').trim().slice(0, 10)
                              + ']'));
                    out.push({msg: own.slice(0, 60),
                              rootCls: String(root.className || '').slice(0, 80),
                              rootId: root.id || '', btns: btns.slice(0, 8)});
                }
            }
            return out;
        }""")


def main():
    bizno = sys.argv[1] if len(sys.argv) > 1 else ""
    do_view = "--view" in sys.argv
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://localhost:9222")
        page = None
        for ctx in browser.contexts:
            for p in ctx.pages:
                if "index_pp.xml" in p.url:
                    page = p
        if page is None:
            print("hometax 페이지 없음")
            return
        print("PAGE:", page.url[:100])

        n = page.evaluate(
            f"() => document.getElementById('{BIZNO_INPUT}') ? 1 : 0")
        print("모달 입력칸 존재:", n)
        if not n:
            print("모달이 안 열려 있음 — GUI에서 모달을 연 상태로 실행하세요.")
            return

        if bizno:
            ok = page.evaluate(
                """([id, v]) => {
                    const el = document.getElementById(id);
                    el.focus(); el.value = v;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    el.blur();
                    return el.value === v;
                }""", [BIZNO_INPUT, bizno])
            print("번호 입력:", ok)
            page.evaluate(
                f"""() => document.getElementById('{BTN_SEARCH}')
                    .dispatchEvent(new MouseEvent('click', {{bubbles: true}}))""")
            print("조회 클릭 — 3초 대기")
            time.sleep(3)

        for round_ in (1, 2):
            notices = dump_notice(page)
            print(f"알림 레이어({round_}):")
            for it in notices:
                print("   ", it)
            if notices:
                # 확인 버튼 클릭 시도
                clicked = page.evaluate(
                    """() => {
                        for (const b of document.querySelectorAll(
                                'a, button, input[type=button]')) {
                            if (!b.offsetParent) continue;
                            const t = (b.value || b.innerText || '').trim();
                            if (t === '확인') {
                                b.dispatchEvent(new MouseEvent('click',
                                    {bubbles: true}));
                                return b.id || t;
                            }
                        }
                        return '';
                    }""")
                print("    확인 클릭:", clicked)
                time.sleep(1.5)
            else:
                break

        # 그리드 구조: '보기' 포함 요소와 그 행
        grid = page.evaluate(
            """() => {
                const out = {links: [], rows: [], headers: []};
                for (const el of document.querySelectorAll('a,button,input,span')) {
                    if (!el.offsetParent) continue;
                    const t = (el.value || el.innerText || '').trim();
                    if (t === '보기') {
                        const tr = el.closest('tr');
                        out.links.push({tag: el.tagName, id: el.id || '',
                                        trId: tr ? (tr.id || '') : null});
                    }
                }
                // 행 텍스트 (보기 포함 행만, 중복 제거)
                const seen = new Set();
                for (const l of out.links) {
                    if (!l.trId || seen.has(l.trId)) continue;
                    seen.add(l.trId);
                    const tr = document.getElementById(l.trId);
                    if (tr) out.rows.push({id: l.trId,
                        cells: [...tr.children].map(td =>
                            (td.id || '') + '=' +
                            td.innerText.replace(/\\s+/g, ' ').trim().slice(0, 22))});
                }
                // 헤더 후보
                for (const th of document.querySelectorAll('td, th')) {
                    if (!th.offsetParent) continue;
                    const t = th.innerText.trim();
                    if (t === '납부서' || t === '접수증' || t === '접수일시')
                        out.headers.push((th.id || '?') + '=' + t);
                }
                return out;
            }""")
        print("보기 버튼:", len(grid["links"]))
        for l in grid["links"]:
            print("   ", l)
        print("헤더:", grid["headers"])
        for r in grid["rows"]:
            print("행", r["id"])
            for c in r["cells"]:
                print("     ", c)

        if do_view and grid["links"]:
            target = grid["links"][-1]
            print("납부서 보기 클릭:", target)
            before = list(page.context.pages)
            page.evaluate(
                """(id) => document.getElementById(id).dispatchEvent(
                    new MouseEvent('click', {bubbles: true}))""", target["id"])
            time.sleep(4)
            for p2 in page.context.pages:
                if p2 not in before:
                    print("새 팝업:", p2.url[:100])


if __name__ == "__main__":
    main()
