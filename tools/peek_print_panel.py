"""개발용 — Report 뷰어의 인쇄방식 패널(인쇄/취소 버튼) 셀렉터 덤프."""
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp("http://localhost:9222")
    for ctx in browser.contexts:
        for p in ctx.pages:
            url = (p.url or "").lower()
            if "clipreport" not in url and "popup.html" not in url:
                continue
            print(f"=== REPORT PAGE: {p.url[:100]}")
            for fr in p.frames:
                try:
                    items = fr.evaluate(
                        """() => {
                            const out = [];
                            document.querySelectorAll(
                                'button, input[type=button], select, input[type=radio]'
                            ).forEach(el => {
                                if (el.offsetParent === null) return;
                                const t = (el.value || el.innerText || el.title || '').trim();
                                const opts = el.tagName === 'SELECT'
                                    ? [...el.options].map(o => o.text).join('|') : '';
                                out.push({tag: el.tagName, id: el.id.slice(0, 55),
                                          cls: (el.className || '').toString().slice(0, 50),
                                          t: t.slice(0, 20), opts: opts.slice(0, 40)});
                            });
                            return out.slice(0, 25);
                        }""")
                    if items:
                        print(f"  frame: {fr.url[:70]}")
                        for it in items:
                            print("    ", it)
                except Exception as e:
                    print("    err:", str(e)[:60])
