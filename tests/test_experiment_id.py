from pathlib import Path

from experiment_id import generate_experiment_id, create_experiment_dir, INDEX_FILE
import json
import tempfile


class TestExperimentId:
    def test_format_matches_expected(self):
        eid = generate_experiment_id(index_file=Path(tempfile.mktemp()))
        # EXP-YYYYMMDD-NNN
        parts = eid.split("-")
        assert len(parts) == 3
        assert parts[0] == "EXP"
        assert len(parts[1]) == 8
        assert parts[1].isdigit()
        assert len(parts[2]) == 3
        assert parts[2].isdigit()

    def test_counter_increments_per_day(self):
        tmp = Path(tempfile.mktemp())
        eid1 = generate_experiment_id(index_file=tmp)
        eid2 = generate_experiment_id(index_file=tmp)
        assert eid1 != eid2  # same day, counter increments
        # counter is stored, so eid1 counter < eid2 counter

    def test_create_experiment_dir_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            eid = "EXP-20260716-001"
            exp_dir = create_experiment_dir(tmp, eid)
            assert exp_dir.exists()
            assert (exp_dir / "artifacts").exists()
            assert (exp_dir / "logs").exists()

    def test_index_persists(self):
        tmp = Path(tempfile.mktemp())
        _ = generate_experiment_id(index_file=tmp)
        _ = generate_experiment_id(index_file=tmp)
        data = json.loads(tmp.read_text())
        today = list(data.keys())[0]
        assert data[today] == 2
