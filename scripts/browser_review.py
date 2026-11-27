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
            script = 'baseline_offline.py' if mode == 'backend' else 'review_ai_chat.py'
            command = [sys.executable, '-X', 'utf8', str(ROOT/'scripts'/script),
                       '--child', mode, '--output-dir', str(OUT), '--port', str(port),
                       '--api-port', str(api_port)]
            processes.append(subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                env={**os.environ, 'AUTH_ENABLED': 'false', 'AUTH_DB_PATH': str(OUT/'scratch'/'auth.sqlite3')},
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
            base = f'http://127.0.0.1:{ui_port}/'
            if '--home-only' in sys.argv:
                page.goto(base)
                home = page.get_by_role('heading', name='Khám phá dữ liệu khí tượng', exact=True)
                expect(home).to_be_visible()
                hero = page.locator('.st-key-era5_home_hero')
                canvas = hero.locator('canvas')
                expect(canvas).not_to_have_attribute('width', '300')
                box = hero.bounding_box()
                page.mouse.click(box['x'] + box['width'] * .51, box['y'] + box['height'] * .95)
                expect(hero.locator('canvas[data-effect]')).to_have_count(0)
                for pointer in ('mouse', 'touch'):
                    cloud_box = canvas.bounding_box()
                    for fx, fy in ((.88, .83), (.86, .91), (.94, .73), (.89, .34), (.96, .18)):
                        x = cloud_box['x'] + cloud_box['width'] * fx
                        y = cloud_box['y'] + cloud_box['height'] * fy
                        if pointer == 'touch':
                            cdp = page.context.new_cdp_session(page)
                            cdp.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': x, 'y': y}]})
                            cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
                            cdp.detach()
                        else:
                            page.mouse.click(x, y)
                        if canvas.get_attribute('data-effect'):
                            break
                    expect(canvas).to_have_attribute('data-effect', 'dissolve')
                expect(hero.locator('canvas[data-effect]')).to_have_count(0, timeout=32000)
                expect(page.locator('.st-key-home_preview_plot .js-plotly-plot')).to_be_visible()
                columns = page.locator('.st-key-era5_home_panels [data-testid="stColumn"]')
                assert abs(columns.nth(0).bounding_box()['height'] - columns.nth(1).bounding_box()['height']) < 2
                page.screenshot(path=str(OUT/'home-desktop.png'), full_page=True)
                page.get_by_role('button', name='Bật chế độ tối').click()
                expect(canvas).to_have_attribute('data-poster', 'night')
                overlay = hero.locator('.era5-home-clouds').evaluate(
                    "(el) => getComputedStyle(el, '::after').backgroundImage")
                assert 'data:image/svg+xml;base64,' in overlay
                assert hero.get_by_role('button').first.bounding_box()['height'] <= 40
                page.screenshot(path=str(OUT/'home-dark.png'), full_page=True)
                page.get_by_role('button', name='Bật chế độ sáng').click()
                expect(canvas).to_have_attribute('data-poster', 'day')
                preview = page.locator('.st-key-era5_home_preview')
                preview.get_by_text('Lượng mưa', exact=True).click()
                expect(page.get_by_text('Chưa có dữ liệu lượng mưa trong các bộ gần đây để xem nhanh.')).to_be_visible()
                preview.get_by_text('Nhiệt độ', exact=True).click()
                expect(page.locator('.st-key-home_preview_plot .js-plotly-plot')).to_be_visible()
                nav = page.get_by_role('complementary', name='Điều hướng khí tượng')
                for name, title in (('Thu thập', 'Thu thập'), ('Kho dữ liệu', 'Dữ liệu đã lưu'),
                                    ('Biểu đồ', 'Biểu đồ'), ('Trang chủ', 'Khám phá dữ liệu khí tượng')):
                    before = finished[0]
                    nav.get_by_role('button', name=name, exact=True).click()
                    wait_done(before)
                    expect(page.locator('html[data-weahub-destination]')).to_have_count(0)
                    expect(page.get_by_role('heading', name=title, exact=True)).to_be_visible()
                    if name != 'Trang chủ':
                        expect(hero).to_have_count(0)
                for label, title in [('Bắt đầu thu thập', 'Thu thập'), ('Mở kho dữ liệu', 'Dữ liệu đã lưu'),
                                     ('Mở trong Biểu đồ', None)]:
                    before = finished[0]
                    page.locator('.st-key-workflow_content').get_by_role('button', name=label, exact=True).click()
                    wait_done(before)
                    if title:
                        expect(page.get_by_role('heading', name=title, exact=True)).to_be_visible()
                    else:
                        expect(page.locator('.st-key-chart_result .js-plotly-plot')).to_be_visible()
                    before = finished[0]
                    nav.get_by_role('button', name='Trang chủ', exact=True).click()
                    wait_done(before)
                    expect(home).to_be_visible()
                page.set_viewport_size(dict(width=390, height=844))
                expect(page.get_by_role('heading', name='Dữ liệu gần đây', exact=True)).to_be_visible()
                footer = page.locator('.st-key-era5_home_sources')
                expect(footer.get_by_text('Thời gian hiển thị theo giờ Việt Nam (UTC+7).')).to_be_visible()
                expect(footer.get_by_role('link', name='Open-Meteo Historical Forecast')).to_be_visible()
                assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
                hero.scroll_into_view_if_needed()
                page.screenshot(path=str(OUT/'home-portrait.png'), full_page=True)
                browser.close()
                print(json.dumps(dict(passed=True, output=str(OUT), scope='home-only')))
                return
            page.goto(base+'?'+urllib.parse.urlencode({'view':'D\u1eef li\u1ec7u \u0111\u00e3 l\u01b0u'}))
            if '--chart-draft-only' in sys.argv:
                nav = page.get_by_role('complementary', name='Điều hướng khí tượng')
                chart = page.locator('.js-plotly-plot')
                filters = page.locator('.st-key-chart_filters_'+cid+' details')
                for section in ('Kho dữ liệu', 'Biểu đồ'):
                    tab = nav.get_by_role('button', name=section, exact=True)
                    if tab.get_attribute('aria-current') != 'page':
                        before = finished[0]
                        tab.click()
                        wait_done(before)
                    before = finished[0]
                    page.get_by_role('button', name='Vẽ biểu đồ', exact=True).click()
                    wait_done(before)
                    expect(filters).to_have_attribute('open', '')
                    expect(chart).to_have_count(0)
                    before = finished[0]
                    page.get_by_role('button', name='Quay lại danh sách', exact=True).click()
                    wait_done(before)
                before = finished[0]
                nav.get_by_role('button', name='Kho dữ liệu', exact=True).click()
                wait_done(before)
                before = finished[0]
                page.get_by_role('button', name='Xem dữ liệu', exact=True).click()
                wait_done(before)
                page.get_by_role('button', name='Vẽ biểu đồ từ dữ liệu này', exact=True).click()
                expect(chart).to_be_visible()
                expect(filters).not_to_have_attribute('open', '')
                browser.close()
                print(json.dumps(dict(passed=True, output=str(OUT), scope='chart-draft-only')))
                return
            if '--job-history-only' in sys.argv:
                nav = page.get_by_role('complementary', name='Điều hướng khí tượng')
                nav.get_by_role('button', name='Thu thập', exact=True).click()
                history = page.locator('.st-key-collection_job_history details')
                choice = page.get_by_role('combobox', name='Xem chi tiết tác vụ')
                expect(history).not_to_have_attribute('open', '')
                expect(choice).not_to_be_visible()
                history.locator('summary').click()
                choice.click()
                page.get_by_role('option').first.click()
                expect(history.get_by_text('Thông tin kỹ thuật', exact=True)).to_be_visible()
                history.get_by_role('button', name='Mở dữ liệu', exact=True).click()
                expect(page.get_by_role('button', name='Tải CSV theo giờ', exact=True)).to_be_visible()
                browser.close()
                print(json.dumps(dict(passed=True, output=str(OUT), scope='job-history-only')))
                return
            if '--headers-only' in sys.argv:
                nav=page.get_by_role('complementary', name='Điều hướng khí tượng')
                measurements=[]
                for width in [1280,620,390]:
                    page.set_viewport_size(dict(width=width,height=900))
                    positions=[]
                    for tab,title in [('Thu thập','Thu thập'),('Kho dữ liệu','Dữ liệu đã lưu'),('Biểu đồ','Biểu đồ')]:
                        before=finished[0]
                        if width <= 760:
                            page.get_by_role('button', name='Mở menu điều hướng').click()
                        nav.get_by_role('button', name=tab, exact=True).click()
                        wait_done(before)
                        heading=page.locator('.st-key-workflow_section_header').get_by_role('heading',name=title,exact=True)
                        expect(heading).to_be_visible()
                        box=heading.bounding_box()
                        positions.append(dict(title=title,x=box['x'],offset=box['y']))
                    assert max(p['offset'] for p in positions)-min(p['offset'] for p in positions)<1,positions
                    assert max(p['x'] for p in positions)-min(p['x'] for p in positions)<1,positions
                    measurements.append(dict(width=width,positions=positions))
                    page.screenshot(path=str(OUT/f'headers-{width}.png'),full_page=True)
                (OUT/'result.json').write_text(json.dumps(dict(passed=True,measurements=measurements),ensure_ascii=False,indent=2),encoding='utf-8')
                browser.close()
                print(json.dumps(dict(passed=True,output=str(OUT),scope='headers-only')))
                return
            launcher_line = page.locator('.pet-launch-lines span')
            expect(launcher_line).to_have_text('Chào bạn, mình là ERA ☁️', timeout=12000)
            page.get_by_role('textbox', name='Tìm bộ dữ liệu đã lưu', exact=True).focus()
            expect(launcher_line).to_have_text('')
            page.get_by_role('button',name='Xem d\u1eef li\u1ec7u',exact=True).click(timeout=30000)
            export = page.get_by_role('button',name='T\u1ea3i CSV theo gi\u1edd',exact=True)
            export.wait_for()
            date_filter = page.get_by_role('group', name='Lọc giờ theo ngày', exact=True)
            date_filter.get_by_role('spinbutton', name='day, Lọc giờ theo ngày').focus()
            date_filter.evaluate("""el => {
                const clipboardData = new DataTransfer();
                clipboardData.setData('text/plain', '02/01/2025');
                document.activeElement.dispatchEvent(new ClipboardEvent('paste', {
                    bubbles: true, cancelable: true, clipboardData
                }));
            }""")
            clear_day = page.get_by_role('button', name='Xóa ngày', exact=True)
            expect(clear_day).to_be_visible()
            clear_day.click()
            expect(clear_day).to_have_count(0)
            date_widget = page.get_by_test_id('stDateInput').filter(has=date_filter)
            date_widget.get_by_test_id('stDateInputCalendarButton').click()
            calendar = page.get_by_test_id('stDateInputCalendar')
            expect(calendar.get_by_role('button', name='Tháng', exact=True)).to_be_visible()
            expect(calendar.get_by_role('button', name='Năm', exact=True)).to_be_visible()
            page.keyboard.press('Escape')
            back = page.get_by_role('button', name='Quay lại danh sách', exact=True)
            search = page.get_by_role('textbox', name='Tìm bộ dữ liệu đã lưu', exact=True)
            expect(search).to_have_count(0)
            expect(page.get_by_role('button', name='Làm mới', exact=True)).to_have_count(0)
            stored_filter = page.get_by_text('Bộ lọc dữ liệu', exact=True)
            quality = page.get_by_text('Thông tin thu thập và chất lượng', exact=True)
            assert stored_filter.bounding_box()['y'] < export.bounding_box()['y'] < quality.bounding_box()['y']
            filter_panel = page.locator('.st-key-stored_filters_' + cid + ' details')
            expect(filter_panel).not_to_have_attribute('open', '')
            stored_filter.click()
            expect(filter_panel).to_have_attribute('open', '')
            page.get_by_role('button', name='Áp dụng bộ lọc', exact=True).click()
            expect(filter_panel).not_to_have_attribute('open', '')
            if '--search-only' in sys.argv:
                back.click()
                page.get_by_role('textbox',name='Tìm bộ dữ liệu đã lưu',exact=True).fill('mock')
                before_search=finished[0]
                page.get_by_role('button',name='Tìm dữ liệu',exact=True).click()
                wait_done(before_search)
                expect(page.locator('.st-key-stored_collection_list').get_by_role('button', name='Xem dữ liệu', exact=True)).to_be_visible()
                before_clear=finished[0]
                page.get_by_role('button',name='Xóa tìm kiếm',exact=True).click()
                wait_done(before_clear)
                expect(page.locator('.st-key-stored_collection_list').get_by_role('button', name='Xem dữ liệu', exact=True)).to_be_visible()
                page.get_by_role('button', name='Xem dữ liệu', exact=True).click()
                export.wait_for()
            page.reload()
            export.wait_for()
            page.get_by_role('button',name='Quay lại danh sách',exact=True).click()
            expect(export).to_have_count(0)
            page.get_by_role('button',name='Xem d\u1eef li\u1ec7u',exact=True).click()
            export.wait_for()
            expect(page.locator('.st-key-stored_collection_list')).to_have_count(0)
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
            expect(search).to_have_count(0)
            expect(page.get_by_role('button', name='Làm mới', exact=True)).to_have_count(0)
            if '--search-only' in sys.argv:
                back.click()
                page.get_by_role('textbox',name='Tìm bộ dữ liệu đã lưu',exact=True).fill('mock')
                before_search=finished[0]
                page.get_by_role('button',name='Tìm dữ liệu',exact=True).click()
                wait_done(before_search)
                expect(page.locator('.st-key-chart_collection_list').get_by_role('button', name='Xem dữ liệu', exact=True)).to_be_visible()
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
            expect(filters).to_have_attribute('open', '')
            page.wait_for_timeout(300)
            assert finished[0] == before_open, 'Opening the filters should stay in the browser'
            original_plot = plot.element_handle()
            chart_type = page.get_by_role('combobox', name='Loại biểu đồ', exact=True)
            for kind in ['Hoa gió', 'Chuỗi thời gian']:
                before = finished[0]
                chart_type.click()
                page.get_by_role('option', name=kind, exact=True).click()
                wait_done(before)
                expect(filters).to_have_attribute('open', '')
                assert original_plot.evaluate('(el) => el.isConnected'), 'Changing chart type replaced the applied plot'
            draw = page.get_by_role('button',name='Vẽ dữ liệu đã chọn',exact=True)
            assert draw.bounding_box()['y'] < plot.bounding_box()['y']
            expect(page.get_by_text('Từ ngày (UTC+7)',exact=True)).to_be_visible()
            for label in ['Ng\u00e0y','Th\u00e1ng','N\u0103m']:
                filters = page.locator('.st-key-chart_filters_'+cid+' details')
                if filters.get_attribute('open') is None:
                    page.get_by_text('Bộ lọc biểu đồ',exact=True).click()
                    expect(filters).to_have_attribute('open', '')
                before_draft = finished[0]
                filters.get_by_text(label, exact=True).click(timeout=5000)
                page.wait_for_timeout(150)
                assert finished[0] == before_draft, 'Editing the frequency should wait for form submission'
                before = finished[0]
                page.get_by_role('button',name='V\u1ebd d\u1eef li\u1ec7u \u0111\u00e3 ch\u1ecdn',exact=True).click()
                wait_done(before)
                page.locator('.js-plotly-plot').wait_for()
                expect(filters).not_to_have_attribute('open', '')
                page.wait_for_timeout(300)
                (OUT/'chart-position.json').write_text(json.dumps(page.locator('.js-plotly-plot').bounding_box()),encoding='utf-8')
                page.screenshot(path=str(OUT/'chart-position.png'),full_page=True)
                expect(page.locator('.js-plotly-plot')).to_be_in_viewport()
                page.screenshot(path=str(OUT/'chart-collapsed.png'),full_page=True)
            page.get_by_text('B\u1ea3ng d\u1eef li\u1ec7u v\u00e0 t\u1ea3i CSV',exact=True).click()
            page.get_by_role('button',name='T\u1ea3i b\u1ea3ng d\u1eef li\u1ec7u (CSV)',exact=True).wait_for()
            page.screenshot(path=str(OUT/'chart.png'),full_page=True)
            nav = page.get_by_role('complementary', name='Điều hướng khí tượng')
            nav.get_by_role('button', name='Kho dữ liệu', exact=True).click()
            export.wait_for()
            nav.get_by_role('button', name='Biểu đồ', exact=True).click()
            page.locator('.js-plotly-plot').wait_for()
            page.reload()
            page.locator('.js-plotly-plot').wait_for()
            nav.get_by_role('button', name='Kho dữ liệu', exact=True).click()
            expect(export).to_have_count(0)
            page.get_by_role('button',name='Xem d\u1eef li\u1ec7u',exact=True).click()
            export.wait_for()
            page.set_viewport_size(dict(width=390,height=900))
            page.wait_for_timeout(1600)
            expect(back).to_be_visible()
            back.click()
            heading = page.get_by_role('heading',name='D\u1eef li\u1ec7u \u0111\u00e3 l\u01b0u',exact=True)
            heading.scroll_into_view_if_needed()
            refresh = page.get_by_role('button',name='L\u00e0m m\u1edbi',exact=True)
            left,right=heading.bounding_box(),refresh.bounding_box()
            assert abs(left['y']+left['height']/2-right['y']-right['height']/2)<20
            assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
            page.screenshot(path=str(OUT/'portrait.png'),full_page=True)
            delete = page.get_by_role('button', name='Xóa', exact=True)
            delete.scroll_into_view_if_needed()
            before_delete = finished[0]
            initial_position = delete.bounding_box()['y']
            delete.click()
            expect(page.get_by_role('button', name='Xác nhận xóa', exact=True)).to_be_visible()
            page.wait_for_timeout(300)
            assert finished[0] == before_delete, 'Opening delete confirmation reran the list'
            assert abs(delete.bounding_box()['y'] - initial_position) < 1, 'Opening delete confirmation shifted the list'
            page.get_by_role('button',name='X\u00e1c nh\u1eadn x\u00f3a',exact=True).click()
            expect(page.get_by_role('button',name='X\u00e1c nh\u1eadn x\u00f3a',exact=True)).to_have_count(0)
            expect(export).to_have_count(0)
            expect(page.get_by_role('button', name='Sang mục Thu thập', exact=True)).to_be_visible()
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
