"""Real Chromium UI checks with isolated mock backend; never uses user DB/CDS."""
from pathlib import Path
import os,sys,subprocess,socket,time,json,urllib.request
from datetime import datetime
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output'/'browser_review'/datetime.now().strftime('%Y%m%d-%H%M%S')
OUT.mkdir(parents=True)
def port():
    with socket.socket() as s: s.bind(('127.0.0.1',0)); return s.getsockname()[1]
a,b=port(),port()
processes=[]; logs=[]
try:
    for mode,p in [('backend',a),('frontend',b)]:
        log=(OUT/(mode+'.log')).open('w',encoding='utf-8'); logs.append(log)
        command=[sys.executable,'-X','utf8','scripts/baseline_offline.py','--child',mode,'--output-dir',str(OUT),'--port',str(p),'--api-port',str(a)]
        if mode=='frontend':
            # Deterministic geocoder fixture: no external network, actual search/form UI.
            code=("from pathlib import Path; from scripts.baseline_offline import isolated_settings,child; "
                  f"out=Path({str(OUT)!r}); isolated_settings(out/'scratch',True); "
                  "import frontend.workflow as workflow; "
                  "workflow.search_places=lambda query:[dict(label='Hà Nội, Việt Nam — kiểm thử',latitude=21.0285,longitude=105.8542)] if query.strip() else []; "
                  "from frontend.api_client import DashboardApiClient; original=DashboardApiClient.list_collections; delayed=[False]; "
                  "exec('def slow_first_read(self,*args,**kwargs):\\n if not delayed[0]:\\n  delayed[0]=True\\n  __import__(\"time\").sleep(3)\\n return original(self,*args,**kwargs)'.replace('\\\\n','\\n')); "
                  "DashboardApiClient.list_collections=slow_first_read; "
                  f"child('frontend',out,{p},{a})")
            command=[sys.executable,'-X','utf8','-c',code]
        processes.append(subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT))
    for p,path in [(a,'/health'),(b,'/_stcore/health')]:
        for i in range(100):
            try:
                urllib.request.urlopen(f'http://127.0.0.1:{p}{path}',timeout=1);break
            except Exception:time.sleep(.2)
        else: raise RuntimeError('Server not ready')
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        chrome=Path(os.environ['LOCALAPPDATA'])/'ms-playwright/chromium-1248/chrome-win64/chrome.exe'
        cdp=os.getenv('BROWSER_REVIEW_CDP')
        browser=pw.chromium.connect_over_cdp(cdp) if cdp else pw.chromium.launch(executable_path=str(chrome),headless=True)
        page=browser.contexts[0].new_page() if cdp else browser.new_page(viewport={'width':1440,'height':1000},device_scale_factor=1)
        page.set_viewport_size({'width':1440,'height':1000})
        def card_action(name):
            button=page.get_by_role('button',name=name,exact=True).first
            if not button.is_visible():
                page.get_by_text('Chọn bộ dữ liệu khác · Mới nhất trước',exact=True).click()
            button.click()
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(f'http://127.0.0.1:{b}')
        page.get_by_text('Dữ liệu đã lưu',exact=True).click()
        page.get_by_text('Đang đọc thư viện dữ liệu…',exact=True).wait_for()
        assert not page.get_by_role('button',name='Bắt đầu thu thập',exact=True).is_visible(), 'Previous collection form remained visible while library loaded'
        page.screenshot(path=str(OUT/'loading-no-old-form.png'))
        page.get_by_text('Chưa có bộ dữ liệu phù hợp.',exact=False).wait_for()
        page.screenshot(path=str(OUT/'01-empty.png'),full_page=True)
        page.get_by_text('Thu thập',exact=True).click()
        page.get_by_role('combobox',name='Nguồn dữ liệu',exact=True).click()
        page.get_by_role('option',name='Demo · Dữ liệu giả lập',exact=True).click()
        page.get_by_role('textbox',name='Tìm địa điểm',exact=True).fill('Hà Nội')
        page.get_by_role('button',name='Tìm',exact=True).click()
        page.get_by_role('combobox',name='Kết quả địa điểm',exact=True).wait_for()
        page.locator('[data-testid="stDateInput"]').nth(1).locator('input').fill('2025-01-02')
        page.get_by_text('Biến khí hậu',exact=True).click()
        assert page.locator('[data-testid="stDateInput"]').nth(1).locator('input').input_value()=='2025-01-02'
        page.screenshot(path=str(OUT/'00-hanoi-request.png'),full_page=True)
        posts=[]
        # The browser submits Streamlit forms; backend state verifies the saved request.
        page.get_by_role('button',name='Bắt đầu thu thập',exact=True).click()
        page.get_by_text('Đã tiếp nhận tác vụ.',exact=True).wait_for()
        listing=json.load(urllib.request.urlopen(f'http://127.0.0.1:{a}/api/v1/jobs'))
        assert len(listing['jobs'])==1,listing
        cid=listing['jobs'][0]['collection_id']
        for i in range(100):
            job=json.load(urllib.request.urlopen(f'http://127.0.0.1:{a}/api/v1/jobs/{cid}'))
            if job['status'] not in ['queued','running']:break
            time.sleep(.1)
        assert job['status']=='completed',job
        page.get_by_role('button',name='Xem dữ liệu vừa thu thập',exact=True).click()
        page.get_by_role('textbox',name='Tìm bộ dữ liệu đã lưu',exact=True).fill('Hà Nội')
        page.get_by_role('button',name='Tìm dữ liệu',exact=True).click()
        page.get_by_role('button',name='Xem chi tiết',exact=True).wait_for()
        page.get_by_text('Dữ liệu từ phiên bản trước',exact=True).click()
        page.get_by_text('Mở kho dữ liệu cũ',exact=True).click()
        page.get_by_role('combobox',name='Chọn dữ liệu',exact=True).wait_for()
        assert not page.locator('[data-testid="stException"]').count()
        page.screenshot(path=str(OUT/'08-legacy.png'),full_page=True)
        page.get_by_text('Mở kho dữ liệu cũ',exact=True).click()
        page.screenshot(path=str(OUT/'02-library.png'),full_page=True)
        card_action('Xem')
        page.get_by_role('button',name='Xem chi tiết',exact=True).click()
        page.get_by_role('button',name='Tải CSV',exact=True).wait_for()
        with page.expect_download() as download:
            page.get_by_role('button',name='Tải CSV',exact=True).click()
        download.value.save_as(str(OUT/'download.csv'))
        import csv
        with (OUT/'download.csv').open(encoding='utf-8-sig') as exported:
            rows=list(csv.DictReader(exported))
        assert len(rows)==48 and {r['collection_id'] for r in rows}=={cid}
        page.locator('[data-testid="stDataFrame"]').last.scroll_into_view_if_needed()
        page.screenshot(path=str(OUT/'03-detail.png'),full_page=True)
        page.get_by_role('button',name='Vẽ biểu đồ từ dữ liệu này',exact=True).click()
        page.get_by_role('button',name='Vẽ dữ liệu đã chọn',exact=True).click()
        page.locator('[data-testid="stPlotlyChart"]').wait_for()
        page.locator('[data-testid="stPlotlyChart"]').scroll_into_view_if_needed()
        page.screenshot(path=str(OUT/'04-chart.png'),full_page=True)
        for frequency in ['Ngày','Tháng','Năm']:
            page.get_by_text('Stop',exact=True).wait_for(state='hidden')
            page.get_by_role('combobox',name='Chu kỳ',exact=True).click()
            page.get_by_role('option',name=frequency,exact=True).click()
            page.get_by_role('button',name='Vẽ dữ liệu đã chọn',exact=True).click()
            page.locator('[data-testid="stPlotlyChart"]').wait_for()
            page.get_by_text('Stop',exact=True).wait_for(state='hidden')
            assert not page.locator('[data-testid="stException"]').count()
        page.get_by_role('combobox',name='Loại biểu đồ',exact=True).click()
        page.get_by_role('option',name='Thống kê và xu hướng',exact=True).click()
        page.get_by_role('button',name='Vẽ dữ liệu đã chọn',exact=True).click()
        page.get_by_text('Trung bình',exact=True).wait_for()
        page.screenshot(path=str(OUT/'09-statistics.png'))
        # Regression: View from chart must navigate without modifying a rendered widget.
        card_action('Xem')
        page.get_by_role('button',name='Xem chi tiết',exact=True).wait_for()
        assert not page.locator('[data-testid="stException"]').count()
        page.get_by_role('button',name='Xem chi tiết',exact=True).scroll_into_view_if_needed()
        page.screenshot(path=str(OUT/'05-return.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path=str(OUT/'06-mobile.png'),full_page=True)
        page.set_viewport_size({'width':1440,'height':1000})
        page.get_by_role('textbox',name='Tìm bộ dữ liệu đã lưu',exact=True).fill('khong-co-du-lieu-nay')
        page.get_by_role('button',name='Tìm dữ liệu',exact=True).click()
        page.get_by_text('Chưa có bộ dữ liệu phù hợp.',exact=False).wait_for()
        page.screenshot(path=str(OUT/'07-no-results.png'),full_page=True)
        page.get_by_role('textbox',name='Tìm bộ dữ liệu đã lưu',exact=True).fill('Hà Nội')
        page.get_by_role('button',name='Tìm dữ liệu',exact=True).click()
        page.get_by_text('Chọn bộ dữ liệu khác · Mới nhất trước',exact=True).wait_for()
        body=page.locator('body').inner_text()
        (OUT/'visible-text.txt').write_text(body,encoding='utf-8')
        assert 'D? li?u' not in body and 'V?' not in body
        assert not errors,errors
        (OUT/'result.json').write_text(json.dumps(dict(passed=True,errors=errors,collection_id=cid,url=f'http://127.0.0.1:{b}',checks=['empty','library','detail','CSV visible','chart rendered','view from chart','mobile screenshot']),ensure_ascii=False,indent=2),encoding='utf-8')
        if cdp:
            page.goto('about:blank')
        else:
            browser.close()
finally:
    for process in processes:process.terminate()
    for process in processes:process.wait(timeout=15)
    for log in logs:log.close()
    print(OUT)
