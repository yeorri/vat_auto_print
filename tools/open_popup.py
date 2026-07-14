"""개발용 — ② 화면에서 명세서 출력 팝업을 띄우고 내부 프레임/버튼을 덤프."""
import time

from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp("http://localhost:9222")
    ctx = browser.contexts[0]
    page = next((p for p in ctx.pages if "index_pp.xml" in p.url), None)
    assert page, "메인 페이지 없음"
    print("메인:", page.title()[:60], "/ 열린 페이지:", len(ctx.pages))

    msgs = []
    page.on("dialog", lambda d: (msgs.append(d.message), d.accept()))

    before = set(ctx.pages)
    page.evaluate("document.getElementById('mf_txppWframe_trigger301').click()")
    time.sleep(4)
    print("dialogs:", msgs)
    page.evaluate("document.getElementById('mf_txppWframe_trigger33').click()")
    for i in range(12):
        time.sleep(1)
        new = [p for p in ctx.pages if p not in before]
        if new:
            break
    print("dialogs:", msgs, "/ 페이지 수:", len(ctx.pages))
    popup = new[0] if new else None
    if popup is None:
        print("팝업 새 창 없음.")
    else:
        popup.wait_for_load_state("domcontentloaded", timeout=15000)
        time.sleep(3)
        print("팝업:", popup.url[:110])
        for fr in popup.frames:
            print(f"  frame: name={fr.name!r} url={fr.url[:100]}")
            try:
                items = fr.evaluate(
                    """() => {
                        const out = [];
                        document.querySelectorAll('*').forEach(el => {
                            const t = (el.title || el.alt || el.value || '').trim();
                            const idc = (el.id + ' ' + (el.className || '')).toString();
                            if (el.offsetParent === null) return;
                            if (/print|prt|인쇄/i.test(t + ' ' + idc)) {
                                out.push({tag: el.tagName, id: el.id.slice(0, 60),
                                          cls: (el.className || '').toString().slice(0, 40),
                                          t: t.slice(0, 30)});
                            }
                        });
                        return out.slice(0, 15);
                    }""")
                for it in items:
                    print("    ", it)
            except Exception as e:
                print("     evaluate err:", str(e)[:70])
