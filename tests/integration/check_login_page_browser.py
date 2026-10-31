"""Manual browser acceptance: python tests/integration/check_login_page_browser.py.

Requires Playwright + Chromium. Uses the real Page and synthetic bridge data only;
no WhatsApp credentials, remote sockets or messages are used.
"""
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2] / 'pages/whatsapp-login'

class StaticPage(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)
    def log_message(self, *_args):
        pass

BRIDGE = r'''
window.posts = [];
window.testMode = new URLSearchParams(location.search).get('mode') || 'expired';
let status = {ok:true, gatewayHealthy:true, ready:false, status:'qr_expired'};
let qr = {ok:true, ready:false, status:window.testMode==='pairing'?'pairing':'qr_expired'};
window.AstrBotPluginPage = {
 ready:async()=>{}, t:(key,fallback)=>fallback, getLocale:()=> 'en-US', onContext:()=>{},
 apiGet:async(route)=>{
  if(route==='runtime') return {ok:true,ready:true,status:'dependencies_current',node:{version:'24.22.2',supported:true},dependenciesInstalled:true};
  if(route==='status') return {...status};
  if(route==='qr') return {...qr};
  return {ok:true,state:'idle',phase:'idle',pluginVersion:'0.2.49',currentVersion:'0.2.49'};
 },
 apiPost:(route)=>{
  if(route!=='restart') return Promise.resolve({ok:true,state:'idle',phase:'idle',updateAvailable:false});
  window.posts.push(route);
  return new Promise(resolve=>{window.finishRestart=()=>{
   status={...status,status:'qr_pending'};
   qr={ok:true,ready:false,status:'qr_pending',qrDataUrl:'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="16" height="16"/%3E'};
   resolve({ok:true,status:'qr_pending'});
  };});
 }
};
'''

def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), StaticPage)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    checks = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
            page = browser.new_page(viewport={'width':1100,'height':850})
            page.add_init_script(BRIDGE)
            page.goto(f'http://127.0.0.1:{server.server_port}/index.html')
            page.locator('#qrPhase').filter(has_text='QR code expired').wait_for()
            assert page.locator('#qrWrap').inner_text().startswith('⚠')
            checks.append('expired QR is immediately visible, not an indefinite connection spinner')
            page.evaluate("document.querySelector('#refreshQr').click(); document.querySelector('#refreshQr').click(); document.querySelector('#qrWrap button').click()")
            page.wait_for_function('window.posts.length === 1')
            assert page.evaluate('window.posts') == ['restart']
            assert page.locator('#refreshQr').is_disabled()
            assert page.locator('#restart').get_attribute('aria-busy') == 'true'
            checks.append('rapid refresh/retry clicks issue one soft restart and lock the controls')
            page.evaluate('window.finishRestart()')
            page.locator('#qrWrap img').wait_for()
            assert not page.locator('#refreshQr').is_disabled()
            checks.append('new QR replaces expired state and controls recover')
            page.goto(f'http://127.0.0.1:{server.server_port}/index.html?mode=pairing')
            page.locator('#qrPhase').filter(has_text='Signing in').wait_for()
            checks.append('newer pairing state is not overwritten by an earlier expired status response')
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            checks.append('mobile viewport has no horizontal overflow')
            browser.close()
    finally:
        server.shutdown()
    print(json.dumps({'passed':checks,'count':len(checks)}, indent=2))

if __name__ == '__main__':
    main()
