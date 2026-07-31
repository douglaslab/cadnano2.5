from pathlib import Path
import warnings

from cadnano.document import Document
from cadnano.fileio.decode import decodeFile


DATA = Path(__file__).resolve().parent / 'data'
ROOT = Path(__file__).resolve().parents[2]


def test_c25_schema_is_detected_after_json_rename(tmp_path):
    renamed = tmp_path / 'octa-renamed.json'
    renamed.write_bytes((DATA / 'octa.13.c25').read_bytes())

    document = decodeFile(str(renamed), Document())

    assert document.activePart() is not None


def test_v3_dynamic_scamz_property_imports_without_nested_undo():
    paths = [
        DATA / 'simple.json',
        ROOT / 'misc' / 'examples' / '01_print_info' / 'design01.json',
    ]
    for path in paths:
        with warnings.catch_warnings():
            warnings.simplefilter('error', FutureWarning)
            document = decodeFile(str(path), Document())
        part = document.activePart()
        assert part is not None
        assert 'scamZ' in part.vh_properties.columns
        assert part.vh_properties['eulerZ'].dtype.kind == 'f'
