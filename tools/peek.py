"""개발용 — 실행 중인 자동화 Chromium(9222)에 CDP로 붙어 화면 상태를 들여다본다.

사용: python tools/peek.py [selector...]
      인자 없으면 페이지/프레임 목록 + 주요 입력 컨트롤 덤프.
"""
import sys

from playwright.sync_api import sync_playwright


def main():
    sels = sys.argv[1:]
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://localhost:9222")
        for ctx in browser.contexts:
            for page in ctx.pages:
                print(f"\n=== PAGE: {page.url[:100]}")
                print(f"    title: {page.title()[:80]}")
                for fr in page.frames:
                    print(f"    frame: name={fr.name!r} url={fr.url[:80]}")
                if "hometax" not in page.url:
                    continue
                if sels:
                    for sel in sels:
                        for fr in page.frames:
                            try:
                                n = fr.locator(sel).count()
                                if n:
                                    vis = fr.locator(sel).first.is_visible()
                                    print(f"    {sel}: count={n} visible={vis} (frame={fr.name!r})")
                            except Exception as e:
                                print(f"    {sel}: err {str(e)[:60]}")
                else:
                    try:
                        items = page.evaluate(
                            """() => {
                                const out = [];
                                document.querySelectorAll('input, select').forEach(el => {
                                    if (el.offsetParent === null) return;
                                    if (!el.id.startsWith('mf_txppWframe')) return;
                                    out.push(el.tagName + ':' + (el.type || '') + ' #' + el.id
                                             + ' [' + (el.title || el.value || '').slice(0, 25) + ']');
                                });
                                return out.slice(0, 40);
                            }""")
                        for it in items:
                            print("   ", it)
                    except Exception as e:
                        print("    evaluate err:", str(e)[:80])


if __name__ == "__main__":
    main()
