"""Die Migrationskette muss linear sein (genau ein Head) und Verzweigungen verständlich melden."""

import shutil
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.main import MigrationError, _assert_single_head

BACKEND = Path(__file__).resolve().parent.parent


def _script_dir(root: Path) -> ScriptDirectory:
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    return ScriptDirectory.from_config(cfg)


def test_repository_has_exactly_one_head():
    sd = _script_dir(BACKEND)
    assert len(sd.get_heads()) == 1
    assert _assert_single_head(sd) == sd.get_current_head()


def test_branched_chain_is_reported_with_file_names(tmp_path):
    shutil.copytree(BACKEND / "alembic", tmp_path / "alembic", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(BACKEND / "alembic.ini", tmp_path / "alembic.ini")
    (tmp_path / "alembic" / "versions" / "0002_stale.py").write_text(
        'revision = "0002_stale"\ndown_revision = "0001_initial"\nbranch_labels = None\ndepends_on = None\n'
        "def upgrade():\n    pass\n\ndef downgrade():\n    pass\n"
    )
    sd = _script_dir(tmp_path)
    assert len(sd.get_heads()) == 2
    with pytest.raises(MigrationError) as exc:
        _assert_single_head(sd)
    msg = str(exc.value)
    assert "2 Heads" in msg and "0002_stale" in msg and "0002_stale.py" in msg and "0013_users" in msg
