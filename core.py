"""Validated connection records and native Windows mapping discovery."""
import ctypes
import json
import os
import re
import socket
import tempfile
import time
from ctypes import wintypes
from pathlib import Path
from urllib.parse import unquote, urlsplit

from localization import tr


VERSION = "2.2.0"
AUTO_DRIVE = "自动选择"


def normalize_share(value):
    value = value.strip().removeprefix('\ufeff').strip()
    if len(value) >= 2 and (value[0], value[-1]) in {('"', '"'), ('“', '”'), ("'", "'")}:
        value = value[1:-1].strip()
    if any(ord(c) < 32 for c in value):
        raise ValueError(tr('共享路径中包含换行或其他不可用字符。'))
    if value.lower().startswith('smb://'):
        try:
            url = urlsplit(value)
            if not url.netloc or any(c in url.netloc for c in '@:\\%[]') or '?' in value or '#' in value:
                raise ValueError
            parts = [unquote(part, errors='strict') for part in url.path.rstrip('/').split('/')[1:]]
            if any('/' in part or '\\' in part for part in parts):
                raise ValueError
            value = '\\\\' + '\\'.join([url.netloc, *parts])
        except ValueError:
            raise ValueError(tr('请粘贴普通 SMB 地址，例如 smb://NAS/Public。')) from None
    value = value.replace('/', '\\').rstrip('\\')
    if not value.startswith('\\\\'):
        raise ValueError(tr(r'请输入完整共享路径，例如 \\NAS\Public'))
    parts = value[2:].split('\\')
    if len(parts) < 2 or any(
        not part or (index < 2 and part in {'.', '..'})
        or any(ord(c) < 32 or c in '<>":|?*' for c in part)
        for index, part in enumerate(parts)
    ):
        raise ValueError(tr('共享路径需包含有效的服务器名和共享文件夹名。'))
    return value


def normalize_drive(value):
    value = value.strip().upper().rstrip(':')
    if not re.fullmatch('[D-Z]', value):
        raise ValueError(tr('请选择 D: 到 Z: 的盘符。'))
    return value + ':'


def clean_records(records):
    if not isinstance(records, list) or len(records) > 500:
        raise ValueError(tr('连接列表格式错误，或超过 500 条。'))
    result, seen = [], set()
    for item in records:
        if not isinstance(item, dict):
            raise ValueError(tr('连接记录格式错误。'))
        path = normalize_share(str(item.get('path', '')))
        drive = normalize_drive(str(item.get('drive', 'Z:')))
        name = str(item.get('name', '')).strip()[:80]
        if path.casefold() not in seen:
            # Explicit allowlist: credentials can never enter import/export files.
            result.append({'path': path, 'drive': drive, 'name': name})
            seen.add(path.casefold())
    return result


def import_records(path):
    path = Path(path)
    if path.stat().st_size > 1024 * 1024:
        raise ValueError(tr('配置文件不能超过 1 MB。'))
    payload = json.loads(path.read_text('utf-8'))
    if not isinstance(payload, dict) or payload.get('schema') != 1:
        raise ValueError(tr('请选择景连导出的 schema=1 配置。'))
    return clean_records(payload.get('favorites'))


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Use a unique temporary file so concurrent instances cannot overwrite it.
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + '.', suffix='.tmp')
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def query_mappings():
    """WNetGetConnection includes remembered/unavailable mapped drives (1201)."""
    if os.name != 'nt':
        return []
    mpr = ctypes.WinDLL('mpr')
    get = mpr.WNetGetConnectionW
    get.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    get.restype = wintypes.DWORD
    rows = []
    for letter in 'DEFGHIJKLMNOPQRSTUVWXYZ':
        size = wintypes.DWORD(32768)
        remote = ctypes.create_unicode_buffer(size.value)
        code = get(letter + ':', remote, ctypes.byref(size))
        if code in (0, 1201) and remote.value:
            rows.append((letter + ':', remote.value, '已映射' if code == 0 else '暂不可用'))
        elif code not in (0, 1200, 1201, 2250):
            raise OSError(code, tr('读取 Windows 网络映射失败'))
    return rows


def available_drives():
    mask = ctypes.windll.kernel32.GetLogicalDrives() if os.name == 'nt' else 0
    return [f'{chr(i + 65)}:' for i in range(25, 2, -1) if not mask & (1 << i)]


def map_with_drive(mapper, remote, drive, user, password, persistent):
    """Return the drive Windows actually mapped. Retry automatic choices on drive collisions only."""
    if drive != AUTO_DRIVE:
        mapper(remote, drive, user, password, persistent)
        return drive
    for candidate in available_drives():
        try:
            mapper(remote, candidate, user, password, persistent)
        except RuntimeError as error:
            if getattr(error, 'winerror', None) not in (85, 1202):
                raise
        else:
            return candidate
    raise ValueError(tr('没有可用的 D: 到 Z: 盘符。请先断开一个不再使用的映射。'))


def edit_favorite_records(records, entry, previous=None):
    record, = clean_records([entry])
    previous_key = previous['path'].casefold() if previous is not None else None
    key = record['path'].casefold()
    keys = [item['path'].casefold() for item in records]
    if key in keys and key != previous_key:
        raise ValueError(tr('此地址已被收藏。'))
    if previous_key is None:
        return clean_records([*records, record])
    if previous_key not in keys:
        raise ValueError(tr('这条收藏已经移除，请重新添加。'))
    updated = list(records)
    updated[keys.index(previous_key)] = record
    return updated


def diagnose_share(remote):
    host = normalize_share(remote)[2:].split('\\', 1)[0]
    lines = [tr('目标：{0}', host), tr('协议：TCP 445')]
    start = time.monotonic()
    try:
        addresses = socket.getaddrinfo(host, 445, type=socket.SOCK_STREAM)
    except OSError as error:
        return False, '\n'.join([*lines, tr('地址解析：失败（{0}）', error),
                                 tr('请核对设备名或尝试共享端的局域网 IP。')])
    resolved = dict.fromkeys(info[4][0] for info in addresses)
    lines.append(tr('解析地址：') + '、'.join(resolved))
    deadline = time.monotonic() + 3
    last_error = None
    for family, kind, protocol, _, sockaddr in addresses:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            with socket.socket(family, kind, protocol) as connection:
                connection.settimeout(remaining)
                connection.connect(sockaddr)
        except OSError as error:
            last_error = error
        else:
            elapsed = round((time.monotonic() - start) * 1000)
            return True, '\n'.join([*lines, tr('连接成功：{0}，共 {1} ms', sockaddr[0], elapsed),
                                    tr('请打开完整共享路径验证账号和权限。')])
    return False, '\n'.join([*lines, tr('端口连接失败：{0}', last_error or tr('连接超时')),
                             tr('请检查共享端电源、SMB 服务、局域网或 VPN 及防火墙。')])


class ShareInfo1(ctypes.Structure):
    _fields_ = [('name', wintypes.LPWSTR), ('kind', wintypes.DWORD),
                ('remark', wintypes.LPWSTR)]


def local_share_paths():
    """Read visible disk shares on this computer through the Windows Server service."""
    if os.name != 'nt':
        raise OSError(tr('本机共享列表仅在 Windows 上可用。'))
    api = ctypes.WinDLL('netapi32')
    enumerate_shares = api.NetShareEnum
    dword_ptr = ctypes.POINTER(wintypes.DWORD)
    enumerate_shares.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD,
        dword_ptr, dword_ptr, dword_ptr,
    ]
    enumerate_shares.restype = wintypes.DWORD
    free = api.NetApiBufferFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = wintypes.DWORD
    resume = wintypes.DWORD()
    host = socket.gethostname()
    paths = set()
    while True:
        buffer = ctypes.c_void_p()
        read, total = wintypes.DWORD(), wintypes.DWORD()
        previous = resume.value
        status = enumerate_shares(None, 1, ctypes.byref(buffer), 0xFFFFFFFF,
                                  ctypes.byref(read), ctypes.byref(total), ctypes.byref(resume))
        try:
            if status not in (0, 234):
                raise OSError(status, tr('无法读取本机共享（Windows 错误 {0}）。', status))
            if read.value:
                shares = ctypes.cast(buffer, ctypes.POINTER(ShareInfo1))
                for item in shares[:read.value]:
                    name = item.name
                    if name and not name.endswith('$') and (item.kind & 0x800000FF) == 0:
                        paths.add(normalize_share(f'\\\\{host}\\{name}'))
        finally:
            if buffer.value:
                free(buffer)
        if status == 0:
            return sorted(paths, key=str.casefold)
        if resume.value == previous:
            raise OSError(tr('Windows 未能继续读取共享列表。'))
