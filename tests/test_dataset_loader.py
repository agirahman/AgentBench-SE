import dataset_loader
from models.issue import Issue


def test_normalize_repo_specs_accepts_mapping():
    specs = dataset_loader.normalize_repo_specs({"django/django": 2, "psf/requests": 1})

    assert specs == [("django/django", 2), ("psf/requests", 1)]


def test_select_issues_uses_normalized_repo_specs(monkeypatch):
    rows = [
        {
            "instance_id": "django-1",
            "repo": "django/django",
            "base_commit": "abc",
            "problem_statement": "Problem 1",
            "hints_text": "",
        },
        {
            "instance_id": "requests-1",
            "repo": "psf/requests",
            "base_commit": "abc",
            "problem_statement": "Problem 2",
            "hints_text": "",
        },
    ]
    monkeypatch.setattr(dataset_loader, "load_swe_bench_lite", lambda: rows)

    issues = dataset_loader.select_issues({"django/django": 1, "psf/requests": 1})

    assert len(issues) == 2
    assert isinstance(issues[0], Issue)
    assert issues[0].difficulty == "hard"
    assert issues[1].difficulty == "easy"
    assert [issue.instance_id for issue in issues] == ["django-1", "requests-1"]
