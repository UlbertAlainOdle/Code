"""Native previews for queued modifications and reversible source maintenance."""
from datetime import datetime
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText


KINDS={'link':'联合查看','archive':'归档重复副本','revision':'修改原条目',
       'delete':'删除原条目','block':'停止使用','unblock':'恢复使用'}
STATES={'prepared':'待核对','pending':'等待确认','active':'联合查看中','archived':'已归档',
        'finished':'已处理','applied':'已修改','success':'已执行','done':'已处理','undone':'已撤销',
        'unknown':'结果待核对','executing':'处理中','expired':'已过期','cancelled':'已取消',
        'stale':'版本或话题已变化','invalidated':'来源已变化','restart_invalidated':'重启后已作废',
        'partial':'结果待核对','unavailable':'当前不可用','superseded':'已被新操作取代'}


def action_text(data):
    view=data.get('view',{})
    parts=[KINDS.get(data['kind'],data['kind'])+' · '+STATES.get(data['status'],data['status'])]
    if data['status']=='pending':
        parts.append('确认截止：'+datetime.fromtimestamp(data['expires']).astimezone().strftime('%Y-%m-%d %H:%M %Z'))
    if data['kind']=='link':parts.append('分别保留原条目和条件，建立联合查看关系；可以撤销。')
    elif data['kind']=='archive':parts.append('保留第一条，将核对为同一来源的第二条归档；原正文和出处保留，可以撤销。')
    elif data['kind']=='delete':parts.append('仅删除所选长期记忆；原聊天保留。已删除内容不能在这里撤销。')
    elif data['kind']=='block':parts.append('停止使用原条目及已知派生内容，并从新话题继续；原聊天保留。')
    elif data['kind']=='unblock':parts.append('恢复原条目的使用；先前排除的历史不会自动重新加入。')
    if view.get('memory_id'):parts.append('原条目：'+view['memory_id'])
    if view.get('keep'):parts.append('保留原条目：'+view['keep'])
    if view.get('deduplication'):
        parts+=['\n语义去重：只停用重复项，保留条目保持可用；两条原文与来源均保留。此操作不释放存储容量。',
                '\n保留条目完整正文',view['keep_body'],'\n拟停用重复条目的完整正文']
    if view.get('archive'):parts.append('归档原条目：'+view['archive'])
    if 'old' in view:parts+=['\n原完整正文',view['old'],'\n拟修改完整正文',view['new']]
    for anchor in view.get('overlap_anchors',[]):
        parts += ['\n整理依据：已独立保存的重要项 · '+anchor['id']+' · 原作者 '+str(anchor['author']),
                  '仅移除完整重合句段，其他原文保持；不会停用整条每日记忆。',anchor['body']]
    if 'body' in view:parts+=['\n原条目完整正文',view['body']]
    for index,copies in enumerate(view.get('sources',[]),1):
        for copy in copies:
            source=copy['source']
            parts += [f'\n来源 {index} · 作者 {source["author"]} · 原条目 {copy["record_id"]}',source['text']]
    if data['status'] in {'unknown','partial','executing'}:
        parts.append('\n结果尚不能确认，请重新读取原条目，不要重复新建或提交。')
    if data['status']=='pending':parts.append('\n请完整核对以上内容，再点击确认；取消待办不会删除原记忆。')
    return '\n'.join(parts)


class ActionPane:
    def __init__(self,notebook,host):
        self.host=host;self.current=None;self.pair=[None,None]
        self.frame=ttk.Frame(notebook,padding=8);notebook.add(self.frame,text='整理与修改待办')
        bar=ttk.Frame(self.frame);bar.pack(fill='x')
        ttk.Label(bar,text='QQ中提出的修改和整理在这里核对。也可在“已保存记忆”中分别选两条加入整理。',wraplength=850).pack(side='left')
        ttk.Button(bar,text='刷新待办',command=self.load).pack(side='right')
        self.pair_labels=[]
        for i in range(2):
            label=ttk.Label(self.frame,text=f'第{i+1}条：未选择',wraplength=880)
            label.pack(anchor='w',pady=(4,0));self.pair_labels.append(label)
        create=ttk.Frame(self.frame);create.pack(fill='x',pady=6)
        self.create_buttons=[]
        for kind,label in [('link','预览联合查看'),('archive','预览归档第二条（保留第一条）')]:
            b=ttk.Button(create,text=label,command=lambda k=kind:self.prepare(k),state='disabled')
            b.pack(side='left',padx=(0,8));self.create_buttons.append(b)
        listing=ttk.Frame(self.frame);listing.pack(fill='x')
        self.tree=ttk.Treeview(listing,columns=('kind','state'),show='headings',height=4,selectmode='browse')
        for name,title,width in [('kind','操作',600),('state','状态',160)]:
            self.tree.heading(name,text=title);self.tree.column(name,width=width)
        self.tree.pack(side='left',fill='x',expand=True)
        scroll=ttk.Scrollbar(listing,command=self.tree.yview);scroll.pack(side='right',fill='y')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<<TreeviewSelect>>',self.open)
        self.tree.bind('<Button-1>',lambda _:'break' if host.busy else None)
        self.tree.bind('<Key>',lambda _:'break' if host.busy else None)
        controls=ttk.Frame(self.frame);controls.pack(side='bottom',fill='x',pady=(8,0))
        self.buttons={}
        for action,label in [('confirm','确认完整预览'),('undo','撤销这次整理'),('cancel','取消这次待办')]:
            b=ttk.Button(controls,text=label,command=lambda a=action:self.decide(a),state='disabled')
            b.pack(side='left',padx=(0,8));self.buttons[action]=b
        self.body=ScrolledText(self.frame,wrap='word',state='disabled',font=('Microsoft YaHei UI',11))
        self.body.pack(fill='both',expand=True,pady=(8,0))

    def disable(self):
        for b in [*self.buttons.values(),*self.create_buttons]:b.configure(state='disabled')

    def invalidate(self):
        self.current=None;self.disable()

    def clear_pair(self):
        self.pair=[None,None]
        for i,label in enumerate(self.pair_labels):label.configure(text=f'第{i+1}条：未选择')

    def choose(self,index):
        if self.host.busy or not self.host.saved_detail:return
        detail=self.host.saved_detail
        self.pair[index]=(self.host.owner.get(),detail)
        memory=detail['memory']
        self.pair_labels[index].configure(text=f'第{index+1}条：'+str(memory.get('title') or '')+' · '+memory['id'])
        self.host.tabs.select(self.frame);self.enable_pair()

    def enable_pair(self):
        if all(self.pair) and all(p[0]==self.host.owner.get() for p in self.pair):
            for b in self.create_buttons:b.configure(state='normal')

    def load(self):
        if self.host.busy:return
        owner=self.host.owner.get();self.invalidate()
        self.host.work(lambda:self.host.client.actions(owner),self.show_listing)

    def show_listing(self,data):
        self.invalidate();self.tree.delete(*self.tree.get_children())
        for row in data['entries']:
            self.tree.insert('','end',iid=row['id'],values=(KINDS[row['kind']],STATES.get(row['phase'],row['phase'])))
        self.enable_pair()

    def open(self,_=None):
        if self.host.busy:return
        selected=self.tree.selection();self.invalidate();owner=self.host.owner.get()
        if selected:self.host.work(lambda:self.host.client.action_preview(owner,selected[0]),self.show)

    def prepare(self,kind):
        if self.host.busy or not all(self.pair):return
        owner=self.host.owner.get()
        if any(p[0]!=owner for p in self.pair):return
        first,second=[p[1] for p in self.pair];self.invalidate()
        self.host.work(lambda:self.host.client.pair_preview(owner,kind,first,second),self.show)

    def show(self,data):
        self.host.invalidate();self.host.tabs.select(self.frame)
        if not data.get('id'):
            self.host.write(self.body,'这两条当前不满足整理条件，没有执行操作。请分别核对原条目。')
            self.host.status.set('未建立整理待办。');return
        self.current=data;self.host.write(self.body,action_text(data));self.enable_pair()
        values=(KINDS[data['kind']],STATES.get(data['status'],data['status']))
        if self.tree.exists(data['id']):self.tree.item(data['id'],values=values)
        else:self.tree.insert('','end',iid=data['id'],values=values)
        if data['status']=='pending':
            self.buttons['confirm'].configure(state='normal');self.buttons['cancel'].configure(state='normal')
        if (data['kind']=='link' and data['status'] in {'active','invalidated'}) or (data['kind']=='archive' and data['status']=='archived'):
            self.buttons['undo'].configure(state='normal')
        self.host.status.set('已重新读取完整内容，请核对后决定。')

    def decide(self,action):
        if self.host.busy or not self.current:return
        current=dict(self.current);owner=self.host.owner.get()
        prompt={'confirm':'是否确认上方完整预览中的操作？','undo':'是否撤销这一次整理？原条目和来源保持。',
                'cancel':'仅取消这次待办，原记忆保持，是否继续？'}[action]
        if not messagebox.askyesno('核对记忆操作',prompt,parent=self.host.window):return
        self.invalidate()
        self.host.work(lambda:self.host.client.action_decide(owner,current,action),self.show)
