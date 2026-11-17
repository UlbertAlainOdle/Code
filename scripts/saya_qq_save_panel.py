"""Native explicit-save selection and old-candidate review within the manager."""
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText


SAVE_STATES = {'select':'请选择来源', 'pending':'等待确认', 'saving':'保存中', 'done':'已保存',
               'partial':'未全部保存', 'unknown':'结果待核对', 'expired':'已过期',
               'invalid':'来源已变化', 'rejected':'本次不保存'}
SAVE_STATES['ignored']='已忽略原候选'


def save_text(data):
    parts = [SAVE_STATES.get(data['state'], data['state'])]
    ignoring=data.get('requested_action')=='ignore'
    if ignoring:parts.append('本次请求：忽略原候选；不会保存为长期记忆。')
    if data.get('expires'):
        parts.append('本次来源及确认截止：'+datetime.fromtimestamp(data['expires']).astimezone().strftime('%Y-%m-%d %H:%M %Z'))
    for i, option in enumerate(data.get('options', []), 1):
        parts += [f'\n可选来源 {i} · {option["author"]}原文', option['text']]
    for i, plan in enumerate(data.get('plans', []), 1):
        parts += [f'\n'+('拟忽略的原候选' if ignoring else f'拟保存第 {i} 条')+f' · 作者：{plan["author"]} · 完整正文', plan['content']]
        for source in plan['sources']:
            if source.get('text'):
                parts += ['\n实际保存请求原话', source['text']]
            if source.get('candidate'):
                parts.append('原候选编号：'+source['candidate'])
    result = data.get('result') or {}
    if result.get('operation_id'):
        parts += ['\n原操作编号：'+result['operation_id']]
    for i, item in enumerate(result.get('items', []), 1):
        state = {'committed':'已提交', 'invalid':'来源或权限已变化', 'not_saved':'未新增', 'unknown':'待核对', 'pending':'未执行'}.get(item['state'], item['state'])
        if item.get('candidate_ignored'):state='已忽略原候选，未新增长期记忆'
        parts.append(f'{i}. {state}'+(' · '+item['memory_id'] if item.get('memory_id') else ''))
    return '\n'.join(parts)


class SavePane:
    def __init__(self, notebook, host):
        self.host = host
        self.current = None
        self.candidates = {}
        self.page = 1
        self.snapshot = ''
        self.frame = ttk.Frame(notebook, padding=8)
        notebook.add(self.frame, text='明确保存与旧候选')
        bar = ttk.Frame(self.frame)
        bar.pack(fill='x')
        self.mode = tk.StringVar(value='保存请求')
        mode = ttk.Combobox(bar, textvariable=self.mode, values=('保存请求', '旧候选'), state='readonly', width=12)
        mode.pack(side='left', padx=(0, 8))
        mode.bind('<<ComboboxSelected>>', lambda _: self.load())
        self.mode_widget = mode
        self.query = tk.StringVar()
        ttk.Entry(bar, textvariable=self.query).pack(side='left', fill='x', expand=True)
        ttk.Button(bar, text='刷新／查找', command=self.load).pack(side='left', padx=6)
        self.previous = ttk.Button(bar, text='上一页', command=lambda:self.load(-1), state='disabled')
        self.next = ttk.Button(bar, text='下一页', command=lambda:self.load(1), state='disabled')
        self.previous.pack(side='left')
        self.next.pack(side='left', padx=(6, 0))
        listing = ttk.Frame(self.frame)
        listing.pack(fill='x', pady=8)
        self.tree = ttk.Treeview(listing, columns=('title','state'), show='headings', height=4, selectmode='browse')
        self.tree.heading('title', text='保存请求／候选')
        self.tree.heading('state', text='状态')
        self.tree.column('title', width=650)
        self.tree.column('state', width=140)
        self.tree.pack(side='left', fill='x', expand=True)
        scroll = ttk.Scrollbar(listing, command=self.tree.yview)
        scroll.pack(side='right', fill='y')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<<TreeviewSelect>>', self.open)
        self.tree.bind('<Button-1>', lambda _: 'break' if host.busy else None)
        self.tree.bind('<Key>', lambda _: 'break' if host.busy else None)
        actions = ttk.Frame(self.frame)
        actions.pack(side='bottom', fill='x', pady=(8, 0))
        self.select = ttk.Button(actions, text='按所选来源生成完整预览', command=self.choose, state='disabled')
        self.select.pack(side='left', padx=(0, 8))
        self.approve = ttk.Button(actions, text='确认保存完整预览', command=lambda:self.decide('approve'), state='disabled')
        self.approve.pack(side='left', padx=(0, 8))
        self.reject = ttk.Button(actions, text='本次不保存', command=lambda:self.decide('reject'), state='disabled')
        self.reject.pack(side='left')
        self.ignore = ttk.Button(actions,text='忽略原候选',command=lambda:self.decide('ignore'),state='disabled')
        self.ignore.pack(side='right')
        self.choice_frame = ttk.Frame(self.frame)
        ttk.Label(self.choice_frame, text='点击切换选择，可选多条；下方展示每条完整原文。').pack(anchor='w')
        self.choices = tk.Listbox(self.choice_frame, selectmode='multiple', exportselection=False, height=3)
        self.choices.pack(fill='x')
        self.body = ScrolledText(self.frame, wrap='word', state='disabled', font=('Microsoft YaHei UI',11), height=10)
        self.body.pack(fill='both', expand=True)

    def disable(self):
        for button in (self.select, self.approve, self.reject, self.ignore):
            button.configure(state='disabled')
        self.mode_widget.configure(state='disabled' if self.host.busy else 'readonly')

    def invalidate(self):
        self.current = None
        self.disable()
        self.choice_frame.pack_forget()

    def show_listing(self, data, *, candidate=False):
        self.invalidate()
        self.tree.delete(*self.tree.get_children())
        self.candidates = {x['id']:x['content_version'] for x in data.get('items', [])} if candidate else {}
        if candidate:
            self.page, self.snapshot = data['page'], data['snapshot']
            rows = data['items']
        else:
            query = self.query.get().strip().casefold()
            rows = [x for x in data['entries'] if query in x.get('title','').casefold()]
        for item in rows:
            title = item.get('title') or item.get('preview') or '原操作结果'
            state = item.get('state') or item.get('status')
            self.tree.insert('', 'end', iid=item['id'], values=(title, SAVE_STATES.get(state,state)))
        self.previous.configure(state='normal' if candidate and self.page>1 else 'disabled')
        self.next.configure(state='normal' if candidate and data['has_next'] else 'disabled')
        self.host.write(self.body, '请选择一项读取全文。这里的“本次不保存”只取消这次保存待办，不会自动忽略或删除原候选。')

    def load(self, delta=0):
        if self.host.busy:
            return
        owner = self.host.owner.get()
        candidate = self.mode.get() == '旧候选'
        page = max(1,self.page+delta) if delta else 1
        query, snapshot = self.query.get(), self.snapshot if delta else ''
        self.host.invalidate()
        self.host.work(lambda: self.host.client.candidates(owner,page,query,snapshot) if candidate else self.host.client.saves(owner),
                       lambda data:self.show_listing(data,candidate=candidate))

    def open(self, _=None):
        if self.host.busy:
            return
        selection = self.tree.selection()
        if not selection:
            return
        identifier, owner = selection[0], self.host.owner.get()
        version = self.candidates.get(identifier)
        self.host.invalidate()
        self.host.work(lambda:self.host.client.candidate_preview(owner,identifier,version) if version else self.host.client.save_preview(owner,identifier), self.show)

    def show(self, data):
        self.invalidate()
        self.current = data
        self.host.tabs.select(self.frame)
        self.host.write(self.body, save_text(data))
        self.choices.delete(0,'end')
        self.choice_frame.pack_forget()
        if data['state']=='select':
            for i,o in enumerate(data['options'],1):
                self.choices.insert('end',f'{i}. {o["author"]}：{o["text"][:70]}')
            self.choice_frame.pack(fill='x',before=self.body,pady=(0,6))
            self.select.configure(state='normal')
        if data['state'] in {'select','pending'}:
            self.reject.configure(state='normal')
        if data['state']=='pending':
            if data.get('requested_action')!='ignore':self.approve.configure(state='normal')
            if data.get('channel')=='local_candidate' or data.get('requested_action')=='ignore':self.ignore.configure(state='normal')
        self.host.status.set(('尚未忽略，请核对原候选全文和本次期限。' if data.get('requested_action')=='ignore' else '尚未保存，请核对完整原文、作者和本次期限。') if data['state'] in {'select','pending'} else '已读取原操作结果，不会重新执行。')

    def choose(self):
        if self.host.busy or not self.current:
            return
        indices = list(self.choices.curselection())
        if not indices:
            self.host.status.set('请先点击选择至少一条来源。')
            return
        owner, data = self.host.owner.get(), self.current
        self.host.invalidate()
        self.host.work(lambda:self.host.client.save_select(owner,data,indices),self.show)

    def decide(self, action):
        if self.host.busy or not self.current:
            return
        owner, data = self.host.owner.get(), self.current
        label = '保存上方全部完整正文' if action=='approve' else '取消这次保存待办'
        if action=='ignore':label='将这条原候选标记为忽略；原聊天不变，也不会新增记忆'
        if not messagebox.askokcancel('确认',f'{label}？\n本次共有 {len(data.get("plans",[]))} 条拟保存内容。',parent=self.host.window):
            return
        self.host.invalidate()
        def done(result):
            self.host.write(self.body,save_text(result))
            self.host.status.set('处理结果：'+SAVE_STATES.get(result['state'],result['state'])+'。可刷新查看原结果。')
        self.host.work(lambda:self.host.client.save_decide(owner,data,action),done)
