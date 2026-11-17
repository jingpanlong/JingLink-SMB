"""景连 SMB 安装器：安装、创建快捷方式并提供卸载入口。"""
import os, shutil, sys, subprocess, winreg
from pathlib import Path
from core import VERSION
import tkinter as tk
from tkinter import messagebox, ttk

APP="景连 SMB 共享连接器"; KEY=r"Software\Microsoft\Windows\CurrentVersion\Uninstall\JingSMB"
def app_file():
    root=Path(getattr(sys,"_MEIPASS",Path(__file__).parent)); return root/"景连SMB.exe"
def icon_file():
    root=Path(getattr(sys,"_MEIPASS",Path(__file__).parent)); return root/"assets"/"app.ico"
def install():
    root=tk.Tk(); root.withdraw(); root.iconbitmap(str(icon_file()))
    dest=Path(os.getenv("LOCALAPPDATA",str(Path.home())))/"Programs"/"JingSMB"
    try:
        dest.mkdir(parents=True,exist_ok=True); shutil.copy2(app_file(),dest/"景连SMB.exe"); shutil.copy2(icon_file(),dest/"app.ico")
        if getattr(sys,"frozen",False): shutil.copy2(sys.executable,dest/"卸载.exe")
        start=Path(os.getenv("APPDATA",str(Path.home())))/"Microsoft"/"Windows"/"Start Menu"/"Programs"; start.mkdir(parents=True,exist_ok=True)
        desk=Path(os.path.join(os.environ.get("USERPROFILE",str(Path.home())),"Desktop")); desk.mkdir(parents=True,exist_ok=True)
        ps="$w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut('{}'); $s.TargetPath='{}'; $s.IconLocation='{}'; $s.Save()"
        for link in [start/(APP+".lnk"),desk/(APP+".lnk")]:
            q=lambda p:str(p).replace("'","''")
            script=ps.format(q(link),q(dest/"景连SMB.exe"),q(dest/"app.ico"))
            subprocess.run(["powershell.exe","-NoProfile","-ExecutionPolicy","Bypass","-Command",script],check=True,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,KEY) as k:
            winreg.SetValueEx(k,"DisplayName",0,winreg.REG_SZ,APP); winreg.SetValueEx(k,"DisplayVersion",0,winreg.REG_SZ,VERSION); winreg.SetValueEx(k,"DisplayIcon",0,winreg.REG_SZ,str(dest/"app.ico")); winreg.SetValueEx(k,"Publisher",0,winreg.REG_SZ,"景同志"); winreg.SetValueEx(k,"InstallLocation",0,winreg.REG_SZ,str(dest)); winreg.SetValueEx(k,"UninstallString",0,winreg.REG_SZ,f'"{dest / "卸载.exe"}" --uninstall')
        messagebox.showinfo("安装完成",f"{APP} 已安装。\n\n程序位置：{dest}\n桌面和开始菜单快捷方式已创建。",parent=root)
        try: subprocess.Popen([str(dest/"景连SMB.exe")],close_fds=True)
        except Exception: pass
    except Exception as e: messagebox.showerror("安装失败",str(e),parent=root)
    root.destroy()
def uninstall():
    root=tk.Tk(); root.withdraw(); root.iconbitmap(str(icon_file()))
    dest=Path(os.getenv("LOCALAPPDATA",str(Path.home())))/"Programs"/"JingSMB"; start=Path(os.getenv("APPDATA",str(Path.home())))/"Microsoft"/"Windows"/"Start Menu"/"Programs"/(APP+".lnk"); desk=Path(os.path.join(os.environ.get("USERPROFILE",str(Path.home())),"Desktop"))/(APP+".lnk")
    try:
        if dest.exists():
            # Windows keeps the running uninstaller image locked until this process exits.
            for p in dest.iterdir():
                if p.name != "卸载.exe":
                    try: p.unlink()
                    except OSError: pass
            try: (dest/"卸载.exe").unlink()
            except OSError:
                safe_dest = str(dest).replace("'", "''")
                cleanup = f"Start-Sleep -Seconds 2; Remove-Item -LiteralPath '{safe_dest}' -Recurse -Force"
                subprocess.Popen(["powershell.exe","-NoProfile","-ExecutionPolicy","Bypass","-Command",cleanup],creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            try: dest.rmdir()
            except OSError: pass
        for p in (start,desk):
            if p.exists(): p.unlink()
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r"Software\Microsoft\Windows\CurrentVersion\Run",0,winreg.KEY_SET_VALUE) as k:
                try:winreg.DeleteValue(k,"JingSMB")
                except FileNotFoundError:pass
        except OSError:pass
        try: winreg.DeleteKey(winreg.HKEY_CURRENT_USER,KEY)
        except FileNotFoundError: pass
        messagebox.showinfo("卸载完成",f"{APP} 已从此用户账户卸载。个人配置文件保留在 AppData\\Roaming\\JingSMB。",parent=root)
    except Exception as e: messagebox.showerror("卸载失败",str(e),parent=root)
    root.destroy()
def installer_window():
    import customtkinter as ctk
    from PIL import Image
    ctk.set_appearance_mode("dark")
    app=ctk.CTk(); app.title("安装景连 SMB"); app.geometry("580x490"); app.resizable(False,False)
    app.configure(fg_color="#0D1518")
    app.after(200,lambda:app.iconbitmap(str(icon_file())))
    logo=ctk.CTkImage(Image.open(icon_file().with_suffix(".png")),size=(76,76))
    ctk.CTkLabel(app,text="",image=logo).pack(pady=(38,14))
    ctk.CTkLabel(app,text="连接，从景连开始。",font=("Microsoft YaHei UI",27,"bold"),text_color="#EAF3F1").pack()
    ctk.CTkLabel(app,text=f"JINGLINK STUDIO  /  {VERSION}",font=("Segoe UI",11),text_color="#65D6B0").pack(pady=(8,22))
    panel=ctk.CTkFrame(app,fg_color="#152226",corner_radius=16,border_width=1,border_color="#293B40");panel.pack(fill="x",padx=40)
    ctk.CTkLabel(panel,text="NAS 与网络共享  ·  深浅主题  ·  托盘驻留",text_color="#EAF3F1").pack(pady=(18,6))
    ctk.CTkLabel(panel,text="安装到当前用户目录，创建桌面和开始菜单快捷方式。",text_color="#9BAFB1",font=("Microsoft YaHei UI",11)).pack(pady=(0,18))
    def run():app.destroy();install()
    ctk.CTkButton(app,text="安装并启动  →",command=run,height=45,width=300,corner_radius=12,fg_color="#147D70",hover_color="#1A9482",font=("Microsoft YaHei UI",14,"bold")).pack(pady=(24,10))
    ctk.CTkLabel(app,text="无需管理员权限 · Windows 10 / 11",text_color="#9BAFB1",font=("Microsoft YaHei UI",10)).pack()
    app.mainloop()

if __name__=="__main__":
    if "--uninstall" in sys.argv: uninstall()
    else: installer_window()
