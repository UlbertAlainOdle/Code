"""Owner-triggered bounded semantic review. Suggestions cannot change memories."""
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

RELATIONS={'duplicate':'可能完全重复','complementary':'内容互补','conflict_or_version':'冲突或不同版本',
           'separate':'分别保留','unknown':'尚不能判断'}


def library_text(data):
    parts=[RELATIONS.get(data['relation'],data['relation']),
           '以下是两条完整原文。语义判断只是建议；不同日期、条件、理由或额外事实应分别保留。']
    if data.get('review_method')=='host_exact_source_copy':
        parts.append('主机核对：完整正文及已确认原始来源相同，未调用语义模型；仍须完整预览和确认。')
    for key,label in [('first','第一条'),('second','第二条')]:
        r=data[key]
        parts += ['\n'+label+' · '+r['id'],
                  f"记录日期：{r.get('recorded_day') or '未记录'}　来源类型：{r.get('source') or '未记录'}",
                  '原记录作者：'+str(r.get('author') or 'unknown')+'　范围：'+str(r['scope']),
                  '重要或受保护：'+('是' if r['protected'] else '否'),r['body']]
    if data.get('guard'):parts.append('\n主机检测到范围或事实条件不同，本次不提供停用。')
    parts.append('\n完全重复可选择保留项并预览停用；每日与重要项部分重合可提出只移除完整覆盖句段的整理建议。均须核对完整修改预览后确认，不释放存储容量。')
    return '\n'.join(parts)


class LibraryPane:
    def __init__(self,notebook,host):
        self.host=host;self.current=None;self.listing=None
        self.frame=ttk.Frame(notebook,padding=8);notebook.add(self.frame,text='全库审阅与去重')
        ttk.Label(self.frame,text='按同一范围逐对审阅，包括用词不同的条目；不会自动合并、停用或删除。每次按钮最多4批、最多4次模型请求。',wraplength=880).pack(anchor='w')
        bar=ttk.Frame(self.frame);bar.pack(fill='x',pady=6)
        self.scan_button=ttk.Button(bar,text='开始／继续审阅',command=self.scan,state='disabled');self.scan_button.pack(side='left')
        ttk.Button(bar,text='刷新进度',command=self.load).pack(side='left',padx=8)
        self.only_duplicates=tk.BooleanVar(value=True)
        ttk.Checkbutton(bar,text='只看重复／可整理',variable=self.only_duplicates,command=self.render).pack(side='left')
        self.progress=ttk.Label(self.frame,text='尚未读取',wraplength=880);self.progress.pack(anchor='w')
        listing=ttk.Frame(self.frame);listing.pack(fill='x',pady=6)
        self.tree=ttk.Treeview(listing,columns=('first','second','relation'),show='headings',height=4,selectmode='browse')
        for name,title,width in [('first','第一条',320),('second','第二条',320),('relation','审阅建议',170)]:
            self.tree.heading(name,text=title);self.tree.column(name,width=width)
        self.tree.pack(side='left',fill='x',expand=True)
        scroll=ttk.Scrollbar(listing,command=self.tree.yview);scroll.pack(side='right',fill='y');self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<<TreeviewSelect>>',self.open)
        self.tree.bind('<Button-1>',lambda _:'break' if host.busy else None)
        self.tree.bind('<Key>',lambda _:'break' if host.busy else None)
        controls=ttk.Frame(self.frame);controls.pack(side='bottom',fill='x',pady=(8,0))
        self.keep_buttons={}
        for keep,label in [('first','保留第一条，预览停用第二条'),('second','保留第二条，预览停用第一条')]:
            b=ttk.Button(controls,text=label,command=lambda k=keep:self.prepare(k),state='disabled');b.pack(side='left',padx=(0,8));self.keep_buttons[keep]=b
        self.overlap_button=ttk.Button(controls,text='预览每日重合整理',command=self.prepare_overlap,state='disabled')
        self.overlap_button.pack(side='left')
        self.body=ScrolledText(self.frame,wrap='word',state='disabled',font=('Microsoft YaHei UI',11));self.body.pack(fill='both',expand=True)

    def disable(self):
        self.scan_button.configure(state='disabled')
        self.overlap_button.configure(state='disabled')
        for b in self.keep_buttons.values():b.configure(state='disabled')

    def invalidate(self):
        self.current=None
        self.overlap_button.configure(state='disabled')
        for b in self.keep_buttons.values():b.configure(state='disabled')

    def load(self):
        if self.host.busy:return
        owner=self.host.owner.get();self.invalidate()
        self.host.work(lambda:self.host.client.library(owner),self.show_listing)

    def show_listing(self,data):
        self.listing=data;self.invalidate();self.render();self.show_progress(data)
        self.scan_button.configure(state='normal' if data.get('available',True) and data.get('cursor',0)<data.get('total_pairs',0) else 'disabled')

    def show_progress(self,data):
        if not data.get('available',True):self.progress.configure(text='全库审阅尚未准备好。');return
        text=f"本次范围内 {data['eligible']} 条，排除 {data['excluded']} 条；已审阅 {data['cursor']}/{data['total_pairs']} 对；本快照已开始 {data['requests']} 次模型请求。"
        if data.get('error'):text+=' 上次未完成，先刷新再继续；未自动修改记忆。'
        if data.get('state')=='complete':text+=' 当前范围已审阅完。'
        self.progress.configure(text=text)

    def render(self):
        if self.host.busy:return
        self.invalidate();self.tree.delete(*self.tree.get_children())
        for row in (self.listing or {}).get('entries',[]):
            if self.only_duplicates.get() and row['relation']!='duplicate' and not row.get('overlap_available'):continue
            self.tree.insert('','end',iid=row['id'],values=(row['first_title'] or row['first'],row['second_title'] or row['second'],RELATIONS[row['relation']]))

    def scan(self):
        if self.host.busy or not self.listing or not self.listing.get('available',True):return
        owner=self.host.owner.get();initial=dict(self.listing);self.invalidate()
        def run():
            state=initial
            for _ in range(4):
                if self.host.closed or state['cursor']>=state['total_pairs']:break
                state=self.host.client.library_scan(owner,state)
                self.host.progress_events.put((self.show_progress,state))
            return state
        self.host.work(run,self.show_listing)

    def open(self,_=None):
        if self.host.busy:return
        selected=self.tree.selection();self.invalidate();owner=self.host.owner.get()
        if selected:self.host.work(lambda:self.host.client.library_preview(owner,selected[0]),self.show)

    def show(self,data):
        self.host.invalidate();self.host.tabs.select(self.frame);self.current=data
        self.host.write(self.body,library_text(data))
        if data.get('overlap_available'):self.overlap_button.configure(state='normal')
        if data['relation']=='duplicate' and not data.get('guard'):
            for keep,other in [('first','second'),('second','first')]:
                if not data[other]['protected']:self.keep_buttons[keep].configure(state='normal')
        self.host.status.set('已读取两条完整原文，请选择适用的整理方式后核对完整预览。')

    def prepare(self,keep):
        if self.host.busy or not self.current:return
        owner=self.host.owner.get();preview=dict(self.current);self.invalidate()
        self.host.work(lambda:self.host.client.library_prepare(owner,preview,keep),self.host.actions.show)

    def prepare_overlap(self):
        if self.host.busy or not self.current:return
        owner=self.host.owner.get();preview=dict(self.current);self.invalidate()
        self.host.work(lambda:self.host.client.overlap(owner,preview),self.host.actions.show)
