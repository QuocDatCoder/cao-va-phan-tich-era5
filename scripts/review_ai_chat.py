"""Offline running-app and Chromium review of floating chat, with isolated fixtures."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.parse

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


def child(args):
    from scripts.baseline_offline import isolated_settings
    isolated_settings(args.output_dir/'scratch',True)
    if args.child=='frontend':
        os.environ['API_BASE_URL']=f'http://127.0.0.1:{args.api_port}'
        from streamlit.web import cli
        sys.argv=['streamlit','run',str(ROOT/'app.py'),'--server.headless=true','--server.address=127.0.0.1',
                  f'--server.port={args.port}','--server.enableAgentApi=true','--browser.gatherUsageStats=false']
        cli.main()
        return
    from types import SimpleNamespace
    from scripts.agent_fixtures import create_fixture
    from backend.app.ai.agent import ClimateAgent
    from backend.app.ai.db_tools import build_database_tools
    from backend.app.api.agent import get_agent
    from backend.app.core.config import get_settings
    from backend.app.main import create_app
    import uvicorn
    fixture=create_fixture(args.output_dir/'fixtures')
    get_settings().climate_data_db_path=fixture.climate_data_db_path
    tools=build_database_tools(fixture)
    class Client:
        settings=SimpleNamespace(model='offline-ui-fixture',reasoning_effort='low')
        def create(self,**kwargs):
            time.sleep(3)
            question=json.loads(next(x['content'] for x in reversed(kwargs['input_items']) if x.get('role')=='user'))['question']
            if 'Chào ARE' in question:
                name='answer_question'
                arguments=dict(category='general',answer='Chào bạn! ARE có thể trò chuyện cùng bạn.',source_ids=[])
            elif 'Vẽ theo ngày' in question:
                name='prepare_view'
                arguments=dict(target='open_chart',collection_id=None,variable=None,latitude=None,longitude=None,
                               start=None,end=None,frequency='daily',view_kind='timeseries')
            elif 'Vẽ thiếu chu kỳ' in question:
                name='prepare_view'
                arguments=dict(target='open_chart',collection_id='fixture-hanoi',variable='temperature',latitude=None,longitude=None,
                    start='2025-01-01T00:00:00+07:00',end='2025-01-01T23:00:00+07:00',frequency=None,view_kind='timeseries')
            elif 'Tìm dữ liệu Hà Nội' in question:
                if any(x.get('type')=='function_call_output' for x in kwargs['input_items']):
                    name,arguments='finish',{}
                else:
                    name='search_collections'
                    arguments=dict(search='Hà Nội fixture',limit=5,offset=0)
            elif 'kiến trúc dự án' in question:
                if any(x.get('type')=='function_call_output' for x in kwargs['input_items']):
                    name='answer_question'
                    arguments=dict(category='project',answer='Dự án dùng **Streamlit**, **FastAPI** và **SQLite**.',source_ids=['architecture'])
                else:
                    name='project_knowledge'
                    arguments=dict(query='kiến trúc stack',limit=1)
            else:
                name='analyze_collection'
                arguments=dict(collection_id='fixture-hanoi',operation='statistics',variable='temperature',
                               start='2025-01-01T00:00:00+07:00',end='2025-01-01T23:00:00+07:00')
            return dict(output=[dict(type='function_call',name=name,call_id='offline-call',arguments=json.dumps(arguments))],usage={})
    app=create_app()
    app.dependency_overrides[get_agent]=lambda:ClimateAgent(Client(),tools)
    uvicorn.run(app,host='127.0.0.1',port=args.port)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--child',choices=['backend','frontend'])
    parser.add_argument('--port',type=int)
    parser.add_argument('--api-port',type=int)
    args=parser.parse_args()
    if args.child:
        return child(args)
    out=args.output_dir.resolve()
    if (ROOT/'output').resolve() not in out.parents or out.exists():
        parser.error('Choose a new output/ subdirectory.')
    out.mkdir(parents=True)
    ports={key:free_port() for key in ['backend','frontend']}
    processes,logs=[],[]
    try:
        for mode in ports:
            log=(out/(mode+'.log')).open('w',encoding='utf-8')
            logs.append(log)
            command=[sys.executable,'-X','utf8',__file__,'--child',mode,'--output-dir',str(out),
                     '--port',str(ports[mode]),'--api-port',str(ports['backend'])]
            processes.append(subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0))
        for mode,path in [('backend','/health'),('frontend','/_stcore/health')]:
            deadline=time.monotonic()+35
            while True:
                try:
                    urllib.request.urlopen(f'http://127.0.0.1:{ports[mode]}{path}',timeout=1)
                    break
                except OSError:
                    if time.monotonic()>deadline:
                        raise RuntimeError('Isolated startup failed: '+mode)
                    time.sleep(.2)
        base=f'http://127.0.0.1:{ports["frontend"]}'
        schema=json.load(urllib.request.urlopen(base+'/_stcore/agent/openapi.json'))
        (out/'agent-openapi.json').write_text(json.dumps(schema),encoding='utf-8')
        def interact(payload):
            req=urllib.request.Request(base+'/_stcore/agent/interact',json.dumps(payload).encode(),{'Content-Type':'application/json'})
            result=json.load(urllib.request.urlopen(req,timeout=40))
            assert result['status']=='ready',result
            return result
        initial=interact({})
        opened=interact(dict(session_id=initial['session_id'],trigger=dict(key='ai_chat_toggle')))
        # Agent API doesn't run the browser storage bridge; input is present but
        # disabled until a real browser hydrates its history.
        assert 'ai_chat_input' in json.dumps(opened['tree'])
        (out/'agent-snapshot.json').write_text(json.dumps(opened,ensure_ascii=False,indent=2),encoding='utf-8')
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as pw:
            executable=Path(os.environ.get('LOCALAPPDATA',''))/'ms-playwright/chromium-1248/chrome-win64/chrome.exe'
            browser=pw.chromium.launch(headless=True,**({'executable_path':str(executable)} if executable.is_file() else {}))
            page=browser.new_page(viewport=dict(width=1280,height=900))
            errors=[]
            page.on('pageerror',lambda err:errors.append(str(err)))
            page.goto(base)
            page.wait_for_function("Object.keys(localStorage).some(key=>key.startsWith('era5.are.chat.v1.'))",timeout=15000)
            page.get_by_role('button',name='AI chat',exact=True).click()
            shell=page.locator('.st-key-ai_chat_shell')
            expect(shell).to_be_visible()
            expect(shell.get_by_text('Chào bạn, mình là ARE!',exact=True)).to_be_visible()
            assert page.locator('.st-key-ai_chat_launcher img').evaluate('(el)=>el.complete && el.naturalWidth>0')
            box=shell.bounding_box()
            assert box['x']>600 and box['y']>=0 and box['y']+box['height']<900
            page.mouse.move(400,100)
            page.screenshot(path=str(out/'welcome.png'))
            page.set_viewport_size(dict(width=1366,height=768))
            box=shell.bounding_box()
            assert box['width']>=540 and box['y']>=60 and box['y']+box['height']<=768
            expect(shell.get_by_placeholder('Hỏi ARE…')).to_be_in_viewport(ratio=1)
            pet_box=page.locator('.st-key-ai_chat_launcher').bounding_box()
            assert 1366-pet_box['x']-pet_box['width']>=40 and 768-pet_box['y']-pet_box['height']>=40
            page.screenshot(path=str(out/'laptop.png'))
            page.set_viewport_size(dict(width=1280,height=900))
            shell.get_by_role('button',name='Tùy chọn',exact=False).click()
            page.get_by_text('Mưa',exact=True).click()
            expect(page.locator('.st-key-ai_chat_launcher .weather-pet')).to_have_attribute('data-mode','rain')
            page.mouse.move(400,100)
            page.screenshot(path=str(out/'rain.png'))
            page.get_by_text('Nắng',exact=True).click()
            expect(page.locator('.st-key-ai_chat_launcher .weather-pet')).to_have_attribute('data-mode','sun')
            page.get_by_text('Tự động',exact=True).click()
            shell.get_by_role('button',name='Thu gọn',exact=True).click()
            expect(shell).not_to_be_visible()
            page.get_by_role('button',name='AI chat',exact=True).click()
            expect(shell).to_be_visible()
            shell.get_by_placeholder('Hỏi ARE…').fill('Thống kê nhiệt độ ngày 01/01/2025')
            shell.get_by_placeholder('Hỏi ARE…').press('Enter')
            expect(shell.get_by_text('Đang kiểm tra dữ liệu…',exact=False)).to_be_visible()
            expect(page.locator('.st-key-ai_chat_launcher .weather-pet')).to_have_attribute('data-mode','rain')
            shell.get_by_role('button',name='Thu gọn',exact=True).click()
            expect(shell).not_to_be_visible()
            page.locator('.st-key-workflow_section').get_by_text('Kho dữ liệu',exact=True).click()
            page.get_by_role('button',name='AI chat',exact=True).click()
            expect(shell.get_by_text('Thống kê từ service:',exact=False)).to_be_visible(timeout=20000)
            expect(page.locator('.st-key-ai_chat_launcher .weather-pet')).to_have_attribute('data-mode','sun')
            page.mouse.move(400,100)
            page.screenshot(path=str(out/'desktop.png'))
            shell.get_by_text('Chi tiết dữ liệu',exact=True).click()
            expect(shell.get_by_text('Độ phủ: 100.0%',exact=True)).to_be_visible()
            expect(shell.get_by_text('Giờ nguồn đã có: 24 / 24',exact=True)).to_be_visible()
            assert shell.locator('[data-testid="stJson"]').count()==0
            page.screenshot(path=str(out/'readable-details.png'))
            shell.get_by_text('Chi tiết dữ liệu',exact=True).click()
            # Action must reuse the chosen dataset and enter chart without resetting chat history.
            shell.get_by_role('button',name='Mở trong Biểu đồ',exact=True).click()
            expect(shell).not_to_be_visible()
            expect(page.get_by_role('heading',name='Biểu đồ',exact=True)).to_be_visible()
            page.get_by_role('button',name='AI chat',exact=True).click()
            expect(shell.get_by_text('Thống kê từ service:',exact=False)).to_be_visible()
            page.set_viewport_size(dict(width=390,height=844))
            expect(shell).to_be_visible()
            box=shell.bounding_box()
            assert box['x']>=0 and box['x']+box['width']<=390 and box['y']>=0
            page.screenshot(path=str(out/'mobile.png'))
            page.set_viewport_size(dict(width=700,height=500))
            box=shell.bounding_box()
            page.screenshot(path=str(out/'short-screen.png'))
            (out/'layout-debug.json').write_text(json.dumps(page.locator('.st-key-ai_chat_messages').evaluate('''el=>{
                const result=[];for(let i=0;el && i<5;i++,el=el.parentElement){
                const r=el.getBoundingClientRect(),c=getComputedStyle(el);result.push({tag:el.tagName,classes:el.className,style:el.getAttribute('style'),height:r.height,y:r.y,min:c.minHeight,max:c.maxHeight,cssHeight:c.height,flex:c.flex});}
                return result;}'''),indent=2),encoding='utf-8')
            assert box['y']>=64 and box['y']+box['height']<=500
            expect(shell.get_by_placeholder('Hỏi ARE…')).to_be_in_viewport(ratio=1)
            page.screenshot(path=str(out/'short-screen.png'))
            page.set_viewport_size(dict(width=1280,height=900))
            chat_input=shell.get_by_placeholder('Hỏi ARE…')
            chat_input.fill('Chào ARE')
            chat_input.press('Enter')
            expect(shell.get_by_text('Chào bạn! ARE sẵn sàng hỗ trợ dữ liệu ERA5 và cách sử dụng ứng dụng.',exact=True)).to_be_visible(timeout=15000)
            chat_input.fill('Tôi muốn ăn cơm tấm')
            chat_input.press('Enter')
            expect(shell.get_by_text('ARE không hỗ trợ nội dung ngoài phạm vi dự án. Bạn có thể hỏi về dữ liệu ERA5, phân tích khí tượng hoặc cách sử dụng ứng dụng.',exact=True)).to_be_visible(timeout=15000)
            page.screenshot(path=str(out/'scope-refusal.png'))
            chat_input.fill('Giải thích kiến trúc dự án')
            chat_input.press('Enter')
            expect(shell.get_by_text('Nguồn trong dự án',exact=True)).to_be_visible(timeout=15000)
            shell.get_by_text('Nguồn trong dự án',exact=True).click()
            expect(shell.get_by_text('frontend/workflow.py:',exact=False)).to_be_visible()
            assert shell.locator('[data-testid="stJson"]').count()==0
            page.screenshot(path=str(out/'project-response.png'))
            chat_input.fill('Vẽ theo ngày với bộ đang xem')
            chat_input.press('Enter')
            expect(shell.get_by_text('Đã chuẩn bị biểu đồ',exact=False)).to_be_visible(timeout=15000)
            shell.get_by_role('button',name='Mở trong Biểu đồ',exact=True).last.click()
            expect(shell).not_to_be_visible()
            saved=json.loads(urllib.parse.parse_qs(urllib.parse.urlsplit(page.url).query)['chart_applied'][0])
            assert saved[2]['frequency']=='daily' and saved[3]=='aggregate'
            expect(page.locator('.st-key-chart_result')).to_be_visible()
            page.screenshot(path=str(out/'applied-daily-action.png'))
            page.get_by_role('button',name='AI chat',exact=True).click()
            chat_input=shell.get_by_placeholder('Hỏi ARE…')
            chat_input.fill('Tìm dữ liệu Hà Nội fixture')
            chat_input.press('Enter')
            expect(shell.get_by_role('button',name='Mở kết quả trong Kho dữ liệu',exact=True)).to_be_visible(timeout=15000)
            shell.get_by_role('button',name='Mở kết quả trong Kho dữ liệu',exact=True).click()
            expect(shell).not_to_be_visible()
            expect(page.get_by_label('Tìm bộ dữ liệu đã lưu',exact=True)).to_have_value('Hà Nội fixture')
            page.get_by_role('button',name='AI chat',exact=True).click()
            reads_before=(out/'backend.log').read_text(encoding='utf-8').count('/fixture-hanoi/timeseries?')
            shell.locator('[class*="st-key-ai_search_open_data_"] button:visible').last.click()
            expect(shell).not_to_be_visible()
            expect(page.locator('.st-key-stored_collection_detail')).to_be_visible()
            assert (out/'backend.log').read_text(encoding='utf-8').count('/fixture-hanoi/timeseries?')==reads_before
            page.screenshot(path=str(out/'data-action.png'))
            page.locator('.st-key-stored_back_to_list button').click()
            expect(page.locator('.st-key-stored_collection_detail')).not_to_be_visible()
            page.get_by_role('button',name='AI chat',exact=True).click()
            chat_input=shell.get_by_placeholder('Hỏi ARE…')
            chat_input.fill('Vẽ thiếu chu kỳ')
            chat_input.press('Enter')
            expect(shell.get_by_role('button',name='Mở kết quả',exact=True)).to_be_visible(timeout=15000)
            shell.get_by_role('combobox',name='Chu kỳ',exact=True).click()
            page.wait_for_timeout(1800)  # Idle chat must not close an open selector.
            page.get_by_role('option',name='Ngày',exact=True).click()
            shell.get_by_role('button',name='Mở kết quả',exact=True).click()
            expect(shell).not_to_be_visible()
            saved=json.loads(urllib.parse.parse_qs(urllib.parse.urlsplit(page.url).query)['chart_applied'][0])
            assert saved[2]['frequency']=='daily' and saved[3]=='aggregate'
            page.get_by_role('button',name='AI chat',exact=True).click()
            page.wait_for_function("""()=>Object.keys(localStorage).some(key=>key.startsWith('era5.are.chat.v1.') &&
                JSON.parse(localStorage.getItem(key)).messages.length>=12 && !JSON.parse(localStorage.getItem(key)).in_flight)""")
            page.wait_for_function("""()=>Object.keys(localStorage).some(key=>key.startsWith('era5.are.chat.v1.') &&
                JSON.parse(localStorage.getItem(key)).messages.some(m=>m.role==='assistant' && m.content.includes('**SQLite**')))""")
            previous_posts=(out/'backend.log').read_text(encoding='utf-8').count('POST /api/v1/agent/chat')
            page.reload()
            expect(shell.get_by_text('Nguồn trong dự án',exact=True)).to_be_visible(timeout=15000)
            expect(shell.get_by_placeholder('Hỏi ARE…')).to_be_enabled()
            assert (out/'backend.log').read_text(encoding='utf-8').count('POST /api/v1/agent/chat')==previous_posts
            page.screenshot(path=str(out/'history-after-f5.png'))
            shell.get_by_role('button',name='Thu gọn',exact=True).click()
            expect(shell).not_to_be_visible()
            page.locator('.st-key-chart_refresh_library button').click()
            page.get_by_role('button',name='AI chat',exact=True).click()
            expect(shell.get_by_text('Nguồn trong dự án',exact=True)).to_be_visible()
            shell.get_by_role('button',name='Tùy chọn',exact=False).click()
            page.locator('.st-key-ai_chat_clear button:visible').click()
            page.wait_for_function("""()=>Object.keys(localStorage).some(key=>key.startsWith('era5.are.chat.v1.') &&
                JSON.parse(localStorage.getItem(key)).messages.length===0)""")
            page.reload()
            expect(shell.get_by_text('Chào bạn, mình là ARE!',exact=True)).to_be_visible(timeout=15000)
            expect(shell.get_by_text('Nguồn trong dự án',exact=True)).not_to_be_visible()
            page.screenshot(path=str(out/'history-cleared.png'))
            # A reload during work keeps the question but must not replay paid work.
            previous_posts=(out/'backend.log').read_text(encoding='utf-8').count('POST /api/v1/agent/chat')
            chat_input=shell.get_by_placeholder('Hỏi ARE…')
            expect(chat_input).to_be_enabled()
            chat_input.fill('Chào ARE, câu đang chờ')
            chat_input.press('Enter')
            page.wait_for_function("""()=>Object.keys(localStorage).some(key=>key.startsWith('era5.are.chat.v1.') &&
                JSON.parse(localStorage.getItem(key)).in_flight===true)""")
            page.reload()
            expect(shell.get_by_text('Câu hỏi đã được giữ; ARE chưa tự gửi lại.',exact=False)).to_be_visible(timeout=15000)
            deadline=time.monotonic()+10
            while (out/'backend.log').read_text(encoding='utf-8').count('POST /api/v1/agent/chat')<previous_posts+1:
                if time.monotonic()>deadline:
                    raise RuntimeError('Pending fixture request did not complete')
                page.wait_for_timeout(100)
            assert (out/'backend.log').read_text(encoding='utf-8').count('POST /api/v1/agent/chat')==previous_posts+1
            assert not errors,errors
            browser.close()
        report=dict(passed=True,agent_api_ready=True,desktop_mobile=True,background_chat=True,
                    chart_navigation=True,history_survives_f5=True,clear_persists=True,
                    page_refresh_keeps_chat=True,no_replay_on_reload=True,applied_context_chart=True,
                    search_action=True,data_action=True,clarification_form=True,browser_errors=errors,paid_ai_calls=0,cds_calls=0)
        (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report))
    finally:
        for proc in processes:
            proc.terminate()
        for proc in processes:
            proc.wait(timeout=10)
        for log in logs:
            log.close()


if __name__=='__main__':
    main()
