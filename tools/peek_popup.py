"""개발용 — 모든 페이지 나열 + 메인 외 페이지의 프레임/인쇄 버튼 덤프."""
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp("http://localhost:9222")
    for ctx in browser.contexts:
        for p in ctx.pages:
            print(f"\n=== PAGE: {p.url[:120]}")
            try:
                print("    title:", p.title()[:70])
            except Exception:
                pass
            if "index_pp.xml" in p.url:
                continue
            for fr in p.frames:
                print(f"  frame: name={fr.name!r} url={fr.url[:110]}")
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
                                              cls: (el.className || '').toString().slice(0, 45),
                                              t: t.slice(0, 30)});
                                }
                            });
                            return out.slice(0, 15);
                        }""")
                    for it in items:
                        print("    ", it)
                except Exception as e:
                    print("     evaluate err:", str(e)[:70])
