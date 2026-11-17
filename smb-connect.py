"""景连 SMB 局域网共享连接器。Windows 原生 SMB 映射前端。"""
from __future__ import annotations
import concurrent.futures, ctypes, json, os, socket, subprocess, sys, tempfile, threading, time, webbrowser
from ctypes import wintypes
from pathlib import Path
import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
from PIL import Image
import pystray
from localization import set_locale, tr
try:
    import winreg
except ImportError:
    winreg = None

APP_NAME = "景连 SMB"
GITHUB_URL = "https://github.com/jingpanlong/JingLink-SMB"
APP_DIR = Path(os.getenv("APPDATA", str(Path.home()))) / "JingSMB"
DATA_FILE = APP_DIR / "connections.json"
from core import VERSION
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

class NETRESOURCE(ctypes.Structure):
    _fields_ = [("dwScope", wintypes.DWORD), ("dwType", wintypes.DWORD), ("dwDisplayType", wintypes.DWORD), ("dwUsage", wintypes.DWORD), ("lpLocalName", wintypes.LPWSTR), ("lpRemoteName", wintypes.LPWSTR), ("lpComment", wintypes.LPWSTR), ("lpProvider", wintypes.LPWSTR)]

MPR = ctypes.WinDLL("mpr") if os.name == "nt" else None
if MPR:
    MPR.WNetAddConnection2W.argtypes = [ctypes.POINTER(NETRESOURCE), wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    MPR.WNetAddConnection2W.restype = wintypes.DWORD
    MPR.WNetCancelConnection2W.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.BOOL]
    MPR.WNetCancelConnection2W.restype = wintypes.DWORD
    MPR.WNetGetLastErrorW.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD, wintypes.LPWSTR, wintypes.DWORD]

ERRORS = {5:"访问被拒绝，请检查账号、密码和共享权限。", 53:"找不到网络路径，请确认设备在线且共享路径正确。", 67:"找不到共享名称，请检查共享文件夹名称。", 85:"此盘符已被占用，请选择其他盘符。", 1202:"此盘符已有记住的连接，请选择其他盘符。", 86:"网络密码错误。", 1200:"设备名称无效。", 1219:"同一设备已有另一组凭据连接，请先断开旧连接。", 1326:"用户名或密码错误。", 2250:"当前没有该网络连接。"}

def win_error(code: int) -> str:
    msg = ERRORS.get(code)
    if msg: return tr(msg)
    buf = ctypes.create_unicode_buffer(512); provider = ctypes.create_unicode_buffer(512); n = wintypes.DWORD()
    try:
        if MPR and MPR.WNetGetLastErrorW(ctypes.byref(n), buf, 512, provider, 512) == 0 and buf.value: return tr('{0}（错误码 {1}）', buf.value, code)
    except Exception: pass
    return tr('Windows 网络错误：{0}', code)

class MappingError(RuntimeError):
    def __init__(self, code):
        super().__init__(win_error(code))
        self.winerror = code


def map_share(remote: str, drive: str, user: str, password: str, persistent: bool=True):
    nr = NETRESOURCE(); nr.dwType = 1; nr.lpLocalName = drive; nr.lpRemoteName = remote
    flags = 1 if persistent else 0
    code = MPR.WNetAddConnection2W(ctypes.byref(nr), password or None, user or None, flags)
    if code: raise MappingError(code)

def unmap(target: str, force: bool=False):
    code = MPR.WNetCancelConnection2W(target, 1, force)
    if code: raise RuntimeError(win_error(code))

def mapped_drives():
    try:
        p = subprocess.run(["net", "use"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8, creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        rows=[]
        for line in p.stdout.splitlines():
            parts=line.split()
            if len(parts)>=3 and (parts[1].endswith(":") or parts[1].startswith("\\\\")):
                rows.append((parts[1], parts[2], " ".join(parts[3:])))
        return rows
    except Exception: return []

def probe(host: str, timeout: float=1.0) -> bool:
    try:
        with socket.create_connection((host,445),timeout=timeout): return True
    except OSError: return False

def load_data():
    global APP_DIR, DATA_FILE
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        probe_file = APP_DIR / ".write-check"
        probe_file.write_text("", encoding="utf-8"); probe_file.unlink()
    except OSError:
        APP_DIR = Path(tempfile.gettempdir()) / "JingSMB"
        APP_DIR.mkdir(parents=True, exist_ok=True)
        DATA_FILE = APP_DIR / "connections.json"
    try:
        d=json.loads(DATA_FILE.read_text("utf-8"))
        if not isinstance(d,dict): return {"favorites":[]}
        for key in ("favorites","recent"):
            try:d[key]=clean_records(d.get(key,[]))
            except (ValueError,TypeError):d[key]=[]
        if d.get("theme") not in ("dark","light"):d["theme"]="dark"
        return d
    except Exception: return {"favorites":[]}

def save_data(d):
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True); DATA_FILE.write_text(json.dumps(d,ensure_ascii=False,indent=2),"utf-8")
    except OSError as e:
        messagebox.showerror(tr("无法保存设置"), tr('常用连接配置无法写入：{0}', e))

def resource_path(relative):
    base=Path(getattr(sys,"_MEIPASS",Path(__file__).resolve().parent))
    return base/relative

class PremiumApp(ctk.CTk):
    """Modern themed shell, tray, startup control and animated overview."""
    DARK={"bg":"#0B1020","surface":"#111A2B","card":"#141F33","border":"#24324A","text":"#F1F5FF","muted":"#9AAAC1","soft":"#202C43","accent":"#7183FF","accent_hover":"#8795FF","good":"#53D6B0","warning":"#FFC66D","input":"#0D1728","nav":"#0B1221"}
    LIGHT={"bg":"#F2F5FA","surface":"#FFFFFF","card":"#FFFFFF","border":"#E1E7F0","text":"#172238","muted":"#65748B","soft":"#EDF1F8","accent":"#5668E8","accent_hover":"#4457D4","good":"#16896B","warning":"#B76810","input":"#F8FAFD","nav":"#FFFFFF"}
    NAV=[("概览","⌂"),("连接共享","↗"),("我的连接","▦"),("网络发现","⌕"),("设置","⚙"),("软件介绍","✦")]
    def __init__(self):
        self.data=load_data(); self.data.setdefault("theme","dark"); self.data.setdefault("tray",True); self.data.setdefault("startup",False); self.data.setdefault("favorites",[]); self.data.setdefault("recent",[])
        set_locale(self.data.get("language"))
        self.start_hidden="--background" in sys.argv
        ctk.set_appearance_mode(self.data["theme"]); ctk.set_default_color_theme("blue")
        ctk.CTk.__init__(self)
        self.title(tr(APP_NAME)); self.geometry("1180x800"); self.minsize(960,680); self.page="概览"; self.tray_icon=None; self.force_exit=False; self.anim_step=0
        self.configure(fg_color=self.c("bg")); self._set_app_icon(); self._make_premium_shell(); self.show_page("概览")
        self.protocol("WM_DELETE_WINDOW",self.on_close); self.bind("<Unmap>",self.on_minimize)
        self.refresh_connections(); self.after(600,self.start_tray); self.after(1800,self.periodic_refresh)
        
    def c(self,key): return (self.LIGHT if self.data.get("theme")=="light" else self.DARK)[key]
    def _set_app_icon(self):
        try: self.iconbitmap(str(resource_path("assets/app.ico")))
        except Exception: pass
    def _make_premium_shell(self):
        self.grid_columnconfigure(1,weight=1); self.grid_rowconfigure(0,weight=1)
        self.sidebar=ctk.CTkFrame(self,width=238,corner_radius=0,fg_color=self.c("nav"),border_width=0); self.sidebar.grid(row=0,column=0,sticky="nsew"); self.sidebar.grid_propagate(False)
        brand=ctk.CTkFrame(self.sidebar,fg_color="transparent"); brand.pack(fill="x",padx=18,pady=(26,24))
        from PIL import Image
        self.brand_image=ctk.CTkImage(Image.open(resource_path("assets/app.png")),size=(45,45))
        ctk.CTkLabel(brand,text="",image=self.brand_image).pack(side="left",padx=(0,11))
        txt=ctk.CTkFrame(brand,fg_color="transparent"); txt.pack(side="left",anchor="center")
        ctk.CTkLabel(txt,text=tr("景连 SMB"),font=ctk.CTkFont(family="Microsoft YaHei UI",size=19,weight="bold"),text_color=self.c("text")).pack(anchor="w")
        ctk.CTkLabel(txt,text="LOCAL NETWORK · SMB",font=ctk.CTkFont(size=9,weight="bold"),text_color=self.c("muted")).pack(anchor="w",pady=(2,0))
        ctk.CTkLabel(self.sidebar,text=tr("工作台"),font=ctk.CTkFont(size=11,weight="bold"),text_color=self.c("muted")).pack(anchor="w",padx=21,pady=(6,8))
        self.navbuttons={}
        for name,icon in self.NAV:
            b=ctk.CTkButton(self.sidebar,text=f"{icon}    {tr(name)}",anchor="w",height=43,corner_radius=11,fg_color="transparent",hover_color=self.c("soft"),text_color=self.c("muted"),font=ctk.CTkFont(family="Microsoft YaHei UI",size=13),command=lambda n=name:self.show_page(n))
            b.pack(fill="x",padx=12,pady=3); self.navbuttons[name]=b
        ctk.CTkFrame(self.sidebar,height=1,fg_color=self.c("border")).pack(fill="x",padx=17,pady=(17,13))
        ctk.CTkLabel(self.sidebar,text=tr("连接概况"),font=ctk.CTkFont(size=11,weight="bold"),text_color=self.c("muted")).pack(anchor="w",padx=21,pady=(0,6))
        self.side_status=ctk.CTkLabel(self.sidebar,text=tr("●  正在检查"),text_color=self.c("warning"),font=ctk.CTkFont(size=12)); self.side_status.pack(anchor="w",padx=21)
        ctk.CTkLabel(self.sidebar,text=tr("安全连接 · Windows 原生 SMB"),text_color=self.c("muted"),font=ctk.CTkFont(size=10)).pack(side="bottom",anchor="w",padx=17,pady=18)
        self.body=ctk.CTkFrame(self,fg_color=self.c("bg"),corner_radius=0); self.body.grid(row=0,column=1,sticky="nsew"); self.body.grid_columnconfigure(0,weight=1); self.body.grid_rowconfigure(1,weight=1)
        self.header=ctk.CTkFrame(self.body,fg_color="transparent",height=88); self.header.grid(row=0,column=0,sticky="ew",padx=32,pady=(19,1)); self.header.grid_columnconfigure(0,weight=1)
        self.heading=ctk.CTkLabel(self.header,text="",font=ctk.CTkFont(family="Microsoft YaHei UI",size=25,weight="bold"),text_color=self.c("text")); self.heading.grid(row=0,column=0,sticky="w")
        self.subheading=ctk.CTkLabel(self.header,text="",font=ctk.CTkFont(family="Microsoft YaHei UI",size=12),text_color=self.c("muted")); self.subheading.grid(row=1,column=0,sticky="w",pady=(3,0))
        self.theme_button=ctk.CTkButton(self.header,text=tr("☼  浅色模式") if self.data["theme"]=="dark" else tr("◐  深色模式"),width=128,height=36,corner_radius=10,fg_color=self.c("surface"),hover_color=self.c("soft"),border_width=1,border_color=self.c("border"),text_color=self.c("text"),command=self.toggle_theme); self.theme_button.grid(row=0,column=1,rowspan=2,sticky="e")
        self.content=ctk.CTkScrollableFrame(self.body,fg_color="transparent",corner_radius=0); self.content.grid(row=1,column=0,sticky="nsew",padx=32,pady=(0,24)); self.content.grid_columnconfigure(0,weight=1)
    def panel(self,parent=None): return ctk.CTkFrame(parent or self.content,fg_color=self.c("card"),corner_radius=18,border_width=1,border_color=self.c("border"))
    def label(self,parent,text,size=13,weight="normal",color="text",**kw): return ctk.CTkLabel(parent,text=text,font=ctk.CTkFont(family="Microsoft YaHei UI",size=size,weight=weight),text_color=self.c(color),**kw)
    def button(self,parent,text,command,primary=False,width=None,**kw):
        height=kw.pop("height",38)
        fg=kw.pop("fg_color", self.c("accent") if primary else self.c("soft"))
        hover=kw.pop("hover_color", self.c("accent_hover") if primary else self.c("border"))
        text_colour=kw.pop("text_color", "#FFFFFF" if primary else self.c("text"))
        return ctk.CTkButton(parent,text=text,command=command,width=width or 140,height=height,corner_radius=10,fg_color=fg,hover_color=hover,text_color=text_colour,font=ctk.CTkFont(family="Microsoft YaHei UI",size=12,weight="bold"),**kw)
    def show_page(self,name):
        self.page=name
        for n,b in self.navbuttons.items(): b.configure(fg_color=self.c("soft") if n==name else "transparent",text_color=self.c("good") if n==name else self.c("muted"))
        for w in self.content.winfo_children(): w.destroy()
        title={"概览":(tr("你好，欢迎回来"),tr("让局域网共享连接，更清晰、更轻松。")),"连接共享":(tr("连接到共享"),tr("输入网络路径，几步即可映射为本机磁盘。")),"我的连接":(tr("共享与收藏"),tr("查看映射状态，打开磁盘或管理常用地址。")),"网络发现":(tr("发现局域网设备"),tr("在本地网络中查找可访问 SMB 的设备。")),"设置":(tr("偏好设置"),tr("按你的习惯调整主题、托盘和启动方式。")),"软件介绍":(tr("关于景连 SMB"),tr("为 NAS、家庭网络和办公文件共享而设计。"))}[name]
        self.heading.configure(text=title[0]); self.subheading.configure(text=title[1])
        getattr(self,"premium_"+{"概览":"home","连接共享":"connect","我的连接":"connections","网络发现":"discover","设置":"settings","软件介绍":"about"}[name])()
    def toggle_theme(self):
        self.data["theme"]="light" if self.data.get("theme")=="dark" else "dark"; ctk.set_appearance_mode(self.data["theme"]); save_data(self.data); self.rebuild_shell()
    def rebuild_shell(self):
        self.anim_generation=getattr(self,"anim_generation",0)+1
        for n in ("sidebar","body"):
            w=getattr(self,n,None)
            if w is not None:
                try:w.destroy()
                except Exception:pass
        self._make_premium_shell(); self.show_page(self.page); self.update_stats()
    def _hero(self):
        self.anim_generation=getattr(self,"anim_generation",0)+1
        from PIL import ImageTk
        self._hero_icon=ImageTk.PhotoImage(Image.open(resource_path("assets/app.png")).convert("RGBA").resize((42,42)))
        self.hero=tk.Canvas(self.content,height=240,highlightthickness=0,bg=self.c("bg")); self.hero.grid(row=0,column=0,sticky="ew",pady=(2,14)); self.hero.bind("<Configure>",lambda e:self.draw_hero(self.anim_generation)); self.draw_hero(self.anim_generation)
    def draw_hero(self,generation=None):
        if generation is not None and generation!=self.anim_generation:return
        if not hasattr(self,"hero") or not self.hero.winfo_exists(): return
        c=self.hero; c.delete("all"); w=max(c.winfo_width(),600); h=240
        start=(14,28,55) if self.data["theme"]=="dark" else (39,56,122); end=(37,48,112) if self.data["theme"]=="dark" else (104,119,222)
        bands=45
        for i in range(bands):
            t=i/(bands-1); rgb=tuple(int(start[j]*(1-t)+end[j]*t) for j in range(3)); col="#%02x%02x%02x"%rgb; x0=w*i/bands; x1=w*(i+1)/bands+1; c.create_rectangle(x0,0,x1,h,fill=col,outline=col)
        phase=self.anim_step*.045
        for i in range(5):
            x=w*(.58+i*.12)+24*__import__("math").sin(phase+i); y=38+i*42+8*__import__("math").cos(phase*.8+i); r=16+(i%2)*7
            c.create_oval(x-r,y-r,x+r,y+r,fill="",outline="#9ab4ff",width=2)
        c.create_text(36,55,anchor="w",text="SHARE YOUR NETWORK",fill="#B6C5FF",font=("Segoe UI",10,"bold"))
        c.create_text(36,103,anchor="w",text=tr("连接每一份\n局域网里的文件"),fill="#FFFFFF",font=("Microsoft YaHei UI",24,"bold"),justify="left")
        c.create_text(38,178,anchor="w",text=tr("NAS、家庭电脑、办公室文件服务器，都能轻松访问。"),fill="#DCE4FF",font=("Microsoft YaHei UI",11))
        c.create_image(w-68,57,image=self._hero_icon)
        self.anim_step+=1
        if self.winfo_exists(): self.after(85,lambda:self.draw_hero(generation))
    def premium_home(self):
        self._hero(); self.button(self.content,tr("＋  添加共享"),lambda:self.show_page("连接共享"),True,width=155).grid(row=1,column=0,sticky="w",pady=(0,14))
        stats=ctk.CTkFrame(self.content,fg_color="transparent"); stats.grid(row=2,column=0,sticky="ew",pady=(0,15)); stats.grid_columnconfigure((0,1,2),weight=1)
        for i,(title,desc,key) in enumerate([(tr("网络映射"),tr("由 Windows 管理"),"mapped"),(tr("常用共享"),tr("收藏的网络位置"),"favorite"),(tr("网络服务"),tr("系统原生支持"),"protocol")]):
            card=self.panel(stats); card.grid(row=0,column=i,sticky="ew",padx=(0,8) if i<2 else 0)
            self.label(card,title,12,color="muted").pack(anchor="w",padx=18,pady=(15,4)); val="Windows SMB" if key=="protocol" else ("—" if key=="mapped" else str(len(self.data.get("favorites",[]))))
            v=self.label(card,val,23,"bold"); v.pack(anchor="w",padx=18); self.label(card,desc,10,color="muted").pack(anchor="w",padx=18,pady=(2,15))
            if key=="mapped": self.stat_mapped=v
            if key=="favorite": self.stat_favs=v
        self.home_recent=self.panel(); self.home_recent.grid(row=3,column=0,sticky="ew",pady=(1,8)); self.home_recent.grid_columnconfigure(0,weight=1)
        top=ctk.CTkFrame(self.home_recent,fg_color="transparent"); top.pack(fill="x",padx=18,pady=(14,7)); self.label(top,tr("最近使用"),15,"bold").pack(side="left"); self.button(top,tr("查看全部  →"),lambda:self.show_page("我的连接"),width=105,height=30).pack(side="right")
        favs=self.data.get("recent",[])
        if not favs: self.label(self.home_recent,tr("尚无连接记录。添加第一个共享，开始你的本地工作空间。"),11,color="muted",wraplength=730,justify="left").pack(anchor="w",padx=19,pady=(5,18))
        for fav in favs[:3]:
            row=ctk.CTkFrame(self.home_recent,fg_color=self.c("soft"),corner_radius=11); row.pack(fill="x",padx=12,pady=4); self.label(row,"▰",15,color="accent").pack(side="left",padx=(12,8),pady=10); self.label(row,fav.get("path",""),12,"bold").pack(side="left",padx=2); self.button(row,tr("连接"),lambda x=fav:self.favorite_connect(x),True,width=74,height=31).pack(side="right",padx=8,pady=6)
        self.update_stats()
    def premium_connect(self):
        f=self.panel(); f.grid(row=0,column=0,sticky="ew",pady=(0,12)); f.grid_columnconfigure(1,weight=1)
        self.label(f,tr("共享位置"),13,"bold").grid(row=0,column=0,sticky="w",padx=20,pady=(20,4)); self.inputs={}
        self.inputs["共享路径"]=ctk.CTkEntry(f,height=43,corner_radius=10,placeholder_text=tr(r"\\NAS名称\共享文件夹"),fg_color=self.c("input"),border_color=self.c("border"),text_color=self.c("text"),font=ctk.CTkFont(size=13)); self.inputs["共享路径"].grid(row=0,column=1,sticky="ew",padx=(14,20),pady=(17,4))
        self.label(f,tr("映射盘符"),11,color="muted").grid(row=1,column=1,sticky="w",padx=15,pady=(0,3)); self.inputs["映射盘符"]=ctk.CTkEntry(f,height=40,width=160,corner_radius=9,placeholder_text="Z:",fg_color=self.c("input"),border_color=self.c("border"),text_color=self.c("text")); self.inputs["映射盘符"].grid(row=2,column=1,sticky="w",padx=14,pady=(0,8))
        self.label(f,tr("用户名"),12).grid(row=3,column=0,sticky="w",padx=20,pady=7); self.inputs["用户名"]=ctk.CTkEntry(f,height=40,corner_radius=9,placeholder_text=tr("可选，例如 DOMAIN\\user"),fg_color=self.c("input"),border_color=self.c("border"),text_color=self.c("text")); self.inputs["用户名"].grid(row=3,column=1,sticky="ew",padx=14,pady=7)
        self.label(f,tr("密码"),12).grid(row=4,column=0,sticky="w",padx=20,pady=7); self.inputs["密码"]=ctk.CTkEntry(f,height=40,corner_radius=9,placeholder_text=tr("密码仅交由 Windows 处理"),show="●",fg_color=self.c("input"),border_color=self.c("border"),text_color=self.c("text")); self.inputs["密码"].grid(row=4,column=1,sticky="ew",padx=14,pady=7)
        self.persist=ctk.CTkCheckBox(f,text=tr("登录时重新连接"),text_color=self.c("muted")); self.persist.select(); self.persist.grid(row=5,column=1,sticky="w",padx=14,pady=(8,3)); self.remember=ctk.CTkCheckBox(f,text=tr("保存到常用共享"),text_color=self.c("muted")); self.remember.grid(row=6,column=1,sticky="w",padx=14,pady=(3,13))
        actions=ctk.CTkFrame(f,fg_color="transparent"); actions.grid(row=7,column=1,sticky="w",padx=10,pady=(0,19)); self.button(actions,tr("连接共享"),self.connect,True,width=125).pack(side="left",padx=4); self.button(actions,tr("测试连接"),self.test_connection,width=110).pack(side="left",padx=4); self.button(actions,tr("打开位置"),self.open_unc,width=110).pack(side="left",padx=4)
        note=self.panel(); note.grid(row=1,column=0,sticky="ew"); self.label(note,tr("安全与连接提示"),13,"bold").pack(anchor="w",padx=18,pady=(14,4)); self.label(note,tr("密码不会写入工具配置。请确认设备已开启文件共享，并使用你有权访问的账号。"),11,color="muted",wraplength=760,justify="left").pack(anchor="w",padx=18,pady=(0,14))
    def premium_connections(self):
        self.connlist=self.panel(); self.connlist.grid(row=0,column=0,sticky="ew",pady=3); self.render_connections()
    def render_connections(self):
        if not hasattr(self,"connlist") or not self.connlist.winfo_exists(): return
        for w in self.connlist.winfo_children(): w.destroy()
        rows=mapped_drives(); self.label(self.connlist,tr("已映射网络磁盘"),15,"bold").pack(anchor="w",padx=18,pady=(16,8))
        if not rows: self.label(self.connlist,tr("当前没有映射磁盘。连接共享文件夹后，状态会显示在这里。"),11,color="muted").pack(anchor="w",padx=18,pady=(0,14))
        for local,remote,status in rows:
            r=ctk.CTkFrame(self.connlist,fg_color=self.c("soft"),corner_radius=11); r.pack(fill="x",padx=12,pady=4); self.label(r,"▰",16,color="accent").pack(side="left",padx=(12,8),pady=10); self.label(r,f"{local}    {remote}",12,"bold").pack(side="left",padx=2); self.button(r,tr("打开"),lambda x=local:os.startfile(x),width=68,height=31).pack(side="right",padx=4,pady=6); self.button(r,tr("断开"),lambda x=local:self.disconnect(x),width=68,height=31,fg_color="#B64B56",hover_color="#D45D68",text_color="#FFFFFF").pack(side="right",padx=4,pady=6)
        self.label(self.connlist,tr("收藏的共享位置"),15,"bold").pack(anchor="w",padx=18,pady=(18,8)); favs=self.data.get("favorites",[])
        if not favs: self.label(self.connlist,tr("连接共享时勾选保存，即可收藏地址。"),11,color="muted").pack(anchor="w",padx=18,pady=(0,14))
        for item in favs:
            r=ctk.CTkFrame(self.connlist,fg_color=self.c("soft"),corner_radius=11); r.pack(fill="x",padx=12,pady=4); self.label(r,"◇",16,color="accent").pack(side="left",padx=(12,8),pady=10); self.label(r,f"{item.get('path')}   ·   {item.get('drive','')}",12).pack(side="left",padx=2); self.button(r,tr("连接"),lambda x=item:self.favorite_connect(x),True,width=70,height=31).pack(side="right",padx=5,pady=6); self.button(r,tr("移除"),lambda x=item:self.remove_fav(x),width=65,height=31).pack(side="right",padx=3,pady=6)
    def premium_discover(self):
        f=self.panel(); f.grid(row=0,column=0,sticky="ew",pady=(0,12)); f.grid_columnconfigure(0,weight=1); self.label(f,tr("扫描本机所在网段"),14,"bold").grid(row=0,column=0,sticky="w",padx=18,pady=(16,4)); self.label(f,tr("检测 TCP 445 端口开放设备；不会读取共享内容。"),11,color="muted").grid(row=1,column=0,sticky="w",padx=18,pady=(0,11)); bar=ctk.CTkFrame(f,fg_color="transparent"); bar.grid(row=2,column=0,sticky="ew",padx=13,pady=(0,9)); self.subnet=ctk.CTkEntry(bar,height=40,corner_radius=9,placeholder_text=tr("自动检测 /24 网段，例如 192.168.1"),fg_color=self.c("input"),border_color=self.c("border"),text_color=self.c("text")); self.subnet.pack(side="left",fill="x",expand=True,padx=4); self.button(bar,tr("开始扫描"),self.scan,True,width=110).pack(side="left",padx=4); self.scanstatus=self.label(f,tr("等待扫描"),11,color="muted"); self.scanstatus.grid(row=3,column=0,sticky="w",padx=18,pady=(0,13))
        self.scanresults=self.panel(); self.scanresults.grid(row=1,column=0,sticky="ew"); self.label(self.scanresults,tr("设备列表"),14,"bold").pack(anchor="w",padx=17,pady=(14,12))
    def scan_done(self,hosts,e):
        if e: self.scanstatus.configure(text=tr('扫描失败：{0}', e)); return
        for w in self.scanresults.winfo_children():w.destroy()
        self.label(self.scanresults,tr('扫描完成 · 发现 {0} 台 SMB 设备', len(hosts or [])),14,"bold").pack(anchor="w",padx=17,pady=(14,9))
        for host in hosts or []:
            row=ctk.CTkFrame(self.scanresults,fg_color=self.c("soft"),corner_radius=10); row.pack(fill="x",padx=11,pady=3); self.label(row,"●",12,color="good").pack(side="left",padx=(12,7),pady=9); self.label(row,host,12,"bold").pack(side="left"); self.button(row,tr("连接此设备"),lambda x=host:self.use_host(x),width=110,height=31).pack(side="right",padx=6,pady=5)
        self.scanstatus.configure(text=tr("已完成。输入设备 IP 和共享名即可连接。"))
    def premium_settings(self, row_offset=0):
        f=self.panel(); f.grid(row=row_offset,column=0,sticky="ew",pady=(0,12)); self.label(f,tr("外观"),15,"bold").pack(anchor="w",padx=18,pady=(17,4)); self.label(f,tr("选择更适合你的工作环境的配色。"),11,color="muted").pack(anchor="w",padx=18)
        theme=ctk.CTkSegmentedButton(f,values=[tr("深色"),tr("浅色")],command=self.select_theme,selected_color=self.c("accent"),selected_hover_color=self.c("accent_hover"),unselected_color=self.c("soft"),unselected_hover_color=self.c("border"),text_color=self.c("text")); theme.set(tr("浅色") if self.data["theme"]=="light" else tr("深色")); theme.pack(anchor="w",padx=18,pady=(12,18))
        f2=self.panel(); f2.grid(row=row_offset + 1,column=0,sticky="ew",pady=7); self.label(f2,tr("启动与托盘"),15,"bold").pack(anchor="w",padx=18,pady=(17,8)); self.tray_var=ctk.BooleanVar(value=bool(self.data.get("tray",True))); ctk.CTkSwitch(f2,text=tr("关闭窗口时收至系统托盘"),variable=self.tray_var,command=self.save_tray,text_color=self.c("text"),progress_color=self.c("accent")).pack(anchor="w",padx=18,pady=8); self.start_var=ctk.BooleanVar(value=self.is_startup_enabled()); ctk.CTkSwitch(f2,text=tr("登录 Windows 后自动启动"),variable=self.start_var,command=self.toggle_startup,text_color=self.c("text"),progress_color=self.c("accent")).pack(anchor="w",padx=18,pady=8); self.start_hidden_var=ctk.BooleanVar(value=bool(self.data.get("start_hidden",False))); ctk.CTkSwitch(f2,text=tr("自动启动时最小化到托盘"),variable=self.start_hidden_var,command=self.toggle_start_hidden,text_color=self.c("text"),progress_color=self.c("accent")).pack(anchor="w",padx=18,pady=(8,18)); self.label(f2,tr("启动项仅写入当前 Windows 用户的注册表 Run 项。"),10,color="muted").pack(anchor="w",padx=18,pady=(0,14))
        f3=self.panel(); f3.grid(row=row_offset + 2,column=0,sticky="ew",pady=7); self.label(f3,tr("窗口行为"),14,"bold").pack(anchor="w",padx=18,pady=(15,4)); self.label(f3,tr("点击窗口右上角关闭按钮后，仍可通过任务栏通知区域的景连图标重新打开。托盘菜单提供显示、快速连接和退出。"),11,color="muted",wraplength=750,justify="left").pack(anchor="w",padx=18,pady=(0,15))
    def select_theme(self,value): self.data["theme"]="light" if value==tr("浅色") else "dark"; ctk.set_appearance_mode(self.data["theme"]); save_data(self.data); self.rebuild_shell()
    def save_tray(self): self.data["tray"]=bool(self.tray_var.get()); save_data(self.data)
    def toggle_start_hidden(self): self.data["start_hidden"]=bool(self.start_hidden_var.get()); save_data(self.data)
    def is_startup_enabled(self):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r"Software\Microsoft\Windows\CurrentVersion\Run") as k: val,_=winreg.QueryValueEx(k,"JingSMB"); return bool(val)
        except OSError: return False
    def toggle_startup(self):
        try:
            path=Path(sys.executable) if getattr(sys,"frozen",False) else Path(__file__).resolve(); command=f'"{path}" --background' if getattr(sys,"frozen",False) else f'"{sys.executable}" "{path}" --background'
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER,r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
                if self.start_var.get(): winreg.SetValueEx(k,"JingSMB",0,winreg.REG_SZ,command)
                else:
                    try: winreg.DeleteValue(k,"JingSMB")
                    except FileNotFoundError: pass
        except OSError as e:
            self.start_var.set(not self.start_var.get()); messagebox.showerror(tr("启动设置失败"),str(e))
    def premium_about(self):
        f=self.panel(); f.grid(row=0,column=0,sticky="ew",pady=(0,12)); logo=ctk.CTkImage(Image.open(resource_path("assets/app.png")),size=(100,100)); ctk.CTkLabel(f,text="",image=logo).pack(anchor="w",padx=22,pady=(22,9)); self.label(f,tr("景连 SMB 共享连接器"),22,"bold").pack(anchor="w",padx=22); self.label(f,tr('版本 {0}     WINDOWS DESKTOP', VERSION),10,color="muted").pack(anchor="w",padx=22,pady=(4,15)); self.label(f,tr("让局域网共享连接更简单"),15,"bold",color="accent").pack(anchor="w",padx=22,pady=(0,7)); self.label(f,tr("快速连接 NAS、家庭电脑和办公文件服务器。使用 Windows 原生 SMB 服务映射网络磁盘，支持收藏、连通性检查、设备发现、托盘驻留与开机启动。"),12,color="muted",wraplength=780,justify="left").pack(anchor="w",padx=22,pady=(0,20))
        f2=self.panel(); f2.grid(row=1,column=0,sticky="ew",pady=7); self.label(f2,tr("联系作者"),15,"bold").pack(anchor="w",padx=18,pady=(16,8))
        for platform,account in [(tr("微信公众号"),"景同志"),(tr("小红书"),"景同学")]:
            row=ctk.CTkFrame(f2,fg_color=self.c("soft"),corner_radius=11); row.pack(fill="x",padx=12,pady=4); self.label(row,platform,11,color="muted",width=130,anchor="w").pack(side="left",padx=12,pady=11); self.label(row,account,14,"bold",color="accent").pack(side="left")
        row=ctk.CTkFrame(f2,fg_color=self.c("soft"),corner_radius=11); row.pack(fill="x",padx=12,pady=4)
        self.label(row,tr("开源仓库"),11,color="muted",width=130,anchor="w").pack(side="left",padx=12,pady=11)
        link=self.label(row,GITHUB_URL.replace("https://",""),14,"bold",color="accent")
        link.pack(side="left",pady=11); link.configure(cursor="hand2")
        link.bind("<Button-1>",lambda event:webbrowser.open(GITHUB_URL))
        self.label(row,tr("点击访问，免费下载最新版和源代码"),10,color="muted").pack(side="left",padx=10,pady=11)
        self.label(f2,tr("凭据通过 Windows 网络服务提交，不存入应用配置。"),10,color="muted",wraplength=720,justify="left").pack(anchor="w",padx=18,pady=(10,15))
    def update_stats(self):
        rows=mapped_drives(); self.side_status.configure(text=tr('●  {0} 个磁盘已连接', len(rows)),text_color=self.c("good") if rows else self.c("muted"))
        if hasattr(self,"stat_mapped"):
            try:self.stat_mapped.configure(text=str(len(rows)))
            except Exception:pass
        if hasattr(self,"stat_favs"):
            try:self.stat_favs.configure(text=str(len(self.data.get("favorites",[]))))
            except Exception:pass
    def refresh_connections(self):
        if hasattr(self,"connlist") and self.connlist.winfo_exists(): self.render_connections()
        self.update_stats()
    def _connected(self,remote,drive,e):
        if e: self.side_status.configure(text=tr("●  连接未完成"),text_color="#E25C68"); messagebox.showerror(tr("连接失败"),str(e)); return
        if self.remember.get()==1:
            fav=self.data.setdefault("favorites",[])
            if remote not in [x.get("path") for x in fav]: fav.append({"path":remote,"drive":drive}); save_data(self.data)
        self.data["recent"]=[{"path":remote,"drive":drive}]+[x for x in self.data.get("recent",[]) if x.get("path")!=remote]; self.data["recent"]=self.data["recent"][:8]; save_data(self.data); self.refresh_connections(); messagebox.showinfo(tr("连接成功"),tr('{0}\n已映射为 {1}', remote, drive))
    def favorite_connect(self,item):
        self.show_page("连接共享"); self.inputs["共享路径"].insert(0,item.get("path","")); self.inputs["映射盘符"].insert(0,item.get("drive","Z:"))
    def remove_fav(self,item): self.data["favorites"].remove(item); save_data(self.data); self.render_connections(); self.update_stats()
    def use_host(self,host): self.show_page("连接共享"); self.inputs["共享路径"].insert(0,f"\\\\{host}\\")
    def periodic_refresh(self):
        if not self.winfo_exists(): return
        self.refresh_connections(); self.after(10000,self.periodic_refresh)
    def start_tray(self):
        if self.tray_icon is not None:return
        try:
            image=Image.open(resource_path("assets/app.png")).convert("RGBA")
            menu=pystray.Menu(pystray.MenuItem(tr("显示景连 SMB"),lambda icon,item:self.after(0,self.show_window),default=True),pystray.MenuItem(tr("快速连接共享"),lambda icon,item:self.after(0,lambda:self.open_page("连接共享"))),pystray.Menu.SEPARATOR,pystray.MenuItem(tr("退出"),lambda icon,item:self.after(0,self.exit_app)))
            self.tray_icon=pystray.Icon("JingSMB",image,tr(APP_NAME),menu)
            def tray_worker():
                try:self.tray_icon.run()
                except Exception:self.after(0,self.tray_failed)
            threading.Thread(target=tray_worker,daemon=True).start()
        except Exception as e:
            self.tray_icon=None; self.side_status.configure(text=tr("托盘暂不可用"),text_color=self.c("warning"))
    def tray_failed(self):
        self.tray_icon=None
        if hasattr(self,"side_status") and self.side_status.winfo_exists():self.side_status.configure(text=tr("托盘暂不可用 · 关闭时退出"),text_color=self.c("warning"))
        if self.start_hidden:self.after(0,self.show_window)
    def open_page(self,page): self.show_window(); self.show_page(page)
    def show_window(self): self.deiconify(); self.state("normal"); self.lift(); self.focus_force()
    def on_minimize(self,event=None):
        # Keep normal minimization on the taskbar; the close button is the explicit tray action.
        return
    def on_close(self):
        if self.data.get("tray",True) and self.tray_icon is not None: self.withdraw()
        else:self.exit_app()
    def exit_app(self):
        self.force_exit=True
        if self.tray_icon:
            try:self.tray_icon.stop()
            except Exception:pass
        self.destroy()

from core import query_mappings, normalize_share, normalize_drive, available_drives, clean_records, import_records, atomic_json

def mapped_drives():
    return query_mappings()

def save_data(d):
    atomic_json(DATA_FILE, d)

# Studio interface is loaded below.
from studio import make_studio
App=make_studio(PremiumApp, globals())

if __name__=="__main__":
    if os.name!="nt": raise SystemExit(tr("此程序仅支持 Windows"))
    if getattr(sys,"frozen",False):
        try:
            hwnd=ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                process_ids=(wintypes.DWORD*1)(); attached=ctypes.windll.kernel32.GetConsoleProcessList(process_ids,1)
                if attached<=1: ctypes.windll.user32.ShowWindow(hwnd,0)
        except Exception: pass
    App().mainloop()
