"""Local-only original query results, separate from approval and chat."""
from datetime import datetime
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText


class ReadPane:
    def __init__(self,notebook,host):
        self.host=host;self.frame=ttk.Frame(notebook,padding=8);notebook.add(self.frame,text='查询与回执')
        bar=ttk.Frame(self.frame);bar.pack(fill='x')
        ttk.Label(bar,text='QQ中的技术查询在这里读取当前结果；不确认保存或执行操作。也可在底部输入只读查询。',wraplength=800).pack(side='left')
        ttk.Button(bar,text='刷新查询',command=self.load).pack(side='right')
        self.tree=ttk.Treeview(self.frame,columns=('label','at'),show='headings',height=5,selectmode='browse')
        for name,title,width in [('label','原查询',600),('at','收到时间',240)]:self.tree.heading(name,text=title);self.tree.column(name,width=width)
        self.tree.pack(fill='x',pady=8);self.tree.bind('<<TreeviewSelect>>',self.open)
        self.tree.bind('<Button-1>',lambda _:'break' if host.busy else None);self.tree.bind('<Key>',lambda _:'break' if host.busy else None)
        self.body=ScrolledText(self.frame,wrap='word',state='disabled',font=('Microsoft YaHei UI',11));self.body.pack(fill='both',expand=True)

    def invalidate(self):self.host.write(self.body,'')
    def disable(self):pass

    def load(self):
        if self.host.busy:return
        owner=self.host.owner.get();self.host.work(lambda:self.host.client.reads(owner),self.show_listing)

    def show_listing(self,data):
        self.tree.delete(*self.tree.get_children())
        for row in data.get('entries',[]):
            at=datetime.fromtimestamp(row['created']).astimezone().strftime('%m-%d %H:%M')
            self.tree.insert('','end',iid=row['id'],values=(row['label'],at))

    def open(self,_=None):
        if self.host.busy:return
        chosen=self.tree.selection();owner=self.host.owner.get();self.host.invalidate()
        if chosen:self.host.work(lambda:self.host.client.read_query(owner,chosen[0]),self.show)

    def show(self,data):
        self.host.invalidate();self.host.tabs.select(self.frame)
        self.host.write(self.body,'只读查询：'+data['query']+'\n\n'+data['text'])
        self.host.status.set('当前查询结果已读取，没有保存或修改记忆／事件。')
