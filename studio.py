"""景连 Studio: responsive desktop workspace and guarded async operations."""
import concurrent.futures
import math
import os
import queue
import socket
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path
import customtkinter as ctk
from PIL import Image, ImageTk
from localization import LANGUAGE_OPTIONS, get_locale, set_locale, tr
from core import (normalize_share, normalize_drive, clean_records, import_records,
                  atomic_json, query_mappings, available_drives, AUTO_DRIVE, VERSION,
                  map_with_drive, edit_favorite_records, diagnose_share, local_share_paths)


def make_studio(Base, api):
    save = api['save_data']
    resource = api['resource_path']

    class StudioApp(Base):
        DARK = dict(bg='#0D1518', surface='#111D21', card='#152226', border='#293B40',
                    text='#EAF3F1', muted='#9BAFB1', soft='#203237', accent='#147D70',
                    accent_hover='#1A9482', good='#65D6B0', warning='#E6B66E', input='#101C20', nav='#101B1F')
        LIGHT = dict(bg='#F2F5F3', surface='#FFFFFF', card='#FFFFFF', border='#D9E3DF',
                     text='#20362F', muted='#627A70', soft='#E7EFEB', accent='#147D70',
                     accent_hover='#10685D', good='#147D70', warning='#A86D22', input='#F6F9F7', nav='#F9FBFA')
        NAV = [('概览','01'),('连接共享','02'),('我的连接','03'),('网络发现','04'),('操作记录','05'),('设置','06'),('软件介绍','07')]

        def __init__(self):
            self.events = queue.Queue()
            self.rows = []
            self.refresh_busy = False
            self.connect_busy = False
            self.scan_busy = False
            self.diagnostic_busy = False
            self.scan_cancel = threading.Event()
            self.logs = []
            self.anim_job = None
            self.page_generation = 0
            self.draft = None
            self.search_value = ''
            self.filter_value = '全部'
            super().__init__()
            self.geometry('1240x850')
            self.minsize(1080,740)
            self.after(40, self.drain_events)
            self.bind('<Control-n>', lambda e:self.show_page('连接共享'))
            self.bind('<Control-r>', lambda e:self.refresh_connections())
            self.bind('<Control-f>', lambda e:self.focus_search())
            self.bind('<Escape>', lambda e:self.scan_cancel.set())
            if os.name == 'nt':
                try: api['ctypes'].windll.shell32.SetCurrentProcessExplicitAppUserModelID('JingSMB.Studio.2')
                except OSError: pass

        def post(self, callback):
            self.events.put(callback)

        def drain_events(self):
            if self.force_exit: return
            for _ in range(100):
                try: callback = self.events.get_nowait()
                except queue.Empty: break
                try: callback()
                except Exception as exc:
                    self.log(tr('界面操作'), str(exc), False)
            self.after(40, self.drain_events)

        def _async(self, fn, done):
            def run():
                try: result, error = fn(), None
                except Exception as exc: result, error = None, exc
                self.post(lambda:done(result,error))
            threading.Thread(target=run, daemon=True).start()

        def log(self, title, detail, success=True):
            self.logs.insert(0, dict(time=time.strftime('%H:%M:%S'), title=title,
                                    detail=str(detail), success=success))
            del self.logs[200:]
            if self.page == '操作记录': self.render_log()

        def _make_premium_shell(self):
            super()._make_premium_shell()
            self.sidebar.configure(width=218)
            self.heading.configure(font=ctk.CTkFont(family='Microsoft YaHei UI',size=27,weight='bold'))
            self.button(self.header,tr('收至托盘'),self.hide_to_tray,width=95,height=36).grid(row=0,column=2,rowspan=2,padx=(10,0))
            self.statusbar = ctk.CTkFrame(self.body,height=34,fg_color=self.c('surface'),corner_radius=0)
            self.statusbar.grid(row=2,column=0,sticky='ew')
            self.footer=self.label(self.statusbar,tr('  景连 SMB {0}', VERSION),10,color='muted')
            self.footer.pack(side='left',padx=22,pady=6)
            self.label(self.statusbar,tr('Ctrl+N 新建  ·  Ctrl+R 刷新'),10,color='muted').pack(side='right',padx=22)

        def capture_draft(self):
            if self.page == '连接共享' and hasattr(self,'inputs'):
                try:
                    values = {key: widget.get() for key, widget in self.inputs.items()}
                    values['映射盘符'] = self.drive_choice()
                    self.draft = (values, self.persist.get(), self.remember.get(), self.open_after.get())
                except tk.TclError: pass

        def show_page(self,name):
            self.capture_draft()
            self.page_generation += 1
            self.stop_animation()
            if name == '操作记录':
                self.page=name
                for n,b in self.navbuttons.items():
                    b.configure(fg_color=self.c('soft') if n==name else 'transparent',text_color=self.c('good') if n==name else self.c('muted'))
                for w in self.content.winfo_children(): w.destroy()
                self.heading.configure(text=tr('操作记录'))
                self.subheading.configure(text=tr('本次会话的连接与诊断结果，最多保留 200 条。'))
                self.premium_log()
            else: super().show_page(name)
            self.update_stats()

        def rebuild_shell(self, language=None):
            self.capture_draft()
            if language is not None:
                set_locale(language)
                self.title(tr(api['APP_NAME']))
                if self.tray_icon is not None:
                    try:
                        self.tray_icon.title = tr(api['APP_NAME'])
                        self.tray_icon.update_menu()
                    except OSError:
                        pass
            self.stop_animation()
            self.configure(fg_color=self.c('bg'))
            super().rebuild_shell()

        def stop_animation(self):
            if self.anim_job:
                self.after_cancel(self.anim_job)
                self.anim_job=None

        def _hero(self):
            self.hero=tk.Canvas(self.content,height=245,highlightthickness=0,bg=self.c('card'))
            self.hero.grid(row=0,column=0,sticky='ew',pady=(6,16))
            self.hero.bind('<Configure>',lambda e:self.draw_hero())
            self.draw_hero()

        def draw_hero(self,generation=None):
            self.stop_animation()
            if self.page != '概览' or not self.hero.winfo_exists(): return
            c=self.hero; c.delete('all'); w=max(800,c.winfo_width()); h=245
            dark=self.data['theme']=='dark'
            c.configure(bg=self.c('card'))
            c.create_text(28,36,anchor='w',text='JINGLINK   /   CONNECTED WORKSPACE',fill=self.c('good'),font=('Segoe UI',10,'bold'))
            c.create_text(28,94,anchor='w',text=tr('文件有去处，\n连接无距离。'),fill=self.c('text'),font=('Microsoft YaHei UI',28,'bold'),justify='left')
            c.create_text(30,177,anchor='w',text=tr('连接你的 NAS、电脑与共享空间。'),fill=self.c('muted'),font=('Microsoft YaHei UI',12))
            c.create_text(30,215,anchor='w',text=tr('WINDOWS 原生服务   /   密码不落盘'),fill=self.c('muted'),font=('Microsoft YaHei UI',9))
            x=w-190; y=126; phase=self.anim_step*.025
            nodes=[(x-105,y-55),(x+110,y-48),(x+85,y+65),(x-110,y+64)]
            for i,(nx,ny) in enumerate(nodes):
                c.create_line(x,y,nx,ny,fill=self.c('border'),width=2)
                t=(phase+i*.23)%1
                px=x+(nx-x)*t; py=y+(ny-y)*t
                c.create_oval(px-3,py-3,px+3,py+3,fill=self.c('good'),outline='')
                c.create_rectangle(nx-19,ny-14,nx+19,ny+14,fill=self.c('soft'),outline=self.c('border'),width=2)
                c.create_line(nx-10,ny+5,nx+10,ny+5,fill=self.c('good'),width=2)
            c.create_oval(x-44,y-44,x+44,y+44,fill=self.c('soft'),outline=self.c('border'),width=2)
            if not hasattr(self,'hero_logo'):
                self.hero_logo=ImageTk.PhotoImage(Image.open(resource('assets/app.png')).resize((64,64)))
            c.create_image(x,y,image=self.hero_logo)
            self.anim_step+=1
            if self.data.get('motion',True) and self.state()=='normal':
                self.anim_job=self.after(50,self.draw_hero)

        def entry(self,parent,placeholder='',width=None):
            return ctk.CTkEntry(parent,height=42,width=width or 200,corner_radius=10,
                               placeholder_text=placeholder,fg_color=self.c('input'),
                               border_color=self.c('border'),text_color=self.c('text'))

        def premium_connect(self):
            f=self.panel(); f.grid(row=0,column=0,sticky='ew',pady=(6,12)); f.grid_columnconfigure(0,weight=1)
            self.label(f,tr('01   共享位置'),15,'bold').grid(row=0,column=0,sticky='w',padx=24,pady=(22,12))
            self.label(f,tr('共享路径'),12,color='muted').grid(row=1,column=0,sticky='w',padx=24)
            self.inputs={}
            path_row = ctk.CTkFrame(f, fg_color='transparent')
            path_row.grid(row=2, column=0, sticky='ew', padx=24, pady=(5, 12))
            self.inputs['共享路径'] = self.entry(path_row, r'\\192.168.1.10\Public')
            self.inputs['共享路径'].pack(side='left', fill='x', expand=True, padx=(0, 8))
            self.button(path_row, tr('粘贴地址'), self.paste_share, width=84).pack(side='left')
            row=ctk.CTkFrame(f,fg_color='transparent'); row.grid(row=3,column=0,sticky='ew',padx=24,pady=(0,18))
            self.label(row,tr('映射盘符'),12,color='muted').pack(side='left',padx=(0,12))
            choices=available_drives()
            self.inputs['映射盘符']=ctk.CTkComboBox(row,values=[tr(AUTO_DRIVE), *choices],width=121,height=38,fg_color=self.c('input'),text_color=self.c('text'),border_color=self.c('border'),button_color=self.c('accent'))
            self.inputs['映射盘符'].set(tr(AUTO_DRIVE)); self.inputs['映射盘符'].pack(side='left')
            self.label(row,tr('连接时可自动挑选空闲盘符'),11,color='muted').pack(side='left',padx=14)
            self.label(f,tr('02   身份验证'),15,'bold').grid(row=4,column=0,sticky='w',padx=24,pady=(2,12))
            cred=ctk.CTkFrame(f,fg_color='transparent'); cred.grid(row=5,column=0,sticky='ew',padx=24); cred.grid_columnconfigure((0,1),weight=1)
            for i,(name,hint) in enumerate([('用户名',tr(r'可选：DOMAIN\user')),('密码',tr('使用当前 Windows 身份时留空'))]):
                self.label(cred,tr(name),12,color='muted').grid(row=0,column=i,sticky='w')
                e=self.entry(cred,hint); e.grid(row=1,column=i,sticky='ew',padx=(0,12) if i==0 else 0,pady=(5,12)); self.inputs[name]=e
                if name=='密码': e.configure(show='●')
            self.reveal=ctk.BooleanVar(value=False)
            ctk.CTkCheckBox(f,text=tr('显示密码'),variable=self.reveal,command=lambda:self.inputs['密码'].configure(show='' if self.reveal.get() else '●'),text_color=self.c('muted'),checkbox_width=18,checkbox_height=18).grid(row=6,column=0,sticky='w',padx=24,pady=(0,12))
            opts=ctk.CTkFrame(f,fg_color='transparent'); opts.grid(row=7,column=0,sticky='ew',padx=24,pady=8)
            self.persist=ctk.CTkCheckBox(opts,text=tr('登录时恢复映射'),text_color=self.c('text'),fg_color=self.c('accent')); self.persist.select(); self.persist.pack(side='left',padx=(0,24))
            self.remember=ctk.CTkCheckBox(opts,text=tr('收藏此共享'),text_color=self.c('text'),fg_color=self.c('accent')); self.remember.pack(side='left', padx=(0, 24))
            self.open_after = ctk.CTkCheckBox(opts, text=tr('连接后打开'), text_color=self.c('text'),
                                               fg_color=self.c('accent'))
            if self.data.get('open_after_connect', False):
                self.open_after.select()
            self.open_after.pack(side='left')
            actions=ctk.CTkFrame(f,fg_color='transparent'); actions.grid(row=8,column=0,sticky='ew',padx=20,pady=(16,24))
            self.connect_btn=self.button(actions,tr('正在连接…') if self.connect_busy else tr('连接共享  →'),self.connect,True,width=155); self.connect_btn.pack(side='left',padx=4)
            if self.connect_busy:self.connect_btn.configure(state='disabled')
            self.button(actions,tr('测试可达性'),self.test_connection,width=120).pack(side='left',padx=5)
            self.button(actions,tr('打开网络位置'),self.open_unc,width=153).pack(side='left',padx=5)
            self.button(actions, tr('保存为收藏'), self.favorite_from_form, width=110).pack(side='left', padx=5)
            note=self.panel(); note.grid(row=1,column=0,sticky='ew')
            self.label(note,tr('连接小贴士'),13,'bold').pack(anchor='w',padx=22,pady=(16,4))
            self.label(note,tr('Windows 负责 SMB 认证与映射。若提示 1219，请先断开同一服务器上使用其他账号的连接。'),11,color='muted',wraplength=730,justify='left').pack(anchor='w',padx=22,pady=(0,18))
            if self.draft:
                values,persist,remember,open_after=self.draft
                for k,v in values.items():
                    e=self.inputs[k]
                    if isinstance(e,ctk.CTkComboBox):e.set(tr(AUTO_DRIVE) if v == AUTO_DRIVE else v)
                    else:e.insert(0,v)
                self.persist.select() if persist else self.persist.deselect()
                self.remember.select() if remember else self.remember.deselect()
                self.open_after.select() if open_after else self.open_after.deselect()

        def paste_share(self):
            try:
                path = normalize_share(self.clipboard_get())
            except tk.TclError:
                messagebox.showwarning(tr('剪贴板为空'), tr('请先复制共享路径。'), parent=self)
                return
            except ValueError as error:
                messagebox.showwarning(tr('地址无效'), str(error), parent=self)
                return
            entry = self.inputs['共享路径']
            entry.delete(0, 'end')
            entry.insert(0, path)
            self.footer.configure(text=tr('共享地址已粘贴'))

        def favorite_from_form(self):
            try:
                path = normalize_share(self.inputs['共享路径'].get())
            except ValueError as error:
                messagebox.showerror(tr('地址无效'), str(error), parent=self)
                return
            existing = next((item for item in self.data['favorites']
                             if item['path'].casefold() == path.casefold()), None)
            drive = self.drive_choice()
            defaults = dict(path=path, drive='Z:' if drive == AUTO_DRIVE else drive, name='')
            self.edit_favorite(existing, defaults)

        def _form(self):
            drive = self.drive_choice()
            if drive != AUTO_DRIVE:
                drive = normalize_drive(drive)
            return (normalize_share(self.inputs['共享路径'].get()), drive,
                    self.inputs['用户名'].get().strip(), self.inputs['密码'].get())

        def drive_choice(self):
            value = self.inputs['映射盘符'].get()
            return AUTO_DRIVE if value in (AUTO_DRIVE, tr(AUTO_DRIVE)) else value

        def connect(self):
            if self.connect_busy:
                return
            try:
                remote, requested_drive, user, password = self._form()
            except ValueError as error:
                messagebox.showerror(tr('检查连接信息'), str(error), parent=self)
                return
            submitted_path = self.inputs['共享路径'].get()
            persistent, remember = bool(self.persist.get()), bool(self.remember.get())
            open_after = bool(self.open_after.get())
            self.connect_busy = True
            self.connect_btn.configure(state='disabled', text=tr('正在连接…'))
            self.footer.configure(text=tr('正在连接 {0} …', remote))

            def done(drive, error):
                self.connect_busy = False
                if self.page == '连接共享':
                    self.connect_btn.configure(state='normal', text=tr('连接共享  →'))
                if error:
                    self.log(tr('连接失败'), f'{remote} · {error}', False)
                    messagebox.showerror(tr('连接失败'), str(error), parent=self)
                    return
                record = dict(path=remote, drive=drive, name='')
                self.data['recent'] = [record] + [item for item in self.data['recent']
                                                 if item['path'].casefold() != remote.casefold()][:11]
                self.data['open_after_connect'] = open_after
                try:
                    if remember and not any(item['path'].casefold() == remote.casefold()
                                            for item in self.data['favorites']):
                        self.data['favorites'] = edit_favorite_records(self.data['favorites'], record)
                    save(self.data)
                except (OSError, ValueError) as exc:
                    messagebox.showwarning(tr('映射成功，但配置未保存'), str(exc), parent=self)
                if self.draft and self.draft[0]['共享路径'] == submitted_path and self.draft[0]['密码'] == password:
                    self.draft[0]['密码'] = ''
                if (self.page == '连接共享' and self.inputs['密码'].get() == password
                        and self.inputs['共享路径'].get() == submitted_path):
                    self.inputs['密码'].delete(0, 'end')
                if self.page == '我的连接':
                    self.render_connections()
                self.log(tr('连接成功'), f'{drive} → {remote}')
                self.refresh_connections()
                self.footer.configure(text=tr('已连接  {0} → {1}', drive, remote))
                if open_after:
                    self.open_location(drive)

            def work():
                return map_with_drive(api['map_share'], remote, requested_drive, user, password, persistent)

            self._async(work, done)

        def test_connection(self):
            if self.diagnostic_busy:
                return
            try:
                remote = normalize_share(self.inputs['共享路径'].get())
            except ValueError as error:
                messagebox.showerror(tr('地址无效'), str(error), parent=self)
                return
            self.diagnostic_busy = True
            self.footer.configure(text=tr('正在解析共享设备并检测 TCP 445 …'))

            def done(result, error):
                self.diagnostic_busy = False
                reachable, report = result if not error else (False, str(error))
                self.log(tr('连通性检测'), report, reachable)
                heading = tr('TCP 445 可达') if reachable else tr('未能连接 TCP 445')
                self.footer.configure(text=heading)
                dialog = self.popup(tr('连接诊断'), '625x350')
                self.label(dialog, heading, 20, 'bold').pack(anchor='w', padx=22, pady=(20, 8))
                area = ctk.CTkTextbox(dialog, wrap='word')
                area.pack(fill='both', expand=True, padx=22)
                area.insert('1.0', report)
                area.configure(state='disabled')
                bar = ctk.CTkFrame(dialog, fg_color='transparent')
                bar.pack(fill='x', padx=22, pady=14)
                self.button(bar, tr('复制诊断'), lambda: self.copy_text(report, tr('诊断已复制')),
                            primary=True, width=126).pack(side='left')
                self.button(bar, tr('关闭'), dialog.destroy, width=80).pack(side='right')

            self._async(lambda: diagnose_share(remote), done)

        def open_unc(self):
            try:
                path=normalize_share(self.inputs['共享路径'].get());self.open_location(path)
            except ValueError as exc:messagebox.showerror(tr('地址无效'),str(exc),parent=self)

        def open_location(self,path):
            try:os.startfile(path+'\\' if len(path)==2 and path.endswith(':') else path)
            except Exception as exc:messagebox.showerror(tr('打开失败'),str(exc),parent=self)

        def favorite_connect(self,item):
            self.draft=None
            if self.page=='连接共享':self.page='我的连接'
            self.show_page('连接共享')
            self.inputs['共享路径'].insert(0, item.get('path', ''))
            drive = item.get('drive')
            self.inputs['映射盘符'].set(drive if drive in available_drives() else tr(AUTO_DRIVE))

        def use_host(self,host):
            self.favorite_connect({'path':f'\\\\{host}\\','drive':AUTO_DRIVE})

        def focus_search(self):
            self.show_page('我的连接');self.search.focus_set()

        def premium_connections(self):
            bar=ctk.CTkFrame(self.content,fg_color='transparent');bar.grid(row=0,column=0,sticky='ew',pady=(6,12))
            self.search_var=tk.StringVar(value=self.search_value)
            self.search=self.entry(bar,tr('搜索名称、地址或盘符'));self.search.configure(textvariable=self.search_var);self.search.pack(side='left',fill='x',expand=True,padx=(0,10))
            self.search_var.trace_add('write',lambda *_:self.search_changed())
            self.button(bar,tr('刷新'),self.refresh_connections,width=70).pack(side='left',padx=4)
            self.button(bar,tr('添加共享'),lambda:self.show_page('连接共享'),True,width=110).pack(side='left',padx=4)
            self.button(bar, tr('新建收藏'), lambda: self.edit_favorite(None), width=95).pack(side='left', padx=4)
            filter_labels = {tr(key): key for key in ('全部', '当前映射', '收藏', '最近连接')}
            filters=ctk.CTkSegmentedButton(self.content,values=list(filter_labels),command=lambda label: self.set_filter(filter_labels[label]),selected_color=self.c('accent'),unselected_color=self.c('soft'),text_color=self.c('text'));filters.set(tr(self.filter_value));filters.grid(row=1,column=0,sticky='w',pady=(0,12))
            self.connlist=self.panel();self.connlist.grid(row=2,column=0,sticky='ew');self.render_connections()

        def set_filter(self,value):self.filter_value=value;self.render_connections()
        def search_changed(self):self.search_value=self.search_var.get();self.render_connections()

        def render_connections(self):
            if self.page!='我的连接' or not hasattr(self,'connlist') or not self.connlist.winfo_exists():return
            for w in self.connlist.winfo_children():w.destroy()
            query = self.search_value.casefold()
            sections = []
            if self.filter_value in ('全部', '当前映射'):
                sections.append((tr('当前映射'), [dict(drive=d, path=p, name=tr(status)) for d,p,status in self.rows], 'mapped'))
            if self.filter_value in ('全部', '收藏'):
                sections.append((tr('收藏的共享'), self.data['favorites'], 'favorite'))
            if self.filter_value == '最近连接':
                sections.append((tr('最近连接'), self.data['recent'], 'recent'))
            for title, items, kind in sections:
                self.label(self.connlist,tr(title),14,'bold').pack(anchor='w',padx=20,pady=(18,8))
                visible=[x for x in items if query in ' '.join(str(v) for v in x.values()).casefold()]
                if not visible:self.label(self.connlist,tr('没有匹配的共享') if query else tr('暂无记录'),12,color='muted').pack(anchor='w',padx=20,pady=(0,15))
                for item in visible:
                    row=ctk.CTkFrame(self.connlist,fg_color=self.c('soft'),corner_radius=12);row.pack(fill='x',padx=12,pady=(0,8))
                    self.label(row,item['drive'],18,'bold',color='good',width=55).pack(side='left',padx=10,pady=15)
                    text=ctk.CTkFrame(row,fg_color='transparent');text.pack(side='left',fill='x',expand=True)
                    self.label(text,item.get('name') or item['path'].split('\\')[-1],12,'bold',anchor='w').pack(anchor='w')
                    self.label(text,item['path'],10,color='muted',anchor='w',wraplength=340,justify='left').pack(anchor='w')
                    if kind == 'mapped':
                        self.button(row,tr('断开'),lambda x=item:self.disconnect(x['drive']),width=83,height=32).pack(side='right',padx=5)
                        self.button(row,tr('打开'),lambda x=item:self.open_location(x['drive']),True,width=60,height=32).pack(side='right',padx=3)
                    else:
                        if kind == 'favorite':
                            self.button(row,tr('编辑'),lambda x=item:self.edit_favorite(x),width=60,height=32).pack(side='right',padx=5)
                        self.button(row,tr('连接'),lambda x=item:self.favorite_connect(x),True,width=60,height=32).pack(side='right',padx=3)
                    self.button(row,tr('复制'),lambda x=item:self.copy_path(x['path']),width=60,height=32).pack(side='right',padx=3)

        def copy_text(self, value, status):
            self.clipboard_clear()
            self.clipboard_append(value)
            self.footer.configure(text=status)

        def copy_path(self, path):
            self.copy_text(path, tr('共享地址已复制'))

        def popup(self, title, geometry):
            dialog = ctk.CTkToplevel(self)
            dialog.title(title)
            dialog.geometry(geometry)
            dialog.transient(self)
            dialog.grab_set()
            dialog.configure(fg_color=self.c('bg'))
            return dialog

        def edit_favorite(self, item, defaults=None):
            editing = item is not None
            dialog = self.popup(tr('编辑收藏') if editing else tr('新建收藏'), '480x365')
            dialog.resizable(False, False)
            self.label(dialog, tr('编辑共享收藏') if editing else tr('新建共享收藏'), 20, 'bold').pack(
                anchor='w', padx=24, pady=(20, 10))
            values = item if editing else (defaults or {'drive': 'Z:'})
            fields = {}
            for key, hint in [('name', tr('自定义名称')), ('path', tr('共享路径')), ('drive', tr('优先盘符'))]:
                entry = self.entry(dialog, hint)
                entry.pack(fill='x', padx=24, pady=5)
                if values.get(key):
                    entry.insert(0, values[key])
                fields[key] = entry

            def commit():
                try:
                    new = edit_favorite_records(self.data['favorites'],
                                                {key: entry.get() for key, entry in fields.items()}, item)
                    atomic_json(api['DATA_FILE'], dict(self.data, favorites=new))
                except (ValueError, OSError) as error:
                    messagebox.showerror(tr('无法保存'), str(error), parent=dialog)
                    return
                self.data['favorites'] = new
                dialog.destroy()
                self.render_connections()
                self.update_stats()
                self.footer.configure(text=tr('收藏已保存'))

            bar = ctk.CTkFrame(dialog, fg_color='transparent')
            bar.pack(fill='x', padx=24, pady=16)
            if editing:
                self.button(bar, tr('移除收藏'), lambda: self.delete_favorite(item, dialog), width=106).pack(side='left')
            self.button(bar, tr('保存收藏'), commit, True, width=106).pack(side='right')

        def delete_favorite(self,item,dialog):
            if messagebox.askyesno(tr('移除收藏'),tr('仅移除这条收藏，不会断开网络映射。'),parent=dialog):
                key = item['path'].casefold()
                new = [record for record in self.data['favorites'] if record['path'].casefold() != key]
                try:atomic_json(api['DATA_FILE'],dict(self.data,favorites=new))
                except OSError as exc:messagebox.showerror(tr('无法保存'),str(exc),parent=dialog);return
                self.data['favorites']=new;dialog.destroy();self.render_connections();self.update_stats()

        def disconnect(self,target):
            if not messagebox.askyesno(tr('断开网络磁盘'),tr('确定断开 {0}？请先保存此磁盘中打开的文件。', target),parent=self):return
            def done(_,err):
                if err:self.log(tr('断开失败'),str(err),False);messagebox.showerror(tr('断开失败'),str(err),parent=self)
                else:self.log(tr('已断开'),target);self.refresh_connections()
            self._async(lambda:api['unmap'](target,False),done)

        def refresh_connections(self):
            if self.refresh_busy:return
            self.refresh_busy=True
            def done(rows,err):
                self.refresh_busy=False
                if err:self.footer.configure(text=tr('读取映射失败：{0}', err));return
                self.rows=rows;self.update_stats();self.render_connections()
            self._async(query_mappings,done)

        def update_stats(self):
            if not hasattr(self,'side_status'):return
            self.side_status.configure(text=tr('●  {0} 个网络映射', len(self.rows)),text_color=self.c('good') if self.rows else self.c('muted'))
            for key,val in [('stat_mapped',len(self.rows)),('stat_favs',len(self.data.get('favorites',[])))]:
                widget=getattr(self,key,None)
                if widget is not None and widget.winfo_exists():widget.configure(text=str(val))

        def premium_discover(self):
            super().premium_discover()
            f=self.subnet.master
            self.button(f,tr('停止'),self.scan_cancel.set,width=70).pack(side='left',padx=4)
            self.progress=ctk.CTkProgressBar(self.scanresults,progress_color=self.c('accent'),height=5);self.progress.pack(fill='x',padx=18,pady=(0,15));self.progress.set(0)
            if os.name == 'nt':
                self.button(f, tr('查看本机共享'), self.show_local_shares, width=120).pack(side='left', padx=4)
            if self.scan_busy:self.scanstatus.configure(text=tr('扫描正在后台进行，按 Esc 可停止。'))
            elif hasattr(self,'last_scan'):self.render_scan(*self.last_scan)

        def show_local_shares(self):
            dialog = self.popup(tr('本机 Windows 共享'), '660x425')
            self.label(dialog, tr('本机 Windows 共享'), 20, 'bold').pack(anchor='w', padx=22, pady=(20, 6))
            self.label(dialog, tr('将路径复制给需要访问此电脑的用户。访问权限由 Windows 管理。'),
                       11, color='muted').pack(anchor='w', padx=22, pady=(0, 10))
            listing = ctk.CTkScrollableFrame(dialog, fg_color=self.c('card'))
            listing.pack(fill='both', expand=True, padx=22, pady=(0, 18))
            status = self.label(listing, tr('正在读取本机共享…'))
            status.pack(anchor='w', padx=10, pady=8)

            def done(paths, error):
                if not dialog.winfo_exists():
                    return
                status.destroy()
                if error or not paths:
                    self.label(listing, str(error) if error else tr('没有可见的文件夹共享。'),
                               wraplength=475).pack(anchor='w', padx=10, pady=8)
                    return
                for unc in paths:
                    row = ctk.CTkFrame(listing, fg_color='transparent')
                    row.pack(fill='x', pady=5)
                    self.label(row, unc, wraplength=425, justify='left').pack(side='left', padx=8)
                    self.button(row, tr('复制路径'), lambda path=unc: self.copy_path(path),
                                width=85).pack(side='right', padx=8)

            self._async(local_share_paths, done)

        def scan(self):
            if self.scan_busy:return
            prefix=self.subnet.get().strip()
            if not prefix:
                try:prefix='.'.join(socket.gethostbyname(socket.gethostname()).split('.')[:3])
                except OSError:prefix=''
            bits=prefix.split('.')
            if len(bits)!=3 or any(not b.isascii() or not b.isdigit() or int(b)>255 for b in bits):messagebox.showerror(tr('网段无效'),tr('请输入 IPv4 前三段，例如 192.168.1'),parent=self);return
            prefix='.'.join(str(int(b)) for b in bits)
            self.scan_busy=True;self.scan_cancel.clear();self.scanstatus.configure(text=tr('正在扫描 {0}.0/24 …', prefix))
            self.scan_prefix=prefix
            def work():
                found=[];completed=0
                def check(i):
                    if self.scan_cancel.is_set():return None
                    host=f'{prefix}.{i}'
                    return host if api['probe'](host,0.65) else None
                with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
                    futures=[pool.submit(check,i) for i in range(1,255)]
                    for future in concurrent.futures.as_completed(futures):
                        host=future.result();completed+=1
                        if host:found.append(host)
                        if completed%8==0:self.post(lambda n=completed:self.scan_progress(n))
                return sorted(found,key=lambda h:int(h.split('.')[-1])),self.scan_cancel.is_set()
            self._async(work,self.scan_done)

        def scan_progress(self,n):
            if self.page=='网络发现' and self.scan_busy:
                self.scanstatus.configure(text=tr('{0}.0/24  ·  已检测 {1}/254', self.scan_prefix, n))
                if self.progress.winfo_exists():self.progress.set(n/254)

        def scan_done(self,result,error):
            self.scan_busy=False
            if error:self.log(tr('扫描失败'),str(error),False);self.footer.configure(text=tr('扫描失败：{0}', error));return
            hosts,cancelled=result;self.last_scan=(hosts,cancelled)
            self.log(tr('扫描已停止') if cancelled else tr('扫描完成'),tr('{0}.0/24 · {1} 台设备', self.scan_prefix, len(hosts)))
            if self.page=='网络发现':self.render_scan(hosts,cancelled)

        def render_scan(self,hosts,cancelled):
            super().scan_done(hosts,None)
            self.scanstatus.configure(text=(tr('已停止 · 以下为部分结果') if cancelled else tr('扫描完成'))+tr(' · {0} 台设备', len(hosts)))
            if not hosts:self.label(self.scanresults,tr('未发现开放 TCP 445 的设备。检查网段、VPN 和设备防火墙后重试。'),12,color='muted',wraplength=720).pack(padx=20,pady=24)

        def select_language(self, label):
            requested = LANGUAGE_OPTIONS[label]
            current = get_locale()
            if requested == current:
                return
            try:
                save(dict(self.data, language=requested))
            except OSError as error:
                self.language_menu.set(next(name for name, code in LANGUAGE_OPTIONS.items() if code == current))
                messagebox.showerror(tr('无法保存设置'), str(error), parent=self)
                return
            self.data['language'] = requested
            self.rebuild_shell(language=requested)
            self.footer.configure(text=tr('语言已切换'))

        def premium_settings(self):
            card = self.panel()
            card.grid(row=0, column=0, sticky='ew', pady=(0, 12))
            self.label(card, tr('界面语言'), 15, 'bold').pack(anchor='w', padx=20, pady=(15, 6))
            self.language_menu = ctk.CTkSegmentedButton(
                card, values=list(LANGUAGE_OPTIONS), command=self.select_language,
                selected_color=self.c('accent'), text_color=self.c('text'),
            )
            self.language_menu.set(next(name for name, code in LANGUAGE_OPTIONS.items() if code == get_locale()))
            self.language_menu.pack(anchor='w', padx=20, pady=(0, 15))
            super().premium_settings(row_offset=1)
            f=self.panel();f.grid(row=4,column=0,sticky='ew',pady=7)
            self.label(f,tr('动态效果'),15,'bold').pack(anchor='w',padx=20,pady=(18,10))
            self.motion_var=ctk.BooleanVar(value=self.data.get('motion',True))
            ctk.CTkSwitch(f,text=tr('播放概览网络流动动画'),variable=self.motion_var,command=self.save_motion,progress_color=self.c('accent'),text_color=self.c('text')).pack(anchor='w',padx=20,pady=(0,18))
            backup=self.panel();backup.grid(row=5,column=0,sticky='ew',pady=7)
            self.label(backup,tr('配置与数据'),15,'bold').pack(anchor='w',padx=20,pady=(18,5))
            self.label(backup,tr('导入会合并收藏；导出仅包含共享地址、盘符和名称，不包含密码。'),11,color='muted',wraplength=680,justify='left').pack(anchor='w',padx=20)
            bar=ctk.CTkFrame(backup,fg_color='transparent');bar.pack(fill='x',padx=16,pady=16)
            self.button(bar,tr('导入收藏'),self.import_config,width=110).pack(side='left',padx=4)
            self.button(bar,tr('导出收藏'),self.export_config,width=110).pack(side='left',padx=4)
            self.button(bar,tr('清空最近记录'),self.clear_recent,width=130).pack(side='left',padx=4)

        def save_motion(self):self.data['motion']=bool(self.motion_var.get());save(self.data)
        def export_config(self):
            path=filedialog.asksaveasfilename(parent=self,title=tr('导出收藏'),defaultextension='.json',initialfile='JingLink-favorites.json',filetypes=[(tr('JSON 配置'),'*.json')])
            if not path:return
            try:atomic_json(path,{'schema':1,'favorites':clean_records(self.data.get('favorites',[]))});self.log(tr('导出收藏'),tr('{0} 条收藏', len(self.data.get('favorites', []))));self.footer.configure(text=tr('收藏已导出'))
            except (OSError,ValueError) as exc:messagebox.showerror(tr('导出失败'),str(exc),parent=self)
        def import_config(self):
            path=filedialog.askopenfilename(parent=self,title=tr('导入收藏'),filetypes=[(tr('JSON 配置'),'*.json')])
            if not path:return
            try:
                records=import_records(path);merged=clean_records(self.data.get('favorites',[])+records)
                atomic_json(api['DATA_FILE'],dict(self.data,favorites=merged));self.data['favorites']=merged
                self.log(tr('导入收藏'),tr('合并后共 {0} 条收藏', len(merged)));self.update_stats();self.footer.configure(text=tr('导入完成 · 共 {0} 条收藏', len(merged)))
            except (OSError,ValueError) as exc:messagebox.showerror(tr('导入失败'),str(exc),parent=self)
        def clear_recent(self):
            if messagebox.askyesno(tr('清空最近记录'),tr('清空最近连接记录？收藏和映射不受影响。'),parent=self):
                self.data['recent']=[];save(self.data);self.footer.configure(text=tr('最近记录已清空'))

        def premium_log(self):
            bar=ctk.CTkFrame(self.content,fg_color='transparent');bar.grid(row=0,column=0,sticky='ew',pady=8)
            self.button(bar,tr('导出记录'),self.export_logs,width=110).pack(side='left',padx=(0,8))
            self.button(bar,tr('清空记录'),self.clear_logs,width=110).pack(side='left')
            self.loglist=self.panel();self.loglist.grid(row=1,column=0,sticky='ew');self.render_log()
        def clear_logs(self):self.logs=[];self.render_log()
        def render_log(self):
            if not hasattr(self,'loglist') or not self.loglist.winfo_exists():return
            for w in self.loglist.winfo_children():w.destroy()
            if not self.logs:self.label(self.loglist,tr('暂无操作记录。连接、测试或扫描后会在此显示结果。'),12,color='muted').pack(padx=24,pady=36)
            for item in self.logs:
                row=ctk.CTkFrame(self.loglist,fg_color=self.c('soft'),corner_radius=10);row.pack(fill='x',padx=12,pady=5)
                self.label(row,f'{item["time"]}   {item["title"]}',12,'bold',color='good' if item['success'] else 'warning').pack(anchor='w',padx=14,pady=(10,2))
                self.label(row,item['detail'],11,color='muted',wraplength=730,justify='left').pack(anchor='w',padx=14,pady=(0,10))
        def export_logs(self):
            path=filedialog.asksaveasfilename(parent=self,defaultextension='.txt',initialfile='JingLink-session.txt',filetypes=[(tr('文本'),'*.txt')])
            if not path:return
            try:Path(path).write_text('\n'.join(f'{x["time"]} {x["title"]}  {x["detail"]}' for x in self.logs),encoding='utf-8');self.footer.configure(text=tr('操作记录已导出'))
            except OSError as exc:messagebox.showerror(tr('导出失败'),str(exc),parent=self)

        def is_startup_enabled(self):
            if api['winreg'] is None:return False
            return super().is_startup_enabled()
        def toggle_startup(self):
            if api['winreg'] is None:
                self.start_var.set(False);messagebox.showinfo(tr('仅 Windows 支持'),tr('开机启动需要在 Windows 中使用。'),parent=self);return
            super().toggle_startup()

        def start_tray(self):
            if os.name!='nt':return
            if self.tray_icon is not None:return
            pystray=api['pystray']
            try:
                menu=pystray.Menu(pystray.MenuItem(lambda _: tr('打开景连'),lambda *_:self.post(self.show_window),default=True),pystray.MenuItem(lambda _: tr('连接共享'),lambda *_:self.post(lambda:self.open_page('连接共享'))),pystray.MenuItem(lambda _: tr('刷新映射'),lambda *_:self.post(self.refresh_connections)),pystray.Menu.SEPARATOR,pystray.MenuItem(lambda _: tr('退出'),lambda *_:self.post(self.exit_app)))
                icon=pystray.Icon('JingSMB',Image.open(resource('assets/app.png')),tr(api['APP_NAME']),menu)
                self.tray_icon=icon
                def ready(icon):
                    icon.visible=True
                    self.post(lambda:self.withdraw() if self.start_hidden and self.data.get('start_hidden',False) else None)
                def worker():
                    try:icon.run(setup=ready)
                    except Exception:self.post(self.tray_failed)
                threading.Thread(target=worker,daemon=True).start()
            except Exception:self.tray_failed()
        def hide_to_tray(self):
            if self.tray_icon is not None and self.tray_icon.visible:self.withdraw();self.stop_animation()
            else:self.footer.configure(text=tr('托盘尚未就绪，请稍后重试。'))
        def on_close(self):
            if self.data.get('tray',True) and self.tray_icon is not None and self.tray_icon.visible:self.hide_to_tray()
            else:self.exit_app()
        def show_window(self):
            super().show_window()
            if self.page=='概览':self.draw_hero()
        def exit_app(self):
            self.scan_cancel.set();self.stop_animation();super().exit_app()

    return StudioApp
