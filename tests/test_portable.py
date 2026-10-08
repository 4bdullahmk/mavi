import http.client, importlib.util, json, pathlib, sys, tempfile, threading, time, unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'portable'))
import server, discord_branding, windows_automation

class WorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();server.DATA=pathlib.Path(cls.tmp.name)
        server.models=lambda:[{'name':'qwen3:8b','size':5_000_000_000}]
        server.capabilities=lambda:{'chat':{'available':True,'reason':''}}
        server.call_model=lambda messages,model=None,job=None:'Local response'
        cls.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        cls.port=cls.http.server_port;threading.Thread(target=cls.http.serve_forever,daemon=True).start()
    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();cls.tmp.cleanup()
    def request(self,path,body=None,origin=True,cookie=True,host=None):
        c=http.client.HTTPConnection('127.0.0.1',self.port)
        headers={'Host':host or f'127.0.0.1:{self.port}'}
        if cookie:headers['Cookie']='mavi_session='+server.TOKEN
        if origin:headers['Origin']=f'http://127.0.0.1:{self.port}' if origin is True else origin
        if body is not None:headers['Content-Type']='application/json'
        c.request('POST' if body is not None else 'GET',path,json.dumps(body) if body is not None else None,headers)
        r=c.getresponse();raw=r.read();status=r.status;c.close()
        return status,json.loads(raw)
    def test_unauthenticated_state_rejected(self):self.assertEqual(self.request('/api/state',cookie=False)[0],403)
    def test_branding_png_is_allowlisted_and_nosniff(self):
        connection=http.client.HTTPConnection('127.0.0.1',self.port)
        connection.request('GET','/mavi-mark.png',headers={'Host':f'127.0.0.1:{self.port}'})
        response=connection.getresponse();body=response.read()
        self.assertEqual(response.status,200)
        self.assertEqual(response.getheader('Content-Type'),'image/png')
        self.assertEqual(response.getheader('X-Content-Type-Options'),'nosniff')
        self.assertTrue(body.startswith(b'\x89PNG\r\n\x1a\n'))
        connection.request('GET','/mavi-mark.png/../app.js',headers={'Host':f'127.0.0.1:{self.port}'})
        blocked=connection.getresponse();blocked.read()
        self.assertEqual(blocked.status,404)
        connection.close()
    def test_dns_rebinding_host_rejected(self):self.assertEqual(self.request('/api/state',host='evil.example')[0],403)
    def test_cross_origin_write_rejected(self):self.assertEqual(self.request('/api/preferences',{'profile':'bad'},origin='https://evil.example')[0],403)
    def test_missing_origin_rejected(self):self.assertEqual(self.request('/api/preferences',{'profile':'bad'},origin=False)[0],403)
    def test_local_preferences_persist_only_local(self):
        status,_=self.request('/api/preferences',{'profile':'Use concise replies','theme':'dark','onboarded':True});self.assertEqual(status,200)
        self.assertEqual(json.loads((server.DATA/'workspace.json').read_text())['profile'],'Use concise replies')
    def test_profile_limit(self):self.assertEqual(self.request('/api/preferences',{'profile':'x'*4501})[0],400)
    def test_unknown_model_rejected(self):self.assertEqual(self.request('/api/preferences',{'model':'missing'})[0],400)
    def test_unknown_mode_rejected(self):self.assertEqual(self.request('/api/chat',{'text':'hello','mode':'shell'})[0],400)
    def test_attachment_limit(self):self.assertEqual(self.request('/api/chat',{'text':'hello','attachments':[{}]*7})[0],400)
    def test_chat_local_model_and_delete(self):
        status,result=self.request('/api/chat',{'text':'hello'});self.assertEqual(status,200)
        key=result['job_id']
        for _ in range(30):
            if server.JOBS[key]['status']=='completed':break
            time.sleep(.01)
        self.assertEqual(server.JOBS[key]['content'],'Local response')
        self.assertEqual(self.request('/api/chat/delete',{'chat_id':result['chat_id']})[0],200)
        self.assertFalse(any(c['id']==result['chat_id'] for c in server.STATE['chats']))
    def test_failed_task_is_persisted_but_excluded_from_model_history(self):
        transcript=[
            {'role':'user','content':'Create a workbook.'},
            {'role':'task_status','content':'No spreadsheet engine installed.','status':'failed'},
        ]
        self.assertEqual(server.model_history(transcript),[{'role':'user','content':'Create a workbook.'}])

        chat={'id':'failure-test','title':'Create a workbook','messages':[dict(transcript[0])]}
        server.STATE['chats'].append(chat)
        job={'id':'failure-test','chat_id':chat['id'],'mode':'files','status':'running','progress':'Working',
             'content':'','error':'','started':time.time(),'_cancel':threading.Event(),
             '_steer':[],'_model':'qwen3:8b','_answer_event':threading.Event()}
        fake_tools=type('FakeTools',(),{'run':staticmethod(lambda *args: (_ for _ in ()).throw(RuntimeError('Spreadsheet engine unavailable.')))})
        with patch.dict(sys.modules,{'tools_runtime':fake_tools}):
            server.run_job(job,chat,'Create a workbook.',[])
        self.assertEqual(job['status'],'failed')
        self.assertEqual(chat['messages'][-1]['role'],'task_status')
        self.assertEqual(chat['messages'][-1]['content'],'Spreadsheet engine unavailable.')
        saved=json.loads((server.DATA/'workspace.json').read_text())
        persisted=next(item for item in saved['chats'] if item['id']==chat['id'])
        self.assertEqual(persisted['messages'][-1]['status'],'failed')
        server.STATE['chats'].remove(chat)
        server.persist()
    def test_stopped_task_is_persisted_as_status_not_assistant_answer(self):
        chat={'id':'stopped-test','title':'Long file task','messages':[{'role':'user','content':'Make a file'}]}
        server.STATE['chats'].append(chat)
        cancel=threading.Event();cancel.set()
        job={'id':'stopped-test','chat_id':chat['id'],'mode':'files','status':'running','progress':'Working',
             'content':'','error':'','started':time.time(),'_cancel':cancel,
             '_steer':[],'_model':'qwen3:8b','_answer_event':threading.Event()}
        fake_tools=type('FakeTools',(),{'run':staticmethod(lambda *args: 'incomplete output')})
        with patch.dict(sys.modules,{'tools_runtime':fake_tools}):
            server.run_job(job,chat,'Make a file',[])
        self.assertEqual(job['status'],'stopped')
        self.assertEqual(chat['messages'][-1]['role'],'task_status')
        self.assertEqual(chat['messages'][-1]['status'],'stopped')
        self.assertEqual(server.model_history(chat['messages']),[{'role':'user','content':'Make a file'}])
        server.STATE['chats'].remove(chat)
        server.persist()
    def test_discord_api_exposes_safe_ids_and_reuses_memory_token(self):
        old_state,old_discord=server.STATE,dict(server.DISCORD)
        old_token,old_stop,old_thread=server.DISCORD_TOKEN,server.DISCORD_STOP,server.DISCORD_THREAD
        server.STATE={'chats':[],'profile':'','settings':{'model':'','theme':'system','onboarded':False,'project_path':'','release_url':'','discord':{}}}
        server.DISCORD_TOKEN='';server.DISCORD_STOP=threading.Event();server.DISCORD_THREAD=None
        server.DISCORD.clear();server.restore_discord_settings()
        token='memory-only-discord-token-fixture'
        try:
            with patch.object(server,'verify_discord_application') as verify, patch.object(server,'launch_discord_worker') as launch:
                payload={'token':token,'application_id':'12345678901234567','channel_id':'23456789012345678',
                         'user_ids':['34567890123456789'],'enabled':True,'allow_tasks':True,'unexpected_secret':token}
                status,_=self.request('/api/discord',payload);self.assertEqual(status,200)
                state_status,state=self.request('/api/state');self.assertEqual(state_status,200)
                self.assertEqual(state['discord']['application_id'],payload['application_id'])
                self.assertEqual(state['discord']['channel_id'],payload['channel_id'])
                self.assertEqual(state['discord']['user_ids'],payload['user_ids'])
                self.assertNotIn('token',state['discord'])
                self.assertNotIn(token,json.dumps(state))
                saved=(server.DATA/'workspace.json').read_text()
                self.assertNotIn(token,saved)
                self.assertEqual(set(json.loads(saved)['settings']['discord']),{'application_id','channel_id','user_ids'})

                update={'application_id':payload['application_id'],'channel_id':'45678901234567890',
                        'user_ids':payload['user_ids'],'enabled':True,'allow_tasks':False}
                status,_=self.request('/api/discord',update);self.assertEqual(status,200)
                self.assertEqual(server.DISCORD_TOKEN,token)
                verify.assert_any_call(token,payload['application_id'])
                self.assertTrue(all(call.args[0]==token for call in launch.call_args_list))
        finally:
            server.STATE=old_state;server.DISCORD_TOKEN=old_token;server.DISCORD_STOP=old_stop;server.DISCORD_THREAD=old_thread
            server.DISCORD.clear();server.DISCORD.update(old_discord)
    def test_discord_save_off_keeps_token_memory_only_until_disconnect(self):
        old_state, old_discord = server.STATE, dict(server.DISCORD)
        old_token, old_stop, old_thread = server.DISCORD_TOKEN, server.DISCORD_STOP, server.DISCORD_THREAD
        server.STATE = {'chats': [], 'profile': '', 'settings': {'model': '', 'theme': 'system', 'onboarded': False,
                       'project_path': '', 'release_url': '', 'discord': {}}}
        server.DISCORD_TOKEN = ''
        server.DISCORD_STOP = threading.Event()
        server.DISCORD_THREAD = None
        server.DISCORD.clear(); server.restore_discord_settings()
        token = 'save-off-session-only-token-fixture'
        ids = {'application_id': '12345678901234567', 'channel_id': '23456789012345678',
               'user_ids': ['34567890123456789']}
        try:
            with patch.object(server, 'verify_discord_application') as verify, patch.object(server, 'launch_discord_worker') as launch:
                server.discord_configure({**ids, 'token': token, 'enabled': False, 'allow_tasks': False})
                self.assertEqual(server.DISCORD_TOKEN, token)
                self.assertNotIn(token, json.dumps(server.DISCORD))
                persisted = (server.DATA / 'workspace.json').read_text(encoding='utf-8')
                self.assertNotIn(token, persisted)
                self.assertEqual(set(json.loads(persisted)['settings']['discord']), set(ids))

                # A later explicit enable can use that token without placing it
                # in the web-visible state or on-disk workspace.
                server.discord_configure({**ids, 'enabled': True, 'allow_tasks': False})
                verify.assert_called_once_with(token, ids['application_id'])
                launch.assert_called_once()
                self.assertEqual(launch.call_args.args[0], token)
                self.assertNotIn(token, json.dumps(server.DISCORD))

                # Saving a disconnect without entering another token clears the
                # process-only credential as well.
                server.discord_configure({**ids, 'enabled': False, 'allow_tasks': False})
                self.assertEqual(server.DISCORD_TOKEN, '')
                self.assertNotIn(token, (server.DATA / 'workspace.json').read_text(encoding='utf-8'))
        finally:
            server.STATE = old_state; server.DISCORD_TOKEN = old_token
            server.DISCORD_STOP = old_stop; server.DISCORD_THREAD = old_thread
            server.DISCORD.clear(); server.DISCORD.update(old_discord)

    def test_explicit_file_routes_without_model(self):
        job={'_model':'qwen3:8b','_cancel':threading.Event()}
        self.assertEqual(server.route_task('Create a text file named mavi-check.txt',[],job),'files')
    def test_explicit_app_and_link_routes_without_model(self):
        job={'_model':'qwen3:8b','_cancel':threading.Event()}
        with patch.object(server,'automation_backend',return_value=windows_automation), \
             patch.object(server,'models',side_effect=AssertionError('explicit target should not call the router')):
            self.assertEqual(server.route_task('Open Brave and visit https://example.com',[],job),'browser')
            self.assertEqual(server.route_task('Work in Webex and review the meeting window',[],job),'computer')
            self.assertEqual(server.route_task('Open File Explorer and find the report',[],job),'computer')
            self.assertEqual(server.route_task('Open Word and format the document',[],job),'computer')
            self.assertEqual(server.route_task('https://example.com/path',[],job),'browser')
            self.assertEqual(server.route_task('How do I open Chrome?',[],job),'chat')
            self.assertEqual(server.route_task('I do not want to use Chrome',[],job),'chat')
            self.assertEqual(server.route_task('Explain the phrase "open Brave"',[],job),'chat')
            self.assertEqual(server.route_task('I use Chrome for work',[],job),'chat')
            self.assertEqual(server.route_task('Open Chrome or Brave',[],job),'chat')
            self.assertEqual(server.route_task('Write a document about working in Brave',[],job),'files')
    def test_artifact_inventory_delete_and_traversal(self):
        server.persist()
        folder=server.DATA/'outputs';folder.mkdir(exist_ok=True)
        (folder/'disposable.txt').write_text('fixture')
        self.assertEqual(self.request('/api/artifacts')[0],200)
        self.assertEqual(self.request('/api/artifacts/delete',{'names':['../workspace.json']})[0],400)
        self.assertTrue((server.DATA/'workspace.json').exists())
        self.assertEqual(self.request('/api/artifacts/delete',{'names':['disposable.txt']})[0],200)
        self.assertFalse((folder/'disposable.txt').exists())
    def test_discord_no_mentions(self):self.assertEqual(discord_branding.message_payload('@everyone')['allowed_mentions'],{'parse':[]})
    def test_discord_unicode_bound(self):
        description=discord_branding.message_payload('🦌'*2000)['embeds'][0]['description'];self.assertLessEqual(len(description.encode('utf-16-le'))//2,1800)
    def test_discord_surrogate(self):discord_branding.message_payload('a\ud800b')['embeds'][0]['description'].encode('utf-8')

class DiscordSettingsIsolationTests(unittest.TestCase):
    def setUp(self):
        self.old_data=server.DATA;self.old_state=server.STATE;self.old_discord=dict(server.DISCORD)
        self.old_token=server.DISCORD_TOKEN;self.old_stop=server.DISCORD_STOP;self.old_thread=server.DISCORD_THREAD
        self.temp=tempfile.TemporaryDirectory()
    def tearDown(self):
        server.DATA=self.old_data;server.STATE=self.old_state;server.DISCORD_TOKEN=self.old_token
        server.DISCORD_STOP=self.old_stop;server.DISCORD_THREAD=self.old_thread
        server.DISCORD.clear();server.DISCORD.update(self.old_discord)
        self.temp.cleanup()
    @staticmethod
    def fresh_state():
        return {'chats':[],'profile':'','settings':{'model':'','theme':'system','onboarded':False,'project_path':'','release_url':'','discord':{}}}
    def test_two_data_folders_are_isolated_and_restart_requires_token(self):
        ids={'application_id':'12345678901234567','channel_id':'23456789012345678','user_ids':['34567890123456789']}
        token='do-not-save-discord-token-fixture'
        with tempfile.TemporaryDirectory() as first,tempfile.TemporaryDirectory() as second:
            server.DATA=pathlib.Path(first);server.STATE=self.fresh_state();server.DISCORD_TOKEN=''
            server.DISCORD_STOP=threading.Event();server.DISCORD_THREAD=None;server.restore_discord_settings()
            server.discord_configure({**ids,'enabled':False,'allow_tasks':True,'token':token,'unexpected_secret':token})
            persisted=(server.DATA/'workspace.json').read_text()
            self.assertNotIn(token,persisted)
            self.assertEqual(set(json.loads(persisted)['settings']['discord']),set(ids))

            server.load()
            self.assertEqual(server.DISCORD['application_id'],ids['application_id'])
            self.assertEqual(server.DISCORD['channel_id'],ids['channel_id'])
            self.assertEqual(server.DISCORD['user_ids'],ids['user_ids'])
            self.assertFalse(server.DISCORD['enabled']);self.assertFalse(server.DISCORD['connected'])
            self.assertFalse(server.DISCORD['allow_tasks']);self.assertEqual(server.DISCORD_TOKEN,'')
            with self.assertRaisesRegex(ValueError,'bot token'):
                server.discord_configure({**ids,'enabled':True})

            server.DATA=pathlib.Path(second);server.STATE=self.fresh_state();server.load()
            self.assertFalse(server.DISCORD['configured']);self.assertFalse(server.DISCORD['enabled'])
            self.assertEqual(server.DISCORD['channel_id'],'');self.assertEqual(server.DISCORD['user_ids'],[])
    def test_invalid_ids_and_bot_mismatch_do_not_replace_live_connection(self):
        server.DATA=pathlib.Path(self.temp.name)
        server.STATE=self.fresh_state()
        server.DISCORD_TOKEN='current-memory-token'
        server.DISCORD_STOP=threading.Event()
        class LiveThread:
            def is_alive(self): return True
            def join(self,timeout=None): raise AssertionError('current connection was disrupted')
        live=LiveThread();server.DISCORD_THREAD=live
        server.DISCORD.clear();server.DISCORD.update(configured=True,connected=True,enabled=True,allow_tasks=True,
            status='Connected · allowed users only',application_id='11111111111111111',channel_id='22222222222222222',user_ids=['33333333333333333'])
        before=dict(server.DISCORD);stop=server.DISCORD_STOP
        bad={'token':'new-memory-token','channel_id':'bad-id','user_ids':['44444444444444444'],'enabled':True}
        with self.assertRaises(ValueError): server.discord_configure(bad)
        self.assertIs(server.DISCORD_THREAD,live);self.assertIs(server.DISCORD_STOP,stop);self.assertFalse(stop.is_set())
        self.assertEqual(server.DISCORD,before);self.assertEqual(server.DISCORD_TOKEN,'current-memory-token')

        from discord_transport import DiscordClient
        supplied='candidate-secret-token'
        with patch.object(DiscordClient,'request',return_value={'id':'99999999999999999','bot':True}):
            with self.assertRaisesRegex(ValueError,'application ID') as error:
                server.discord_configure({'token':supplied,'application_id':'88888888888888888',
                    'channel_id':'77777777777777777','user_ids':['66666666666666666'],'enabled':True})
        self.assertNotIn(supplied,str(error.exception))
        self.assertIs(server.DISCORD_THREAD,live);self.assertIs(server.DISCORD_STOP,stop);self.assertFalse(stop.is_set())
        self.assertEqual(server.DISCORD,before);self.assertEqual(server.DISCORD_TOKEN,'current-memory-token')

    def test_worker_checks_application_before_reading_and_redacts_secret_from_status(self):
        from discord_transport import DiscordClient
        token='worker-secret-token'
        stop=threading.Event();server.DISCORD_STOP=stop
        server.DISCORD.clear();server.DISCORD.update(configured=True,connected=False,enabled=True,allow_tasks=True,status='Connecting…')
        calls=[]
        def wrong_identity(_client,method,path,**kwargs):
            calls.append(path)
            return {'id':'99999999999999999','bot':True}
        with patch.object(DiscordClient,'request',wrong_identity):
            server.discord_loop(token,'22222222222222222',{'33333333333333333'},stop,'88888888888888888',True)
        self.assertEqual(calls,['/users/@me'])
        self.assertNotIn(token,server.DISCORD['status'])
        self.assertIn('does not match',server.DISCORD['status'])

    def test_replaced_worker_cannot_start_a_job_or_inherit_task_consent(self):
        old_stop=threading.Event();new_stop=threading.Event()
        server.DISCORD_STOP=new_stop
        with patch.object(server,'new_job') as new_job:
            with self.assertRaises(InterruptedError):
                server.start_discord_job({'mode':'files'},'discord:old:user',old_stop,True)
            new_job.assert_not_called()


if __name__=='__main__':unittest.main()
