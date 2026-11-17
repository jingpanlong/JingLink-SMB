from concurrent.futures import ThreadPoolExecutor
import ctypes
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core


@pytest.mark.parametrize(('typed', 'expected'), [
    (r'\\NAS\Public', r'\\NAS\Public'),
    ('“//NAS/Team Share/”', r'\\NAS\Team Share'),
    ('\ufeff"//NAS/Public/"\r\n', r'\\NAS\Public'),
    ('SMB://NAS/Team%20Share/%E5%9B%BE%E7%89%87', r'\\NAS\Team Share\图片'),
    ('smb://192.168.1.10/Public/', r'\\192.168.1.10\Public'),
])
def test_paste_path(typed, expected):
    assert core.normalize_share(typed) == expected


@pytest.mark.parametrize('typed', [
    'smb://bob:secret@NAS/Public', 'smb://NAS:445/Public', 'smb://NAS/Public?x=1',
    'smb://NAS/Public#stuff', 'smb://NAS/a%2fb', 'smb://NAS/a%5Cb',
    'smb://NAS/a%00b', 'smb://NAS/%FF', r'\\.\pipe\service',
    r'\\NAS\bad:name', 'smb://NAS//Public',
])
def test_reject_non_share_and_credentials(typed):
    with pytest.raises(ValueError):
        core.normalize_share(typed)


class WinMappingError(RuntimeError):
    def __init__(self, code):
        self.winerror = code


def test_auto_drive_retries_only_collision(monkeypatch):
    monkeypatch.setattr(core, 'available_drives', lambda: ['Z:', 'Y:', 'X:'])
    calls = []

    def mapper(remote, drive, user, password, persistent):
        calls.append((remote, drive, user, password, persistent))
        if drive == 'Z:':
            raise WinMappingError(85)
        if drive == 'Y:':
            raise WinMappingError(1202)

    assert core.map_with_drive(mapper, r'\\NAS\Public', core.AUTO_DRIVE, 'user', 'pw', True) == 'X:'
    assert calls == [(r'\\NAS\Public', drive, 'user', 'pw', True) for drive in ['Z:', 'Y:', 'X:']]


def test_connection_errors_abort_without_fallback(monkeypatch):
    monkeypatch.setattr(core, 'available_drives', lambda: ['Z:', 'Y:'])
    calls = []

    def mapper(_, drive, *_args):
        calls.append(drive)
        raise WinMappingError(1326)

    with pytest.raises(WinMappingError):
        core.map_with_drive(mapper, r'\\NAS\Public', core.AUTO_DRIVE, '', '', False)
    assert calls == ['Z:']
    calls.clear()
    with pytest.raises(WinMappingError):
        core.map_with_drive(mapper, r'\\NAS\Public', 'Y:', '', '', False)
    assert calls == ['Y:']


def test_no_free_drive(monkeypatch):
    monkeypatch.setattr(core, 'available_drives', lambda: [])
    with pytest.raises(ValueError, match='没有可用'):
        core.map_with_drive(None, r'\\NAS\Public', core.AUTO_DRIVE, '', '', False)


def test_create_and_edit_favorites_keeps_order_and_rejects_duplicate():
    old = [{'path': r'\\NAS\Public', 'drive': 'Z:', 'name': '资料'}]
    second = {'path': 'smb://NAS/Private', 'drive': 'Y', 'name': '新地址', 'password': 'private'}
    result = core.edit_favorite_records(old, second)
    assert result[1] == {'path': r'\\NAS\Private', 'drive': 'Y:', 'name': '新地址'}
    updated = core.edit_favorite_records(result, dict(old[0], name='办公'), old[0])
    assert [item['name'] for item in updated] == ['办公', '新地址']
    with pytest.raises(ValueError, match='已被收藏'):
        core.edit_favorite_records(result, dict(second, path=r'\\nas\public', drive='X:'))


def test_json_writes_leave_valid_json_with_concurrent_writers(tmp_path):
    dest = tmp_path / 'config.json'
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda index: core.atomic_json(dest, {'value': index}), range(20)))
    assert json.loads(dest.read_text())['value'] in range(20)
    assert list(tmp_path.iterdir()) == [dest]


def test_diagnostics_dns_failure(monkeypatch):
    def dns(*_args, **_kwargs):
        raise socket.gaierror('no address')

    monkeypatch.setattr(core.socket, 'getaddrinfo', dns)
    success, report = core.diagnose_share(r'\\NAS\Public')
    assert not success and '地址解析：失败' in report and 'TCP 445' in report


@pytest.mark.parametrize('reachable', [True, False])
def test_diagnostics_tcp(monkeypatch, reachable):
    sock = ('192.168.1.10', 445)
    monkeypatch.setattr(core.socket, 'getaddrinfo',
                        lambda *_a, **_k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', sock)])

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def settimeout(self, timeout):
            assert 0 < timeout <= 3

        def connect(self, target):
            assert target == sock
            if not reachable:
                raise TimeoutError('connection timed out')

    monkeypatch.setattr(core.socket, 'socket', lambda *_: Connection())
    success, report = core.diagnose_share(r'\\NAS\Private')
    assert success is reachable
    assert '解析地址：192.168.1.10' in report and 'Private' not in report
    assert ('账号和权限' in report) is reachable


def test_read_only_local_share_enumeration_handles_pages_and_frees_buffers(monkeypatch):
    first = (core.ShareInfo1 * 3)(
        core.ShareInfo1('Public', 0, 'one'), core.ShareInfo1('C$', 0x80000000, ''),
        core.ShareInfo1('Printer', 1, ''),
    )
    second = (core.ShareInfo1 * 1)(core.ShareInfo1('Photo Library', 0, 'two'))
    freed = []

    def enumerate_(host, level, output, limit, read, total, resume):
        assert host is None and level == 1 and limit == 0xFFFFFFFF
        page = first if not resume._obj.value else second
        output._obj.value = ctypes.addressof(page)
        read._obj.value = len(page)
        total._obj.value = 4
        if page is first:
            resume._obj.value = 1
            return 234
        return 0

    def free(buffer):
        freed.append(buffer.value)
        return 0

    monkeypatch.setattr(core, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(core.socket, 'gethostname', lambda: 'DESKTOP')
    monkeypatch.setattr(core.ctypes, 'WinDLL',
                        lambda name: SimpleNamespace(NetShareEnum=enumerate_, NetApiBufferFree=free), raising=False)
    assert core.local_share_paths() == [r'\\DESKTOP\Photo Library', r'\\DESKTOP\Public']
    assert freed == [ctypes.addressof(first), ctypes.addressof(second)]
