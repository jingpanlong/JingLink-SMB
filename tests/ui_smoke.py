"""Run with a display; native Windows operations are not invoked."""
import importlib.util
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import customtkinter as ctk
from PIL import ImageGrab
import core
import studio

# Keep drive availability deterministic, including on Windows test machines.
core.available_drives = studio.available_drives = lambda: ["Z:", "Y:", "X:"]
spec=importlib.util.spec_from_file_location('jinglink',Path(__file__).resolve().parents[1]/'smb-connect.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
# Isolate from user data and avoid tray during UI-only smoke checks.
test_data = TemporaryDirectory(prefix='jinglink-ui-')
m.APP_DIR = Path(test_data.name)
m.DATA_FILE = m.APP_DIR / 'connections.json'
m.App.start_tray = lambda self: None
app=m.App();app.stop_animation()
errors=[]
app.report_callback_exception=lambda *e:errors.append(e)
app.data['favorites']=[dict(path=r'\\NAS\Public',drive='Z:',name='家庭资料库')]
app.rows=[('Z:',r'\\NAS\Public','已映射')]
app.data['recent']=app.data['favorites'][:]
app.refresh_connections=lambda:None
out=Path(__file__).resolve().parents[1]/'previews';out.mkdir(exist_ok=True)
def pump():
    for i in range(6):app.update();time.sleep(.04)
for theme in ['dark','light']:
    app.data['theme']=theme;ctk.set_appearance_mode(theme);app.rebuild_shell()
    for page,_ in app.NAV:
        app.show_page(page);pump()
        assert app.heading.cget('text')
        if page=='连接共享':assert app.connect_btn.cget('text')=='连接共享  →'
        if os.environ.get("JINGLINK_CAPTURE_PREVIEWS") == "1" and page in ['概览','连接共享','我的连接','设置']:
            name={'概览':'overview','连接共享':'connect','我的连接':'shares','设置':'settings'}[page]
            ImageGrab.grab(bbox=(0,0,1240,850),xdisplay=os.environ.get('DISPLAY')).save(out/f'{theme}-{name}.png')
    app.show_page('连接共享');app.inputs['共享路径'].insert(0,r'\\server\share')
    app.inputs['密码'].insert(0,'temporary');app.show_page('概览');app.show_page('连接共享');pump()
    assert app.inputs['共享路径'].get()==r'\\server\share'
    app.draft=None;app.page='概览'
app.show_page('网络发现');app.scan_busy=True;app.scan_prefix='192.168.1';app.scan_progress(80);app.show_page('设置')
app.scan_done((['192.168.1.10'],True),None);app.show_page('网络发现');pump()
app.show_page('我的连接');app.search_var.set('no-match');pump();app.search_var.set('');pump()
app.search_var.set('');app.edit_favorite(app.data['favorites'][0]);pump()
for child in app.winfo_children():
    if isinstance(child,ctk.CTkToplevel):child.destroy()
app.favorite_connect({'path':r'\\test\share','drive':'Y:'});pump()
assert app.inputs['共享路径'].get()==r'\\test\share'
assert app.inputs['映射盘符'].get()=='Y:'

def widgets(parent, widget_type):
    for child in parent.winfo_children():
        if isinstance(child, widget_type):
            yield child
        yield from widgets(child, widget_type)


def button(parent, label):
    return next(child for child in widgets(parent, ctk.CTkButton)
                if child.cget('text') == label)


def active_dialog():
    return next(child for child in app.winfo_children() if isinstance(child, ctk.CTkToplevel))


def sync(work, done):
    done(work(), None)


app._async = sync
app.show_page('连接共享')
app.clipboard_clear()
app.clipboard_append('smb://NAS/Team%20Share')
button(app.content, '粘贴地址').invoke()
assert app.inputs['共享路径'].get() == r"\\NAS\Team Share"
button(app.content, '保存为收藏').invoke()
dialog = active_dialog()
button(dialog, '保存收藏').invoke()
assert app.data['favorites'][-1]['path'] == r"\\NAS\Team Share"

app.show_page('我的连接')
app.set_filter('最近连接')
assert any(label.cget('text') == r"\\NAS\Public"
           for label in widgets(app.connlist, ctk.CTkLabel))
button(app.connlist, '连接').invoke()
app.inputs['映射盘符'].set(studio.AUTO_DRIVE)
clipped = []
opened = []
m.map_share = lambda *values: clipped.append(values)
app.open_location = opened.append
app.open_after.select()
app.connect_btn.invoke()
assert clipped[0][:2] == (r"\\NAS\Public", 'Z:')
assert opened == ['Z:']
assert app.data['recent'][0]['drive'] == 'Z:'

studio.diagnose_share = lambda path: (True, '测试用 TCP 445 诊断')
button(app.content, '测试可达性').invoke()
dialog = active_dialog()
button(dialog, '复制诊断').invoke()
assert app.clipboard_get() == '测试用 TCP 445 诊断'
dialog.destroy()

studio.local_share_paths = lambda: [r"\\DESKTOP\Public"]
app.show_page('网络发现')
app.show_local_shares()
dialog = active_dialog()
button(dialog, '复制路径').invoke()
assert app.clipboard_get() == r"\\DESKTOP\Public"
dialog.destroy()
# Real language selector, stable page IDs, and saved preferences.
import json
from localization import get_locale

app.show_page('连接共享')
app.inputs['共享路径'].delete(0, 'end')
app.inputs['共享路径'].insert(0, r'\\NAS\Draft')
app.inputs['映射盘符'].set(studio.AUTO_DRIVE)
app.inputs['用户名'].insert(0, 'test-user')
app.show_page('设置')
app.language_menu._buttons_dict['English'].invoke()
pump()
assert get_locale() == 'en' and app.title() == 'JingLink SMB'
assert app.language_menu.get() == 'English'
assert json.loads(m.DATA_FILE.read_text(encoding='utf-8'))['language'] == 'en'
if os.environ.get('JINGLINK_CAPTURE_PREVIEWS') == '1':
    ImageGrab.grab(bbox=(0,0,1240,850),xdisplay=os.environ.get('DISPLAY')).save(out/'english-settings.png')

for page, _ in app.NAV:
    app.navbuttons[page].invoke()
    pump()
    assert app.heading.cget('text').isascii()
app.show_page('连接共享')
assert app.connect_btn.cget('text') == 'Connect  →'
assert app.inputs['共享路径'].get() == r'\\NAS\Draft'
assert app.inputs['用户名'].get() == 'test-user'
assert app.inputs['映射盘符'].get() == 'Auto-select'
assert app._form()[1] == studio.AUTO_DRIVE
app.show_page('我的连接')
assert app.filter_value == '最近连接'
assert button(app.connlist, 'Connect')
app.show_page('设置')
app.language_menu._buttons_dict['中文'].invoke()
pump()
assert app.title() == '景连 SMB' and get_locale() == 'zh'
assert json.loads(m.DATA_FILE.read_text(encoding='utf-8'))['language'] == 'zh'
app.select_language('English')
assert not errors, errors
app.exit_app()
reopened = m.App()
assert reopened.title() == 'JingLink SMB'
reopened.exit_app()
test_data.cleanup()
print('PASS: 14 themed pages, form preservation, search, scan navigation, paste, offline favorites, recent, mapped-open, diagnostics, local shares, English labels, language persistence and draft preservation; no Tk callback errors')
