import argparse

from main import parse_args


def test_parse_args_accepts_repo_spec_override(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "main.py",
            "--provider",
            "openrouter",
            "--issues",
            "3",
            "--repo-spec",
            "django/django=2",
            "--repo-spec",
            "psf/requests=1",
        ],
    )

    args = parse_args()

    assert args.provider == "openrouter"
    assert args.issues == 3
    assert args.repo_spec == ["django/django=2", "psf/requests=1"]
