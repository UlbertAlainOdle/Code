"""Native memory console. Its window never owns the backend lifecycle."""
import json
import queue
import threading
from datetime import datetime
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, build_opener, ProxyHandler, HTTPCookieProcessor
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText


KINDS = {'daily': '每日稿', 'important': '重要内容', 'revision': '已有记忆更新'}
STATES = {'open': '今日整理中', 'pending': '等待批准', 'saving': '保存中', 'saved': '已保存',
          'rejected': '不保存', 'expired': '已过期', 'invalid': '来源已失效',
          'unknown': '结果待核对', 'superseded': '已被后续内容替代', 'active': '可用', 'archived': '已归档'}
OPERATIONS = {'delete': '删除这条记忆', 'block': '停止使用', 'unblock': '恢复使用'}


class ConsoleError(Exception):
    pass


class MemoryClient:
    """Own cookie jar, loopback only, no token files and no remote proxy."""
    def __init__(self, port=8000):
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('invalid_port')
        self.origin = f'http://127.0.0.1:{port}'
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
        self.lock = threading.Lock()

    def request(self, path, body=None):
        if not path.startswith(('memory-console', 'memory-management')) or '..' in path:
            raise ValueError('invalid_console_path')
        headers = {'Origin': self.origin, 'X-Saya-QQ-UI': '1', 'Content-Type': 'application/json'}
        req = Request(self.origin + '/api/qq/' + path, headers=headers,
                      data=json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None)
        try:
            with self.lock:
                try:
                    response = self.opener.open(req, timeout=150 if path in {'memory-console/daily-overlap','memory-console/library-overlap'} else 20)
                except HTTPError as error:
                    if error.code != 401:
                        raise
                    error.close()
                    with self.opener.open(Request(self.origin + '/api/qq/ui-session', data=b'{}', headers=headers), timeout=5):
                        pass
                    # urllib keeps the Cookie header it added to a reused
                    # Request. After backend restart, retry with the new jar.
                    req.remove_header('Cookie')
                    response = self.opener.open(req, timeout=150 if path in {'memory-console/daily-overlap','memory-console/library-overlap'} else 20)
                with response:
                    return json.load(response)
        except HTTPError as error:
            try:
                detail = json.load(error).get('detail')
            except (ValueError, OSError):
                detail = None
            finally:
                error.close()
            raise ConsoleError(detail if isinstance(detail, str) else '请求未完成，请重新读取核对。') from None
        except (OSError, URLError, ValueError):
            raise ConsoleError('无法确认后台响应。请检查后台状态，再刷新原条目；不要重复新建。') from None

    def catalog(self, owner='', page=1, query='', snapshot=''):
        return self.request('memory-management?' + urlencode(dict(owner_id=owner, page=page, query=query, snapshot=snapshot)))

    def accounts(self):
        return self.request('memory-console/accounts')['owners']

    def listing(self, owner):
        return self.request('memory-console?' + urlencode({'owner_id': owner}))

    def preview(self, owner, identifier):
        return self.request('memory-console/preview/' + quote(identifier, safe='') + '?' + urlencode({'owner_id': owner}))

    def decide(self, owner, preview, action):
        if action not in {'approve', 'reject'} or preview.get('state') != 'pending':
            raise ConsoleError('请先打开一条待批准内容的完整预览。')
        if action == 'approve' and preview.get('waiting_on'):
            raise ConsoleError('请先决定与这份每日稿重叠的独立保存内容，再重新预览。')
        return self.request('memory-console/decision', dict(owner_id=owner, id=preview['id'],
                            preview_version=preview['preview_version'], action=action))

    def detail(self, owner, identifier):
        return self.request('memory-management/item?' + urlencode(dict(owner_id=owner, memory_id=identifier)))

    def command(self, owner, text):
        return self.request('memory-console/command', dict(owner_id=owner, text=text))

    def operation_preview(self, owner, detail, action):
        return self.request('memory-console/operation-preview', dict(owner_id=owner,
                            memory_id=detail['memory']['id'], content_version=detail['content_version'], action=action))

    def operation_confirm(self, owner, preview):
        return self.request('memory-console/operation-confirm', dict(owner_id=owner, token=preview['token']))

    def saves(self, owner):
        return self.request('memory-console/saves?' + urlencode({'owner_id':owner}))

    def save_preview(self, owner, identifier):
        return self.request('memory-console/saves/'+quote(identifier,safe='')+'?'+urlencode({'owner_id':owner}))

    def save_select(self, owner, preview, indices):
        return self.request('memory-console/save-select',dict(owner_id=owner,id=preview['id'],preview_version=preview['preview_version'],indices=indices))

    def save_decide(self, owner, preview, action):
        return self.request('memory-console/save-decision',dict(owner_id=owner,id=preview['id'],preview_version=preview['preview_version'],action=action))

    def candidates(self, owner, page=1, query='', snapshot=''):
        return self.request('memory-console/candidates?'+urlencode(dict(owner_id=owner,page=page,query=query,snapshot=snapshot)))

    def candidate_preview(self, owner, identifier, version):
        return self.request('memory-console/candidate-preview',dict(owner_id=owner,candidate_id=identifier,content_version=version))

    def revision_preview(self, owner, detail, operation, content):
        return self.request('memory-console/revision-preview',dict(owner_id=owner,memory_id=detail['memory']['id'],
                            content_version=detail['content_version'],operation=operation,content=content))

    def revision_confirm(self, owner, preview):
        return self.request('memory-console/revision-confirm',dict(owner_id=owner,token=preview['confirmation_token']))

    def actions(self,owner):
        return self.request('memory-console/actions?'+urlencode({'owner_id':owner}))

    def action_preview(self,owner,identifier):
        return self.request('memory-console/actions/'+quote(identifier,safe='')+'?'+urlencode({'owner_id':owner}))

    def pair_preview(self,owner,kind,first,second):
        return self.request('memory-console/action-preview',dict(owner_id=owner,kind=kind,
            first=first['memory']['id'],second=second['memory']['id'],first_version=first['content_version'],second_version=second['content_version']))

    def action_decide(self,owner,preview,action):
        return self.request('memory-console/action-decision',dict(owner_id=owner,id=preview['id'],preview_version=preview['preview_version'],action=action))

    def events(self,owner):
        return self.request('memory-console/events?'+urlencode({'owner_id':owner}))

    def event_preview(self,owner,identifier):
        return self.request('memory-console/events/'+quote(identifier,safe='')+'?'+urlencode({'owner_id':owner}))

    def event_decide(self,owner,preview,action):
        return self.request('memory-console/event-decision',dict(owner_id=owner,id=preview['id'],preview_version=preview['preview_version'],action=action))

    def library(self,owner):
        return self.request('memory-console/library?'+urlencode({'owner_id':owner}))

    def reads(self,owner):
        return self.request('memory-console/reads?'+urlencode({'owner_id':owner}))

    def read_query(self,owner,identifier):
        return self.request('memory-console/reads/'+quote(identifier,safe='')+'?'+urlencode({'owner_id':owner}))

    def library_preview(self,owner,identifier):
        return self.request('memory-console/library/'+quote(identifier,safe='')+'?'+urlencode({'owner_id':owner}))

    def library_scan(self,owner,state):
        return self.request('memory-console/library-scan',dict(owner_id=owner,snapshot=state['snapshot'],cursor=state['cursor']))

    def library_prepare(self,owner,preview,keep):
        return self.request('memory-console/library-prepare',dict(owner_id=owner,id=preview['id'],preview_version=preview['preview_version'],keep=keep))

    def overlap(self,owner,preview,*,daily=False,cancel=False):
        return self.request('memory-console/'+('daily-overlap-cancel' if cancel else 'daily-overlap' if daily else 'library-overlap'),
            dict(owner_id=owner,id=preview['id'],preview_version=preview['preview_version']))


def preview_text(data):
    """Show full body and source text; never confirm a catalog excerpt."""
    parts = [f"{KINDS.get(data.get('kind'), '内容')} · {STATES.get(data.get('state'), data.get('state', ''))}",
             f"对象：{data.get('subject', '')}　日期：{data.get('day', '')}"]
    if data.get('expires'):
        parts.append('待批截止：' + datetime.fromtimestamp(data['expires']).astimezone().strftime('%Y-%m-%d %H:%M %Z'))
    if 'previous_body' in data:
        parts += ['\n原条目完整正文', data['previous_body'], '\n拟更新完整正文']
    else:
        parts += ['\n完整正文']
    parts.append(data.get('body') or '（正文已清理，或当前没有待批正文）')
    if data.get('waiting_on'):
        parts += ['\n请先决定重叠的独立保存内容：', '\n'.join(data['waiting_on'])]
    if data.get('overlap_stale'):parts.append('\n先前的整理依据已变化，当前显示原稿，不能使用旧整理结果批准。请重新整理或本次不保存。')
    if data.get('contact_maintenance'):parts.append('\n这是该联系人自己的既有每日记忆维护，仍由主人批准，原创建身份及范围保持。')
    for anchor in data.get('overlap_anchors',[]):
        parts += ['\n已独立保存的重要记忆 · '+anchor['id']+' · 原作者 '+str(anchor['author']),
                  '记录日期：'+str(anchor.get('recorded_day') or '未记录'),anchor['body']]
    for source in data.get('sources', []):
        parts += [f"\n来源 · 用户原话 · 消息 {source.get('message_id', '')}", source.get('text', '')]
    if data.get('result'):
        parts += ['\n原操作结果', json.dumps(data['result'], ensure_ascii=False, indent=2)]
    return '\n'.join(parts)


class MemoryPanel:
    def __init__(self, window, port=8000, client=None):
        self.window = window
        self.client = client or MemoryClient(port)
        self.events = queue.Queue()
        self.progress_events = queue.Queue()
        self.busy = False
        self.closed = False
        self.preview = None
        self.saved_detail = None
        self.operation = None
        self.saves = None
        self.actions = None
        self.event_pane = None
        self.library_pane = None
        self.read_pane = None
        self.owner = tk.StringVar()
        self.status = tk.StringVar(value='正在连接后台……')
        self.policy = tk.StringVar(value='每日稿和重要内容都在这里决定，聊天中无需逐条确认。')
        self.page = 1
        self.snapshot = ''
        self.has_next = False
        window.title('Saya · 记忆操作台')
        window.geometry('980x720')
        window.minsize(850, 650)
        window.protocol('WM_DELETE_WINDOW', self.close)
        frame = ttk.Frame(window, padding=16)
        frame.pack(fill='both', expand=True)
        row = ttk.Frame(frame)
        row.pack(fill='x')
        ttk.Label(row, text='记忆操作台', font=('Microsoft YaHei UI', 17, 'bold')).pack(side='left')
        ttk.Label(row, text='主人账号：').pack(side='left', padx=(24, 0))
        self.owners = ttk.Combobox(row, textvariable=self.owner, state='readonly', width=18)
        self.owners.pack(side='left')
        self.owners.bind('<<ComboboxSelected>>', lambda _: self.refresh())
        self.refresh_button = ttk.Button(row, text='刷新', command=self.refresh)
        self.refresh_button.pack(side='right')
        ttk.Label(frame, textvariable=self.policy, wraplength=1000).pack(anchor='w', pady=(8, 4))
        ttk.Label(frame, textvariable=self.status, wraplength=1000).pack(anchor='w', pady=(0, 8))
        self.tabs = ttk.Notebook(frame)
        pending = ttk.Frame(self.tabs, padding=8)
        saved = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(pending, text='每日稿与待确认')
        self.tabs.add(saved, text='已保存记忆')
        entry_list = ttk.Frame(pending)
        entry_list.pack(fill='x')
        self.entries = ttk.Treeview(entry_list, columns=('kind', 'subject', 'day', 'state'), show='headings', height=6, selectmode='browse')
        for name, title, width in [('kind', '类型', 150), ('subject', '对话对象', 190), ('day', '日期', 150), ('state', '状态', 180)]:
            self.entries.heading(name, text=title)
            self.entries.column(name, width=width)
        self.entries.pack(side='left', fill='x', expand=True)
        entry_scroll = ttk.Scrollbar(entry_list, command=self.entries.yview)
        entry_scroll.pack(side='right', fill='y')
        self.entries.configure(yscrollcommand=entry_scroll.set)
        self.entries.bind('<<TreeviewSelect>>', self.select_entry)
        self.entries.bind('<Button-1>', lambda _: 'break' if self.busy else None)
        self.entries.bind('<Key>', lambda _: 'break' if self.busy else None)
        self.body = ScrolledText(pending, wrap='word', state='disabled', font=('Microsoft YaHei UI', 11), height=14)
        self.body.tag_configure('all', spacing1=3, spacing3=3)
        actions = ttk.Frame(pending)
        actions.pack(side='bottom', fill='x')
        self.approve = ttk.Button(actions, text='保留到长期记忆', command=lambda: self.decide('approve'), state='disabled')
        self.reject = ttk.Button(actions, text='本次不保存', command=lambda: self.decide('reject'), state='disabled')
        self.approve.pack(side='left', padx=(0, 8))
        self.reject.pack(side='left')
        self.organize_daily=ttk.Button(actions,text='整理与重要项重合的内容',command=self.prepare_daily_overlap,state='disabled')
        self.organize_daily.pack(side='left',padx=8)
        self.cancel_daily_overlap=ttk.Button(actions,text='保留原稿',command=lambda:self.prepare_daily_overlap(cancel=True),state='disabled')
        self.cancel_daily_overlap.pack(side='left',padx=(0,8))
        ttk.Label(actions, text='今日稿在日终前只能查看。').pack(side='left', padx=6)
        self.body.pack(fill='both', expand=True, pady=8)
        search = ttk.Frame(saved)
        search.pack(fill='x')
        self.query = tk.StringVar()
        ttk.Entry(search, textvariable=self.query).pack(side='left', fill='x', expand=True)
        ttk.Button(search, text='查找', command=lambda: self.load_saved(reset=True)).pack(side='left', padx=6)
        self.prev = ttk.Button(search, text='上一页', command=lambda: self.load_saved(-1), state='disabled')
        self.next = ttk.Button(search, text='下一页', command=lambda: self.load_saved(1), state='disabled')
        self.prev.pack(side='left')
        self.next.pack(side='left', padx=(6, 0))
        saved_list = ttk.Frame(saved)
        saved_list.pack(fill='x', pady=8)
        self.saved = ttk.Treeview(saved_list, columns=('title', 'state'), show='headings', height=6, selectmode='browse')
        self.saved.heading('title', text='条目')
        self.saved.heading('state', text='状态')
        self.saved.column('title', width=680)
        self.saved.column('state', width=130)
        self.saved.pack(side='left', fill='x', expand=True)
        saved_scroll = ttk.Scrollbar(saved_list, command=self.saved.yview)
        saved_scroll.pack(side='right', fill='y')
        self.saved.configure(yscrollcommand=saved_scroll.set)
        self.saved.bind('<<TreeviewSelect>>', self.select_saved)
        self.saved.bind('<Button-1>', lambda _: 'break' if self.busy else None)
        self.saved.bind('<Key>', lambda _: 'break' if self.busy else None)
        self.saved_body = ScrolledText(saved, wrap='word', state='disabled', font=('Microsoft YaHei UI', 11))
        organize=ttk.Frame(saved);organize.pack(fill='x')
        for index,label in enumerate(('加入整理：第一条（保留）','加入整理：第二条')):
            ttk.Button(organize,text=label,command=lambda i=index:self.actions.choose(i)).pack(side='left',padx=(0,8))
        saved_actions = ttk.Frame(saved)
        saved_actions.pack(side='bottom', fill='x', pady=(8, 0))
        self.operation_buttons = []
        for action, label in OPERATIONS.items():
            button = ttk.Button(saved_actions, text=label, command=lambda a=action: self.prepare_operation(a), state='disabled')
            button.pack(side='left', padx=(0, 8))
            self.operation_buttons.append(button)
        edit = ttk.Button(saved_actions,text='编辑／补充',command=self.edit_saved,state='disabled')
        edit.pack(side='left',padx=(0,8))
        self.operation_buttons.append(edit)
        self.confirm_operation = ttk.Button(saved_actions, text='确认上述操作', command=self.commit_operation, state='disabled')
        self.confirm_operation.pack(side='right')
        self.saved_body.pack(fill='both', expand=True)
        from saya_qq_save_panel import SavePane
        self.saves = SavePane(self.tabs,self)
        from saya_qq_action_panel import ActionPane
        self.actions = ActionPane(self.tabs,self)
        from saya_qq_event_panel import EventPane
        self.event_pane = EventPane(self.tabs,self)
        from saya_qq_library_panel import LibraryPane
        self.library_pane = LibraryPane(self.tabs,self)
        from saya_qq_read_panel import ReadPane
        self.read_pane = ReadPane(self.tabs,self)
        footer = ttk.Frame(frame)
        footer.pack(side='bottom', fill='x')
        commands = ttk.Frame(footer)
        commands.pack(fill='x', pady=(10, 0))
        ttk.Label(commands, text='输入：').pack(side='left')
        self.command_text = tk.StringVar()
        command_entry = ttk.Entry(commands, textvariable=self.command_text)
        command_entry.pack(side='left', fill='x', expand=True)
        command_entry.bind('<Return>', lambda _: self.run_command())
        ttk.Button(commands, text='读取／生成预览', command=self.run_command).pack(side='left', padx=(8, 0))
        ttk.Label(footer, text='支持记忆清单、查找、详情、历史、保存结果及个人事件的只读查询；link、archive 两个编号或 delete、block、unblock 一个编号只生成预览。确认须点击按钮。关闭此窗口不停止后台。', wraplength=900).pack(anchor='w', pady=(6, 0))
        self.tabs.pack(fill='both', expand=True)
        window.after(80, self.poll)
        self.refresh()

    @staticmethod
    def write(widget, text):
        widget.configure(state='normal')
        widget.delete('1.0', 'end')
        widget.insert('1.0', text)
        widget.configure(state='disabled')

    def invalidate(self):
        self.preview = None
        self.saved_detail = None
        self.operation = None
        self.approve.configure(state='disabled')
        self.reject.configure(state='disabled')
        if getattr(self,'organize_daily',None) is not None:self.organize_daily.configure(state='disabled')
        if getattr(self,'cancel_daily_overlap',None) is not None:self.cancel_daily_overlap.configure(state='disabled')
        for button in self.operation_buttons:
            button.configure(state='disabled')
        self.confirm_operation.configure(state='disabled')
        if self.saves is not None:self.saves.invalidate()
        if self.actions is not None:self.actions.invalidate()
        if self.event_pane is not None:self.event_pane.invalidate()
        if getattr(self,'library_pane',None) is not None:self.library_pane.invalidate()
        if getattr(self,'read_pane',None) is not None:self.read_pane.invalidate()

    def work(self, function, complete):
        if self.busy or self.closed:
            return False
        self.busy = True
        self.refresh_button.configure(state='disabled')
        self.owners.configure(state='disabled')
        self.approve.configure(state='disabled')
        self.reject.configure(state='disabled')
        if getattr(self,'organize_daily',None) is not None:self.organize_daily.configure(state='disabled')
        if getattr(self,'cancel_daily_overlap',None) is not None:self.cancel_daily_overlap.configure(state='disabled')
        for button in self.operation_buttons:
            button.configure(state='disabled')
        self.confirm_operation.configure(state='disabled')
        if self.saves is not None:self.saves.disable()
        if self.actions is not None:self.actions.disable()
        if self.event_pane is not None:self.event_pane.disable()
        if getattr(self,'library_pane',None) is not None:self.library_pane.disable()
        self.status.set('正在读取或处理，请稍候……')
        def worker():
            try:
                self.events.put((complete, function(), None))
            except Exception as error:
                self.events.put((complete, None, str(error) if isinstance(error, ConsoleError) else '未能确认结果，请重新读取原条目。'))
        threading.Thread(target=worker, daemon=True).start()
        return True

    def poll(self):
        if self.closed:
            return
        progress=getattr(self,'progress_events',None)
        if progress is not None:
            while True:
                try:callback,state=progress.get_nowait()
                except queue.Empty:break
                try:callback(state)
                except Exception:pass
        try:
            complete, data, error = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.refresh_button.configure(state='normal')
            self.owners.configure(state='readonly')
            if error:
                self.invalidate()
                self.status.set(error + ' 当前展示的正文保留供核对。')
            else:
                self.status.set('已读取。')
                try:complete(data)
                except Exception:
                    # A display failure cannot authorize another write or
                    # stop polling permanently after a committed operation.
                    self.invalidate()
                    self.status.set('结果显示未完成，请刷新并核对原条目；不要重复提交。')
        self.window.after(80, self.poll)

    def refresh(self):
        if self.busy:
            return
        owner = self.owner.get()
        self.invalidate()
        self.actions.clear_pair()
        def fetch():
            owners = self.client.accounts()
            if not owners:
                raise ConsoleError('后台尚未配置主人账号，请先检查原 QQ 配置。')
            catalog = self.client.catalog(owner if owner in owners else owners[0])
            return catalog, self.client.listing(catalog['owner_id']), self.client.saves(catalog['owner_id']), self.client.actions(catalog['owner_id']), self.client.events(catalog['owner_id']), self.client.library(catalog['owner_id']), self.client.reads(catalog['owner_id'])
        def show(data):
            catalog, listing, saves, actions, events, library, reads = data
            self.owner.set(catalog['owner_id'])
            self.owners.configure(values=catalog['owners'])
            self.show_listing(listing)
            self.page = 1
            self.query.set('')
            self.show_catalog(catalog)
            self.saves.mode.set('保存请求')
            self.saves.query.set('')
            self.saves.show_listing(saves)
            self.actions.show_listing(actions)
            self.event_pane.show_listing(events)
            self.library_pane.show_listing(library)
            self.read_pane.show_listing(reads)
        self.work(fetch, show)

    def show_listing(self, listing):
        self.invalidate()
        self.entries.delete(*self.entries.get_children())
        for entry in listing['entries']:
            self.entries.insert('', 'end', iid=entry['id'], values=(KINDS.get(entry['kind'], entry['kind']), entry['subject'], entry['day'], STATES.get(entry['state'], entry['state'])))
        policy = listing['policy']
        self.policy.set(f"每日整理{'已开启' if policy['enabled'] else '未开启'} · {policy['timezone']} 00:00 结算 · 待批保留三天 · 由主人统一批准")
        if listing.get('error'):
            self.status.set('后台整理诊断：' + listing['error'])

    def select_entry(self, _=None):
        if self.busy:
            return
        selected = self.entries.selection()
        self.invalidate()
        if selected:
            owner = self.owner.get()
            self.work(lambda: self.client.preview(owner, selected[0]), self.show_preview)

    def show_preview(self, preview):
        self.invalidate()
        self.tabs.select(0)
        self.write(self.body, preview_text(preview))
        self.preview = preview
        if preview.get('overlap_available'):self.organize_daily.configure(state='normal')
        if preview.get('overlap_anchors') or preview.get('overlap_stale'):self.cancel_daily_overlap.configure(state='normal')
        if preview['state'] == 'pending':
            self.reject.configure(state='normal')
            if not preview.get('waiting_on') and not preview.get('overlap_stale'):
                self.approve.configure(state='normal')
        self.status.set('完整预览已读取；请核对正文、对象和来源后决定。')

    def prepare_daily_overlap(self,*,cancel=False):
        if self.busy or not self.preview:return
        preview=dict(self.preview);owner=self.owner.get();self.invalidate()
        self.work(lambda:self.client.overlap(owner,preview,daily=True,cancel=cancel),self.show_preview)

    def decide(self, action):
        if self.busy or not self.preview:
            return
        preview = dict(self.preview)
        owner = self.owner.get()
        label = '保留到长期记忆' if action == 'approve' else '本次不保存'
        if not messagebox.askokcancel(label, f"确认对刚才完整预览的这条内容执行“{label}”？\n对象：{preview['subject']}　日期：{preview['day']}", parent=self.window):
            return
        self.invalidate()
        def done(result):
            self.status.set('处理结果：' + STATES.get(result.get('state'), result.get('state', '待核对')) + '。可刷新查看原结果。')
            self.write(self.body, preview_text(preview) + '\n\n本次结果\n' + json.dumps(result, ensure_ascii=False, indent=2))
        self.work(lambda: self.client.decide(owner, preview, action), done)

    def show_catalog(self, data):
        self.snapshot = data['snapshot']
        self.has_next = data['has_next']
        self.page = data['page']
        self.saved.delete(*self.saved.get_children())
        for item in data['items']:
            self.saved.insert('', 'end', iid=item['id'], values=(item['title'] or item['preview'], STATES.get(item['status'], item['status'])))
        self.prev.configure(state='normal' if self.page > 1 else 'disabled')
        self.next.configure(state='normal' if self.has_next else 'disabled')
        self.write(self.saved_body, f"共 {data['total']} 条 · 第 {self.page} 页。请选择条目读取完整正文。")

    def load_saved(self, delta=0, reset=False):
        if self.busy:
            return
        page = 1 if reset else max(1, self.page + delta)
        query = self.query.get()
        owner = self.owner.get()
        self.invalidate()
        snapshot = '' if reset else self.snapshot
        self.work(lambda: self.client.catalog(owner, page, query, snapshot), self.show_catalog)

    def select_saved(self, _=None):
        if self.busy:
            return
        selected = self.saved.selection()
        self.invalidate()
        if selected:
            owner = self.owner.get()
            def show(data):
                memory = data['memory']
                self.write(self.saved_body, f"{memory.get('title') or ''}\n编号：{memory['id']}\n\n完整正文\n{memory.get('content') or ''}\n\n来源\n" + json.dumps(memory.get('origin') or {}, ensure_ascii=False, indent=2))
                self.saved_detail = data
                if not memory.get('is_locked') and not memory.get('managed_slot') and memory.get('source') != 'assistant_role':
                    for button in self.operation_buttons:
                        button.configure(state='normal')
            self.work(lambda: self.client.detail(owner, selected[0]), show)

    def prepare_operation(self, action):
        if self.busy or not self.saved_detail:
            return
        detail, owner = self.saved_detail, self.owner.get()
        self.invalidate()
        def show(preview):
            self.operation = preview
            self.write(self.saved_body, f"准备{OPERATIONS[preview['action']]}\n编号：{preview['memory_id']}\n\n完整正文\n{preview['body']}\n\n来源\n" +
                       json.dumps(preview['origin'], ensure_ascii=False, indent=2) + '\n\n影响范围\n' + preview['message'])
            self.confirm_operation.configure(state='normal')
            self.status.set('尚未执行。请核对完整正文和影响范围，再点击“确认上述操作”。确认有效期十分钟，重启或切换话题会作废。')
        self.work(lambda: self.client.operation_preview(owner, detail, action), show)

    def edit_saved(self):
        if self.busy or not self.saved_detail:
            return
        detail, owner = self.saved_detail, self.owner.get()
        editor = tk.Toplevel(self.window)
        editor.title('编辑长期记忆 · 完整差异预览')
        editor.geometry('880x650')
        editor.minsize(760,570)
        editor.transient(self.window)
        editor._preview = None
        frame=ttk.Frame(editor,padding=14);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text=f"主人 {owner} · {detail['memory']['id']}",wraplength=820).pack(anchor='w')
        operation=tk.StringVar(value='更正整条')
        mode=ttk.Combobox(frame,textvariable=operation,values=('更正整条','补充内容'),state='readonly')
        mode.pack(anchor='w',pady=6)
        status=tk.StringVar(value='编辑后先读取完整差异，确认后仍更新原编号。')
        ttk.Label(frame,textvariable=status,wraplength=820).pack(anchor='w')
        footer=ttk.Frame(frame);footer.pack(side='bottom',fill='x',pady=(8,0))
        preview_button=ttk.Button(footer,text='读取完整修改预览')
        preview_button.pack(side='left')
        confirm_button=ttk.Button(footer,text='确认这次修改',state='disabled')
        confirm_button.pack(side='right')
        input_text=ScrolledText(frame,wrap='word',height=7,font=('Microsoft YaHei UI',11))
        input_text.pack(fill='x',pady=6)
        input_text.insert('1.0',detail['memory'].get('content') or '')
        output=ScrolledText(frame,wrap='word',height=8,state='disabled',font=('Microsoft YaHei UI',11))
        output.pack(fill='both',expand=True)
        def changed(_=None):
            if input_text.edit_modified():
                editor._preview=None;confirm_button.configure(state='disabled');input_text.edit_modified(False)
        input_text.bind('<<Modified>>',changed)
        input_text.edit_modified(False)
        def switch(_):
            input_text.delete('1.0','end')
            if operation.get()=='更正整条':input_text.insert('1.0',detail['memory'].get('content') or '')
        mode.bind('<<ComboboxSelected>>',switch)
        def request(confirm=False):
            if self.busy or not editor.winfo_exists():return
            if owner!=self.owner.get():status.set('主人账号已切换，请关闭此编辑窗口后重新读取。');return
            snapshot=editor._preview
            if confirm and not snapshot:return
            if confirm and not messagebox.askokcancel('确认修改','确认提交刚才完整预览的修改？原编号保留。',parent=editor):return
            content=input_text.get('1.0','end-1c')
            action='append' if operation.get()=='补充内容' else 'correct'
            editor._preview=None
            preview_button.configure(state='disabled');confirm_button.configure(state='disabled')
            input_text.configure(state='disabled');mode.configure(state='disabled')
            status.set('正在核对……')
            def call():
                try:return self.client.revision_confirm(owner,snapshot) if confirm else self.client.revision_preview(owner,detail,action,content)
                except ConsoleError as error:return {'status':'error','message':str(error)}
            def show(result):
                self.status.set('修改状态：'+str(result.get('status','待核对'))+'。')
                if not editor.winfo_exists():return
                input_text.configure(state='normal');mode.configure(state='readonly')
                preview_button.configure(state='normal')
                if result.get('status')=='preview':
                    self.write(output,'原完整正文\n'+result['old']+'\n\n拟保存完整正文\n'+result['new'])
                    editor._preview=result;confirm_button.configure(state='normal')
                    status.set('尚未修改。请核对完整前后正文，再点击确认。')
                else:
                    labels={'applied':'已更新原条目。请关闭窗口后刷新查看。','noop':'内容没有变化，未新增修订。','done':'这次修改已处理，不会再次执行。',
                            'stale':'版本或使用状态变化，请重新读取条目。','unknown':'结果待核对，不会自动重试。','partial':'写入或投影待核对，请重新读取。'}
                    status.set(result.get('message') or labels.get(result.get('status'),'本次未确认执行，请重新读取核对。'))
                    if confirm:preview_button.configure(state='disabled')
            self.work(call,show)
        preview_button.configure(command=lambda:request(False))
        confirm_button.configure(command=lambda:request(True))

    def commit_operation(self):
        if self.busy or not self.operation:
            return
        preview, owner = self.operation, self.owner.get()
        if not messagebox.askokcancel('确认操作', f"确认{OPERATIONS[preview['action']]}？\n{preview['title']}\n{preview['message']}", parent=self.window):
            return
        self.invalidate()
        def done(result):
            state = result.get('operation_state') or result.get('status')
            messages = {'done': '该操作已处理，请刷新查看当前状态。', 'unknown': '结果待核对，不会自动重试。',
                        'restart_invalidated': '后台已重启，旧确认作废。请重新读取条目及操作预览。',
                        'expired': '确认已过期，请重新读取。', 'stale': '版本或内容已变化，请重新读取。'}
            self.status.set(messages.get(state, '本次没有确认执行成功，请重新读取原条目核对。'))
            self.write(self.saved_body, '本次操作结果\n' + json.dumps(result, ensure_ascii=False, indent=2))
        self.work(lambda: self.client.operation_confirm(owner, preview), done)

    def run_command(self):
        if self.busy or not self.command_text.get().strip():
            return
        text = self.command_text.get().strip()
        owner = self.owner.get()
        self.invalidate()
        def show(result):
            if result['kind'] == 'preview':
                self.show_preview(result['data'])
            elif result['kind'] == 'list':
                self.show_listing(result['data'])
            elif result['kind'] == 'action_preview':
                self.actions.show(result['data'])
            elif result['kind'] == 'native_read':
                self.read_pane.show(result['data'])
            else:
                self.tabs.select(0)
                self.write(self.body, result['text'])
        self.work(lambda: self.client.command(owner, text), show)

    def close(self):
        self.closed = True
        self.window.destroy()


def open_memory_panel(parent, port=8000):
    existing = getattr(parent, '_saya_memory_console', None)
    if existing is not None and existing.winfo_exists():
        existing.deiconify()
        existing.lift()
        existing.focus_set()
        return existing
    window = tk.Toplevel(parent)
    parent._saya_memory_console = window
    window._console = MemoryPanel(window, port)
    return window


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    root = tk.Tk()
    MemoryPanel(root, args.port)
    root.mainloop()
