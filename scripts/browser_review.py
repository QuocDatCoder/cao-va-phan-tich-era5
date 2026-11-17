"""Chromium acceptance checks against isolated mock services, never user data/CDS."""
from pathlib import Path
from datetime import datetime
import csv
import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output/browser_review' / datetime.now().strftime('%Y%m%d-%H%M%S')


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    from playwright.sync_api import sync_playwright, expect
    OUT.mkdir(parents=True, exist_ok=False)
    api_port, ui_port = free_port(), free_port()
    processes, logs = [], []
    try:
        for mode, port in [('backend', api_port), ('frontend', ui_port)]:
            log = (OUT / (mode+'.log')).open('w', encoding='utf-8')
            logs.append(log)
            command = [sys.executable, '-X', 'utf8', str(ROOT/'scripts/baseline_offline.py'),
                       '--child', mode, '--output-dir', str(OUT), '--port', str(port),
                       '--api-port', str(api_port)]
            processes.append(subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0))
        for port, path in [(api_port, '/health'), (ui_port, '/_stcore/health')]:
            deadline = time.monotonic()+30
            while True:
                try:
                    urllib.request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=1)
                    break
                except OSError:
                    if time.monotonic()>deadline: raise RuntimeError('Isolated server startup failed')
                    time.sleep(.2)
        # Test-only API seed; the production collection form has no Demo option.
        payload = dict(source='mock', variable='temperature', place_name='Hanoi fixture',
            location=dict(latitude=21., longitude=105.), start_date='2024-12-31',
            end_date='2025-01-02', frequency='hourly', request_timezone='UTC+7',
            requested_start='2025-01-01T00:00:00+07:00', requested_end='2025-01-02T23:00:00+07:00')
        request = urllib.request.Request(f'http://127.0.0.1:{api_port}/api/v1/downloads',
            json.dumps(payload).encode(), {'Content-Type':'application/json'})
        created = json.load(urllib.request.urlopen(request, timeout=10))
        cid = created['collection_id']
        deadline = time.monotonic()+15
        while True:
            job = json.load(urllib.request.urlopen(f'http://127.0.0.1:{api_port}/api/v1/jobs/{cid}', timeout=5))
            if job['status'] not in {'queued','running'}: break
            if time.monotonic()>deadline: raise RuntimeError('Mock job timeout')
            time.sleep(.1)
        assert job['status']=='completed', job
        with sync_playwright() as pw:
            executable = Path(os.environ.get('LOCALAPPDATA',''))/'ms-playwright/chromium-1248/chrome-win64/chrome.exe'
            launch = dict(executable_path=str(executable)) if executable.is_file() else {}
            browser = pw.chromium.launch(headless=True, **launch)
            page = browser.new_page(viewport=dict(width=1280,height=900))
            from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
            finished = [0]
            def on_frame(data):
                if isinstance(data, bytes):
                    message = ForwardMsg()
                    message.ParseFromString(data)
                    if message.WhichOneof('type') == 'script_finished' and message.script_finished in {0, 3}:
                        finished[0] += 1
            page.on('websocket', lambda ws: ws.on('framereceived', on_frame))
            def wait_done(before):
                deadline = time.monotonic()+30
                while finished[0] <= before:
                    if time.monotonic()>deadline: raise RuntimeError('Render timeout')
                    page.wait_for_timeout(10)
                page.evaluate('() => new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{ui_port}/?'+urllib.parse.urlencode({'view':'D\u1eef li\u1ec7u \u0111\u00e3 l\u01b0u'}))
            if '--headers-only' in sys.argv:
                nav=page.locator('.st-key-workflow_section')
                measurements=[]
                for width in [1280,620,390]:
                    page.set_viewport_size(dict(width=width,height=900))
                    positions=[]
                    for tab,title in [('Thu thập','Thu thập'),('Kho dữ liệu','Dữ liệu đã lưu'),('Biểu đồ','Biểu đồ')]:
                        before=finished[0]
                        nav.get_by_text(tab,exact=True).click()
                        wait_done(before)
                        heading=page.locator('.st-key-workflow_section_header').get_by_role('heading',name=title,exact=True)
                        expect(heading).to_be_visible()
                        a,b=nav.bounding_box(),heading.bounding_box()
                        positions.append(dict(title=title,x=b['x'],offset=b['y']-a['y']-a['height']))
                    assert max(p['offset'] for p in positions)-min(p['offset'] for p in positions)<1,positions
                    assert max(p['x'] for p in positions)-min(p['x'] for p in positions)<1,positions
                    measurements.append(dict(width=width,positions=positions))
                    page.screenshot(path=str(OUT/f'headers-{width}.png'),full_page=True)
                (OUT/'result.json').write_text(json.dumps(dict(passed=True,measurements=measurements),ensure_ascii=False,indent=2),encoding='utf-8')
                browser.close()
                print(json.dumps(dict(passed=True,output=str(OUT),scope='headers-only')))
                return
            page.get_by_role('button',name='Xem d\u1eef li\u1ec7u',exact=True).click(timeout=30000)
            export = page.get_by_role('button',name='T\u1ea3i CSV',exact=True)
            export.wait_for()
            if '--search-only' in sys.argv:
                page.get_by_role('textbox',name='Tìm bộ dữ liệu đã lưu',exact=True).fill('mock')
                before_search=finished[0]
                page.get_by_role('button',name='Tìm dữ liệu',exact=True).click()
                wait_done(before_search)
                expect(page.locator('.st-key-stored_library_cards details')).to_have_attribute('open', '')
                before_clear=finished[0]
                page.get_by_role('button',name='Xóa tìm kiếm',exact=True).click()
                wait_done(before_clear)
                expect(page.locator('.st-key-stored_library_cards details')).to_have_attribute('open', '')
            page.reload()
            export.wait_for()
            page.get_by_role('button',name='Quay lại danh sách',exact=True).click()
            expect(export).to_have_count(0)
            page.get_by_role('button',name='Xem d\u1eef li\u1ec7u',exact=True).click()
            export.wait_for()
            expect(page.locator('.st-key-stored_library_cards details')).not_to_have_attribute('open', '')
            detail = page.locator('.st-key-stored_collection_detail')
            page.wait_for_function("() => { const el = document.querySelector('.st-key-stored_collection_detail'); return el && el.getBoundingClientRect().top >= 50 && el.getBoundingClientRect().top < 160; }")
            with page.expect_download() as download:
                export.click()
            download.value.save_as(str(OUT/'download.csv'))
            with (OUT/'download.csv').open(encoding='utf-8-sig') as handle:
                rows = list(csv.DictReader(handle))
            assert len(rows)==48 and {row['collection_id'] for row in rows}=={cid}
            page.screenshot(path=str(OUT/'stored.png'),full_page=True)
            before = finished[0]
            page.get_by_role('button',name='V\u1ebd bi\u1ec3u \u0111\u1ed3 t\u1eeb d\u1eef li\u1ec7u n\u00e0y',exact=True).click()
            wait_done(before)
            page.locator('.js-plotly-plot').wait_for()
            plot = page.locator('.js-plotly-plot')
            if '--search-only' in sys.argv:
                page.get_by_role('textbox',name='Tìm bộ dữ liệu đã lưu',exact=True).fill('mock')
                before_search=finished[0]
                page.get_by_role('button',name='Tìm dữ liệu',exact=True).click()
                wait_done(before_search)
                expect(page.locator('.st-key-chart_library_cards details')).to_have_attribute('open', '')
                before_clear=finished[0]
                page.get_by_role('button',name='Xóa tìm kiếm',exact=True).click()
                wait_done(before_clear)
                result = dict(passed=True, checks=['search opens stored library', 'clear opens stored library', 'search opens chart library', 'clear search'])
                (OUT/'result.json').write_text(json.dumps(result),encoding='utf-8')
                browser.close()
                print(json.dumps(dict(passed=True, output=str(OUT), scope='search-only')))
                return
            filters = page.locator('.st-key-chart_filters_'+cid+' details')
            expect(filters).not_to_have_attribute('open', '')
            before_open = finished[0]
            page.get_by_text('Bộ lọc biểu đồ',exact=True).click()
            wait_done(before_open)
            draw = page.get_by_role('button',name='Vẽ dữ liệu đã chọn',exact=True)
            assert draw.bounding_box()['y'] < plot.bounding_box()['y']
            expect(page.get_by_text('Từ ngày (UTC+7)',exact=True)).to_be_visible()
            for label in ['Ng\u00e0y','Th\u00e1ng','N\u0103m']:
                filters = page.locator('.st-key-chart_filters_'+cid+' details')
                if filters.get_attribute('open') is None:
                    before_open = finished[0]
                    page.get_by_text('Bộ lọc biểu đồ',exact=True).click()
                    wait_done(before_open)
                    expect(filters).to_have_attribute('open', '')
                page.get_by_role('combobox',name='Chu k\u1ef3',exact=True).click()
                try:
                    page.get_by_role('option',name=label,exact=True).click(timeout=5000)
                except Exception:
                    page.screenshot(path=str(OUT/'failure.png'),full_page=True)
                    (OUT/'failure-text.txt').write_text(page.locator('body').inner_text(),encoding='utf-8')
                    raise
                before = finished[0]
                page.get_by_role('button',name='V\u1ebd d\u1eef li\u1ec7u \u0111\u00e3 ch\u1ecdn',exact=True).click()
                wait_done(before)
                page.locator('.js-plotly-plot').wait_for()
                expect(filters).not_to_have_attribute('open', '')
                page.wait_for_timeout(300)
                (OUT/'chart-position.json').write_text(json.dumps(page.locator('.js-plotly-plot').bounding_box()),encoding='utf-8')
                page.screenshot(path=str(OUT/'chart-position.png'),full_page=True)
                page.wait_for_function("() => { const el = document.querySelector('.st-key-chart_collection_detail'); return el && el.getBoundingClientRect().top >= 50 && el.getBoundingClientRect().top < 160; }")
                page.screenshot(path=str(OUT/'chart-collapsed.png'),full_page=True)
            page.get_by_text('B\u1ea3ng d\u1eef li\u1ec7u v\u00e0 t\u1ea3i CSV',exact=True).click()
            page.get_by_role('button',name='T\u1ea3i b\u1ea3ng d\u1eef li\u1ec7u (CSV)',exact=True).wait_for()
            page.screenshot(path=str(OUT/'chart.png'),full_page=True)
            nav = page.locator('.st-key-workflow_section')
            nav.get_by_text('Kho dữ liệu',exact=True).click()
            export.wait_for()
            nav.get_by_text('Biểu đồ',exact=True).click()
            page.locator('.js-plotly-plot').wait_for()
            page.reload()
            page.locator('.js-plotly-plot').wait_for()
            nav = page.locator('.st-key-workflow_section')
            nav.get_by_text('Kho d\u1eef li\u1ec7u',exact=True).click()
            expect(export).to_have_count(0)
            page.get_by_role('button',name='Xem d\u1eef li\u1ec7u',exact=True).click()
            export.wait_for()
            page.set_viewport_size(dict(width=390,height=900))
            page.wait_for_timeout(1600)
            heading = page.get_by_role('heading',name='D\u1eef li\u1ec7u \u0111\u00e3 l\u01b0u',exact=True)
            heading.scroll_into_view_if_needed()
            refresh = page.get_by_role('button',name='L\u00e0m m\u1edbi',exact=True)
            left,right=heading.bounding_box(),refresh.bounding_box()
            assert abs(left['y']+left['height']/2-right['y']-right['height']/2)<20
            assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
            page.screenshot(path=str(OUT/'portrait.png'),full_page=True)
            page.get_by_text('Ch\u1ecdn b\u1ed9 d\u1eef li\u1ec7u kh\u00e1c',exact=False).click()
            page.get_by_role('button',name='X\u00f3a',exact=True).click()
            page.get_by_role('button',name='X\u00e1c nh\u1eadn x\u00f3a',exact=True).click()
            expect(page.get_by_role('button',name='X\u00e1c nh\u1eadn x\u00f3a',exact=True)).to_have_count(0)
            expect(export).to_have_count(0)
            page.get_by_text('Ch\u01b0a c\u00f3 b\u1ed9 d\u1eef li\u1ec7u ph\u00f9 h\u1ee3p.',exact=False).wait_for()
            assert not errors,errors
            assert not page.locator('[data-testid="stException"]').count()
            browser.close()
        result=dict(passed=True,errors=errors,collection_id=cid,csv_rows=len(rows),
                    checks=['stored','CSV','hour/day/month/year','chart','F5','portrait refresh alignment','confirmed deletion'])
        (OUT/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(passed=True,output=str(OUT))))
    except Exception as exc:
        (OUT/'result.json').write_text(json.dumps(dict(passed=False,error=type(exc).__name__,message=str(exc)),ensure_ascii=False,indent=2),encoding='utf-8')
        raise
    finally:
        for process in processes: process.terminate()
        for process in processes: process.wait(timeout=15)
        for log in logs: log.close()


if __name__=='__main__':
    main()
