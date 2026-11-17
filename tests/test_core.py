import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import normalize_share, normalize_drive, clean_records, import_records, atomic_json

@pytest.mark.parametrize('value',['NAS','\\\\NAS','\\\\NAS\\','\\\\NAS\\bad*','\\\\NAS\\a\nb','\\\\NAS\\\\Public'])
def test_invalid_unc(value):
    with pytest.raises(ValueError):normalize_share(value)

def test_normalize():
    assert normalize_share(' //NAS/Public/ ') == '\\\\NAS\\Public'
    assert normalize_drive('z') == 'Z:'

@pytest.mark.parametrize('value',['C:', 'A', '磁:', 'ZZ:', '1:', '', 'D:\\'])
def test_invalid_drive(value):
    with pytest.raises(ValueError):normalize_drive(value)

def test_export_strips_credentials_and_deduplicates():
    items=[{'path':'\\\\NAS\\Public','drive':'Z:', 'password':'secret','user':'private'}, {'path':'\\\\nas\\public','drive':'Y:'}]
    assert clean_records(items)==[{'path':'\\\\NAS\\Public','drive':'Z:','name':''}]

def test_import_and_atomic_write(tmp_path):
    path=tmp_path/'config.json'
    data={'schema':1,'favorites':[{'path':'\\\\NAS\\Public','drive':'Z:'}]}
    atomic_json(path,data)
    assert import_records(path)[0]['drive']=='Z:'
    assert not path.with_suffix('.json.tmp').exists()
    atomic_json(path,dict(schema=9,favorites=[]))
    with pytest.raises(ValueError):import_records(path)

def test_malformed_import(tmp_path):
    p=tmp_path/'bad.json';p.write_text('{')
    with pytest.raises(ValueError):import_records(p)

def test_oversize_import(tmp_path):
    p=tmp_path/'big.json';p.write_bytes(b' '*1048577)
    with pytest.raises(ValueError):import_records(p)
