"""Full native previews for owner-private event-operation requests."""
from datetime import datetime
from tkinter import ttk,messagebox
from tkinter.scrolledtext import ScrolledText

ACTION={'stop':'停止跟进','pause':'暂停跟进','resume':'恢复跟进','delete':'删除卡片正文','cancel':'标记取消','complete':'标记完成'}
ACTION.update(keep='优先保留',unkeep='解除优先保留',importance='调整重要度',timing='调整日期')
SCOPE={'personal':'本人的个人事件','group_following':'本人的群事件跟进','group_common':'全群共同事件','both':'个人事件与本人的群跟进'}
STATE={'native_prepared':'等待核对','done':'已执行','refused':'本次不执行','expired':'已过期','superseded':'已被后续请求取代','stale':'来源或权限已变化','failed':'未完成，请核对'}
STATE['incomplete_sources']='来源过长，不能完整确认'


def event_text(data):
    parts=[ACTION[data['action']]+' · '+STATE.get(data['state'],data['state']),
           '影响范围：'+SCOPE[data['scope']],'原聊天和独立长期记忆保留。']
    if data['state']=='native_prepared':
        parts.append('核对截止：'+datetime.fromtimestamp(data['expires']).astimezone().strftime('%Y-%m-%d %H:%M %Z'))
        parts.append('本次共 '+str(len(data['targets']))+' 张事件卡。')
    if data['scope'] in {'group_following','both'}:parts.append('群跟进只影响本人，其他成员的跟进保持。')
    if data['scope']=='group_common':parts.append('共同事件操作会影响其他成员，仍要求每张卡的协调人权限。')
    if data['action']=='delete':parts.append('本次删除卡片正文，不是停止跟进；执行后不能在这里撤销。')
    proposed=data.get('proposed') or {}
    if proposed:
        parts.append('只调整本人的事件设置，不延长原来源有效期。')
        if proposed.get('importance'):
            parts.append('拟设重要度：'+{'high':'高','normal':'普通','low':'低','auto':'自动（解除手动锁定，当前值保持）'}[proposed['importance']])
        if proposed.get('timing'):
            timing=proposed['timing'];parts.append('拟设'+('截止时间' if timing['kind']=='deadline' else '安排时间')+'：'+('清除' if timing['value']=='clear' else timing['value']))
    for target in data['targets']:
        parts+=['\n'+target['title'],'原事件：'+target['id']+(' · 群 '+target['group'] if target['group'] else '')]
        if target.get('settings'):
            settings=target['settings'];parts.append('当前优先保留：'+('是' if settings['keep'] else '否'))
            parts.append('当前重要度：'+{'high':'高','normal':'普通','low':'低'}.get(settings['importance'],'普通')+('（手动）' if settings['importance_locked'] else '（自动）'))
            for kind,value in settings.get('timing',{}).items():
                parts.append('当前'+('截止时间' if kind=='deadline' else '安排时间')+'：'+str(value.get('local') or {'removed':'未设置','unknown':'需澄清'}.get(value.get('status'),'未设置')))
        for source in target['sources']:
            parts+=['\n有效来源 · 作者 '+str(source.get('author') or '原发言者'),source['text']]
        if not target['sources']:parts.append('当前没有可展示的有效来源；不展示已过期正文。')
    pending=data.get('pending_dialogue') or {}
    if pending.get('count'):
        parts+=['\n同时影响 '+str(pending['count'])+' 条本人未归档讨论来源。']
        for source in pending.get('sources',[]):parts+=['\n未归档来源：'+source['id'],source['text']]
    if data['state']=='incomplete_sources':parts.append('未归档来源超过完整预览容量或原记录已截断；本次不能确认执行，请缩小范围。')
    result=data.get('result')
    if result:
        parts+=['\n原操作结果：'+STATE.get(data['state'],data['state'])]
        for change in result.get('changes',[]):parts.append('已处理原事件：'+change['id'])
    return '\n'.join(parts)


class EventPane:
    def __init__(self,notebook,host):
        self.host=host;self.current=None
        self.frame=ttk.Frame(notebook,padding=8);notebook.add(self.frame,text='事件操作待办')
        bar=ttk.Frame(self.frame);bar.pack(fill='x')
        ttk.Label(bar,text='主人私聊中需要核对的事件操作在这里决定。日常事件整理继续沿用原规则。',wraplength=850).pack(side='left')
        ttk.Button(bar,text='刷新事件待办',command=self.load).pack(side='right')
        listing=ttk.Frame(self.frame);listing.pack(fill='x',pady=8)
        self.tree=ttk.Treeview(listing,columns=('action','scope','state'),show='headings',height=5,selectmode='browse')
        for name,title,width in [('action','操作',250),('scope','影响范围',440),('state','状态',160)]:
            self.tree.heading(name,text=title);self.tree.column(name,width=width)
        self.tree.pack(side='left',fill='x',expand=True)
        scroll=ttk.Scrollbar(listing,command=self.tree.yview);scroll.pack(side='right',fill='y');self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<<TreeviewSelect>>',self.open)
        self.tree.bind('<Button-1>',lambda _:'break' if host.busy else None)
        self.tree.bind('<Key>',lambda _:'break' if host.busy else None)
        buttons=ttk.Frame(self.frame);buttons.pack(side='bottom',fill='x',pady=(8,0))
        self.confirm=ttk.Button(buttons,text='确认完整事件操作',state='disabled',command=lambda:self.decide('confirm'));self.confirm.pack(side='left',padx=(0,8))
        self.reject=ttk.Button(buttons,text='本次不执行',state='disabled',command=lambda:self.decide('reject'));self.reject.pack(side='left')
        self.body=ScrolledText(self.frame,wrap='word',state='disabled',font=('Microsoft YaHei UI',11));self.body.pack(fill='both',expand=True)

    def disable(self):
        self.confirm.configure(state='disabled');self.reject.configure(state='disabled')

    def invalidate(self):self.current=None;self.disable()

    def show_listing(self,data):
        self.invalidate();self.tree.delete(*self.tree.get_children())
        for row in data['entries']:
            self.tree.insert('','end',iid=row['id'],values=(ACTION[row['action']],SCOPE[row['scope']],STATE.get(row['state'],row['state'])))
        if not data.get('available',True):self.host.write(self.body,'事件服务尚未准备好，请在下次正常启动后台后刷新。')

    def load(self):
        if self.host.busy:return
        owner=self.host.owner.get();self.invalidate()
        self.host.work(lambda:self.host.client.events(owner),self.show_listing)

    def open(self,_=None):
        if self.host.busy:return
        chosen=self.tree.selection();self.invalidate();owner=self.host.owner.get()
        if chosen:self.host.work(lambda:self.host.client.event_preview(owner,chosen[0]),self.show)

    def show(self,data):
        self.host.invalidate();self.current=data;self.host.tabs.select(self.frame)
        self.host.write(self.body,event_text(data))
        if self.tree.exists(data['id']):self.tree.item(data['id'],values=(ACTION[data['action']],SCOPE[data['scope']],STATE.get(data['state'],data['state'])))
        if data['state']=='native_prepared':
            self.confirm.configure(state='normal');self.reject.configure(state='normal')
        self.host.status.set('事件操作状态已重新读取；只有完整预览仍有效时才能确认。')

    def decide(self,action):
        if self.host.busy or not self.current:return
        current=dict(self.current);owner=self.host.owner.get()
        text='是否确认以上完整范围的“'+ACTION[current['action']]+'”？' if action=='confirm' else '本次不执行，原事件保持，是否继续？'
        if not messagebox.askyesno('核对事件操作',text,parent=self.host.window):return
        self.invalidate();self.host.work(lambda:self.host.client.event_decide(owner,current,action),self.show)
