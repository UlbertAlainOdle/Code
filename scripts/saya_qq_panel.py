"""Visible owner of manual service management. Closing means stop and verify."""
import queue,threading
import tkinter as tk
from tkinter import ttk,messagebox
import manage_qq_service as service
from saya_qq_control import inventory,stop_system


def execute(action,port=8000,progress=print):
    from start_saya_qq import start_system
    with service.InstanceLease(service.DIRECTORY/'full-control.lock'):
        if action in {'stop','restart'}:stop_system(port,progress=progress)
        if action in {'start','restart'}:start_system(port=port)
        return inventory(port)


class Panel:
    def __init__(self,root,port=8000,auto_start=True):
        self.root,self.port=root,port;self.events=queue.Queue();self.busy=False;self.closing=False
        root.title('Saya 与 QQ · 后台管理');root.geometry('760x610');root.minsize(650,500)
        root.protocol('WM_DELETE_WINDOW',self.close)
        outer=ttk.Frame(root,padding=20);outer.pack(fill='both',expand=True)
        ttk.Label(outer,text='Saya 与 QQ 后台管理',font=('Microsoft YaHei UI',18,'bold')).pack(anchor='w')
        ttk.Label(outer,text='关闭此面板 = 停止并核验后台。检查失败时会保留窗口显示原因。',wraplength=690).pack(anchor='w',pady=(6,12))
        self.summary=tk.StringVar(value='正在检查……');ttk.Label(outer,textvariable=self.summary,font=('Microsoft YaHei UI',12,'bold')).pack(anchor='w')
        row=ttk.Frame(outer);row.pack(fill='x',pady=12);self.buttons=[]
        for text,action in [('启动','start'),('停止全部后台','stop'),('重启','restart'),('刷新状态','status')]:
            button=ttk.Button(row,text=text,command=lambda a=action:self.perform(a));button.pack(side='left',padx=(0,8));self.buttons.append(button)
        from saya_qq_memory_panel import open_memory_panel
        ttk.Button(row,text='记忆操作台',command=lambda:open_memory_panel(root,self.port)).pack(side='left',padx=(0,8))
        ttk.Button(row,text='停止并退出',command=self.close).pack(side='right')
        self.tree=ttk.Treeview(outer,columns=('kind','pid'),show='headings',height=9)
        self.tree.heading('kind',text='检测到的本项目进程');self.tree.heading('pid',text='PID（WSL进程单独标注）');self.tree.column('kind',width=420);self.tree.column('pid',width=180)
        self.tree.pack(fill='both',expand=True)
        self.detail=tk.StringVar(value='');ttk.Label(outer,textvariable=self.detail,wraplength=690).pack(anchor='w',pady=8)
        ttk.Label(outer,text='操作结果与检查记录').pack(anchor='w')
        self.log=tk.Text(outer,height=7,wrap='word',state='disabled',font=('Microsoft YaHei UI',10));self.log.pack(fill='both',expand=True,pady=(4,0))
        root.after(100,self.poll);root.after(300,lambda:self.perform('start' if auto_start else 'status'));root.after(6000,self.refresh)

    def append(self,text):
        self.log.configure(state='normal');self.log.insert('end',text+'\n');self.log.see('end');self.log.configure(state='disabled')

    def show(self,state):
        for item in self.tree.get_children():self.tree.delete(item)
        for item in state['processes']:self.tree.insert('','end',values=(item['kind'],item['pid']))
        health=state.get('health',{})
        if state['stopped']:self.summary.set('已全部停止 · 未检测到受管后台残留（仅管理面板仍打开）')
        elif not state['issues'] and all(health.get(k)==v for k,v in {'saya':'ready','consumer':'running','napcat':'reachable','qq_login':'matched'}.items()):self.summary.set('运行正常 · QQ接收已就绪')
        else:self.summary.set('部分运行或需要处理 · 请查看下方进程与提示')
        self.detail.set('检测到进程：'+str(len(state['processes']))+' 个；占用端口：'+(', '.join(map(str,state['ports'])) or '无')+'\n'+('；'.join(state['issues']) or '本项目归属检查完成；日常QQ和其他程序不在关闭范围。'))

    def perform(self,action):
        if self.busy:return
        self.busy=True
        for button in self.buttons:button.configure(state='disabled')
        if action!='status':self.append({'start':'正在启动……','stop':'正在停止并核验……','restart':'正在先停止、再启动……'}[action]);self.summary.set('操作中，请稍候……')
        def work():
            error=None;state=None
            try:state=inventory(self.port) if action=='status' else execute(action,self.port,lambda t:self.events.put(('log',t)))
            except Exception as exc:
                error=str(exc)
                try:state=inventory(self.port)
                except Exception:pass
            self.events.put(('done',(action,state,error)))
        threading.Thread(target=work,daemon=True).start()

    def poll(self):
        try:
            while True:
                kind,value=self.events.get_nowait()
                if kind=='log':self.append(value);continue
                action,state,error=value;self.busy=False
                for button in self.buttons:button.configure(state='normal')
                if state:self.show(state)
                if error:self.append('未完成：'+error)
                elif action!='status':self.append('已完成，并已重新检查进程和端口。')
                if self.closing:
                    if error:self.closing=False;messagebox.showwarning('未能确认全部停止','窗口将保留。\n'+error,parent=self.root)
                    elif state and state['stopped']:self.root.destroy();return
                    else:self.perform('stop')
        except queue.Empty:pass
        self.root.after(100,self.poll)

    def refresh(self):
        if not self.busy and not self.closing:self.perform('status')
        self.root.after(6000,self.refresh)

    def close(self):
        self.closing=True
        if self.busy:self.append('已收到退出请求；当前操作结束后停止并退出。')
        else:self.perform('stop')


def run_panel(port=8000):
    service.DIRECTORY.mkdir(parents=True,exist_ok=True)
    try:
        with service.InstanceLease(service.DIRECTORY/'panel.lock'):
            root=tk.Tk();Panel(root,port);root.mainloop()
    except service.InstanceInUse:
        root=tk.Tk();root.withdraw();messagebox.showinfo('面板已打开','已有管理面板，请从任务栏打开它。');root.destroy()
