"""Tests for targeted instance selection (--instance-ids).

Exists so a measured failure can be re-run on its own — the django-11001 case,
where review's revision act was granted 1 turn and the reviewer's correct
diagnosis could not be applied. Without this, verifying a budget fix would mean
re-running the whole suite.
"""
import pytest

from dataset_loader import select_issues


class TestSelectById:
    def test_single_instance(self):
        issues = select_issues(instance_ids=["django__django-11001"])
        assert [i.instance_id for i in issues] == ["django__django-11001"]
        issue = issues[0]
        assert issue.repo == "django/django"
        assert issue.base_commit
        assert issue.problem_statement

    def test_multiple_instances_keep_dataset_order(self):
        """Order must not depend on the order the ids were typed.

        A subset has to line up with the same run's full-suite output, otherwise
        comparing the two is confusing for no reason.
        """
        forward = select_issues(instance_ids=["django__django-11283", "django__django-10914"])
        backward = select_issues(instance_ids=["django__django-10914", "django__django-11283"])
        assert [i.instance_id for i in forward] == [i.instance_id for i in backward]

    def test_duplicate_ids_are_deduped(self):
        issues = select_issues(
            instance_ids=["django__django-11001", "django__django-11001"]
        )
        assert len(issues) == 1

    def test_unknown_id_raises_rather_than_silently_running_nothing(self):
        """A typo must fail loudly.

        Silently returning an empty list would produce a "successful" run that
        measured nothing — the worst possible outcome for an experiment.
        """
        with pytest.raises(ValueError, match="unknown instance_id"):
            select_issues(instance_ids=["django__django-99999"])

    def test_single_underscore_typo_is_caught(self):
        """SWE-bench ids use DOUBLE underscores; the common typo must not pass."""
        with pytest.raises(ValueError, match="unknown instance_id"):
            select_issues(instance_ids=["django_django-11001"])

    def test_ids_win_over_repo_specs(self):
        """Both given: the ids decide, so the command cannot measure a different set."""
        issues = select_issues(
            [("django/django", 10)], instance_ids=["django__django-11001"]
        )
        assert [i.instance_id for i in issues] == ["django__django-11001"]

    def test_none_falls_back_to_repo_specs(self):
        """The default path must be untouched by the new parameter."""
        issues = select_issues([("django/django", 3)])
        assert len(issues) == 3
        assert all(i.repo == "django/django" for i in issues)
