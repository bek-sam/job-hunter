"""The apply browser: your real Google Chrome with a separate profile (~/.jobhunter/chrome-profile)
and a local DevTools port. Scripts attach to it with Playwright, open/fill tabs, then detach --
the tabs stay open for you to review and click Submit. Sign in to Workday/LinkedIn/etc. in this
window once; the sessions are remembered.

  python browser.py        # start it (or bring it up) -- do this once and sign in to your job sites
"""
import json, subprocess, time, urllib.request
import config

CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'


def running():
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{config.CDP_PORT}/json/version', timeout=2) as r:
            return json.loads(r.read()).get('webSocketDebuggerUrl')
    except Exception:
        return None


def ensure():
    if running(): return True
    config.CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([CHROME, f'--remote-debugging-port={config.CDP_PORT}', '--remote-debugging-address=127.0.0.1',
                      f'--user-data-dir={config.CHROME_PROFILE}', '--no-first-run', '--no-default-browser-check',
                      # no extensions: Oct 12 filled forms were submitted after our script detached (Claude in
                      # Chrome / Sider / Career Clipper were installed here and could act on the tabs)
                      '--disable-extensions',
                      'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(40):
        time.sleep(0.5)
        if running(): return True
    return False


def connect(p):
    """p = sync_playwright() instance -> (browser, context). Never call browser.close(): just leave the
    `with sync_playwright()` block -- Chrome was started by us, so it (and its open tabs) keeps running."""
    if not ensure(): raise RuntimeError('Chrome apply window did not start')
    # a Chrome with zero tabs has no window/context and CDP connect fails ("Browser context management is not
    # supported", Oct 12 after cleanup closed every tab) -> open a blank tab first
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{config.CDP_PORT}/json', timeout=3) as r:
            if not [t for t in json.loads(r.read()) if t.get('type') == 'page']:
                urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{config.CDP_PORT}/json/new?about:blank', method='PUT'), timeout=5)
                time.sleep(1)
    except Exception:
        pass
    b = p.chromium.connect_over_cdp(f'http://127.0.0.1:{config.CDP_PORT}')
    return b, b.contexts[0]


if __name__ == '__main__':
    print('apply browser up' if ensure() else 'failed to start Chrome')
