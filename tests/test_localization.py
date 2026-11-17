import ast
from pathlib import Path
from string import Formatter
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import normalize_share
from localization import ENGLISH, get_locale, set_locale, tr


@pytest.fixture(autouse=True)
def preserve_locale():
    original = get_locale()
    yield
    set_locale(original)


def test_chinese_remains_the_default_for_old_and_invalid_configurations():
    for old_setting in (None, "", "unknown", {}, "zh"):
        assert set_locale(old_setting) == "zh"
        assert tr("设置") == "设置"


def test_english_labels_and_error_messages():
    set_locale("en")
    assert tr("设置") == "Settings"
    assert tr("已连接  {0} → {1}", "Z:", r"\\NAS\Team") == r"Connected  Z: → \\NAS\Team"
    with pytest.raises(ValueError, match="Enter the full share path"):
        normalize_share("NAS")
    assert normalize_share("smb://NAS/Team") == r"\\NAS\Team"


def test_catalog_covers_ui_strings_and_preserves_placeholders():
    formatter = Formatter()
    for chinese, english in ENGLISH.items():
        assert [name for _, name, _, _ in formatter.parse(chinese) if name is not None] == [
            name for _, name, _, _ in formatter.parse(english) if name is not None
        ]
    for name in ("smb-connect.py", "studio.py", "core.py"):
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
        for call in ast.walk(tree):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id == "tr" and isinstance(call.args[0], ast.Constant)):
                assert call.args[0].value in ENGLISH, (name, call.lineno, call.args[0].value)
