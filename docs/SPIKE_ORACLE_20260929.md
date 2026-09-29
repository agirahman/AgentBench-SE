# Oracle Feasibility Spike — 2026-09-29

## Verdict

**NO-GO as a discriminating reviewer oracle under SWE-bench rules.** A local
pre-existing Django test run is technically feasible and comfortably below the
60-second invocation target, but it does not distinguish the known wrong and
right candidate patches. The nearest legitimate suites pass for both candidates
on `django__django-11001`; the narrower FilePathField suite also passes for both
until the broader nearby deconstruction test is included, where the wrong patch
fails for an unrelated migration/deconstruction regression while the right patch
passes. That failure is not the issue's newly introduced behavior and is not a
reliable oracle for the target bug.

No `test_patch`, `FAIL_TO_PASS`, `PASS_TO_PASS`, or dataset-provided hidden test
was opened, copied, executed, or used to choose a command. Only tests present in
the base-commit checkouts were selected by listing and inspecting their paths.
The candidate patches were applied only to scratch worktrees under
`.oracle_tmp/`, outside `datasets/`. The cached checkouts were not mutated by
this spike (they were already dirty from prior experiment artifacts; no files
were edited there).

## Instances and base commits

| Instance | Base commit | WRONG | RIGHT | Nearest pre-existing modules |
|---|---|---|---|---|
| `django__django-10924` | `bceadd2788dc2dad53eba0caae172bd8522fd483` | `patches/django__django-10924_review.txt` | `patches/django__django-10924_direct.txt` | `forms_tests.field_tests.test_filepathfield`; `field_deconstruction.tests.FieldDeconstructionTests`; `model_forms.tests.FileAndImageFieldTests` |
| `django__django-11001` | `ef082ebb84f00e38af4e8880d04e8365c2766d34` | `patches/django__django-11001_review.txt` | `patches/django__django-11001_planning.txt` | `ordering`; `queries` |

The module/file selection was verified from the base commit with commands such
as `git -C <scratch> ls-tree -r --name-only HEAD tests` and
`git -C <scratch> grep -n ... HEAD -- tests/...`. The issue candidate patches
were applied with source-only paths where needed; model-added test files from
the generated patches were intentionally not run or used.

## Exact commands that worked

PowerShell 5.1 commands (the repository is on Windows):

```powershell
# Read base commits from the local SWE-bench cache metadata. PowerShell 5.1
# has no here-document syntax, so pass a temporary stdin script:
$script = @'
from datasets import load_dataset
ids = {'django__django-10924', 'django__django-11001'}
for row in load_dataset('SWE-bench/SWE-bench_Lite', split='test'):
    if row['instance_id'] in ids:
        print(row['instance_id'], row['base_commit'])
'@
$script | .venv\Scripts\python.exe -

# Scratch copies/worktrees were made outside datasets/:
git -C datasets/repos/django/django/<base_commit> worktree add --detach .oracle_tmp/<case> <base_commit>

# Apply only candidate source/docs changes; never apply generated test files:
git -C .oracle_tmp/10924-wrong apply --include='django/**' --include='docs/**' results/EXP-20260928-003/patches/django__django-10924_review.txt
git -C .oracle_tmp/10924-right  apply --include='django/**' --include='docs/**' results/EXP-20260928-003/patches/django__django-10924_direct.txt
git -C .oracle_tmp/11001-wrong apply --include='django/**' results/EXP-20260928-003/patches/django__django-11001_review.txt
git -C .oracle_tmp/11001-right  apply --include='django/**' results/EXP-20260928-003/patches/django__django-11001_planning.txt

# The old Django 3.0 checkout needs pytz/sqlparse and a distutils shim on Python 3.12:
python -m venv .oracle_tmp/venv-time2
.oracle_tmp\venv-time2\Scripts\python.exe -m pip install --no-cache-dir setuptools==75.8.0 pytz==2025.2 sqlparse==0.5.3

# Run the nearest legitimate tests (candidate checkout is the current directory):
$env:PYTHONPATH=(Get-Location).Path
.oracle_tmp\venv-time2\Scripts\python.exe tests/runtests.py forms_tests.field_tests.test_filepathfield field_deconstruction.tests.FieldDeconstructionTests model_forms.tests.FileAndImageFieldTests --verbosity=0
.oracle_tmp\venv-time2\Scripts\python.exe tests/runtests.py ordering queries --verbosity=0
```

The stdin script above is the exact working PowerShell 5.1 form.

## Results and timings

### Setup / dependencies

- Environment that worked: **Windows `.venv`-derived isolated environment**,
  Python 3.12.3, with `setuptools==75.8.0`, `pytz==2025.2`, and
  `sqlparse==0.5.3` installed. `setuptools` supplies the `distutils` compatibility
  module required by this historical Django checkout.
- The project's existing `.venv` lacked Django, pytz, and sqlparse. The existing
  `.venv-linux` also lacked Django/pytz/sqlparse/pytest and WSL's `python3 -m
  venv` could not create a new environment because `ensurepip` was unavailable.
- Fresh Windows setup measurement: virtualenv creation **14.741 s**;
  dependency install **15.773 s**; total **30.514 s** in this machine/cache
  state. This is a one-time setup, not per reviewer invocation.
- Docker client reported version **28.3.2**, but the Docker daemon was not
  reachable (`dockerDesktopLinuxEngine` pipe missing), so Docker was not usable.

### Test execution

All invocations were non-interactive and used SQLite, the Django test runner,
and only base-commit test modules. Cold wall times include Python/test-runner
startup and were measured after dependency installation; warm times are the
second repeated invocation in the same isolated checkout.

| Candidate | Suite | Cold | Warm 1 | Warm 2 | Outcome |
|---|---|---:|---:|---:|---|
| 10924 WRONG | FilePathField + deconstruction + model forms, 53 tests | 2.491 s | 1.917 s | 1.908 s | **FAIL**: pre-existing `test_file_path_field` deconstruction assertion |
| 10924 RIGHT | same, 53 tests | 2.161 s | 1.859 s | 1.866 s | **PASS** |
| 11001 WRONG | `ordering` + `queries`, 387 tests | 2.957 s | 3.424 s | 2.602 s | **PASS** |
| 11001 RIGHT | same, 387 tests | 2.927 s | 3.449 s | 2.607 s | **PASS** |

The narrowest direct FilePathField module alone was also measured: **8 tests,
5.346 s cold** for WRONG and **4.954 s cold** for RIGHT; both passed. The
baseline before patch was **8 tests, 6.177 s cold**. Thus the apparent 10924
separation comes from a pre-existing deconstruction expectation conflicting with
the wrong patch's migration implementation, not from a new callable-path test.
The 11001 ordering/SQL suites show no separation at all.

The broader suites are still well under the 60-second target: approximately
2–3 seconds warm and under 3 seconds cold after interpreter/test-runner setup.
The first-ever invocation was approximately 5–6 seconds because Django's test
runner initialization and system checks dominate startup. No manual setup is
needed after the environment exists.

## Discrimination assessment

| Instance | WRONG | RIGHT | Discriminates? | Interpretation |
|---|---|---|---|---|
| 10924, narrow nearest FilePathField | PASS | PASS | **No** | Hidden/new callable behavior is absent from the base suite. |
| 10924, broader nearby suite | FAIL | PASS | **Misleading / not target-discriminating** | The wrong patch breaks an existing deconstruction contract; this is incidental regression evidence, not the issue's intended new behavior. |
| 11001, ordering + SQL | PASS | PASS | **No** | No pre-existing multiline-ordering test exists in the base commit. |

The official evaluation note explains that 10924's wrong patch fails the hidden
callable behavior because the callable is never evaluated at model-form
construction, while 11001 needs multiline regex handling. Those hidden tests
were not used here; this report relies only on the observed candidate behavior
and pre-existing suites. In thesis terms, a reviewer can cheaply obtain a
regression signal, but not a generally valid issue-resolution oracle from these
base tests.

## Generalization estimate

- **Django:** GO for local mechanics: a cached lightweight Python environment,
  SQLite, and targeted test labels fit far below 60 seconds. However, the oracle
  is still NO-GO for reliable discrimination when the regression test is newly
  added by the benchmark issue, as demonstrated by 11001 and the narrow 10924
  run.
- **SymPy:** likely technically feasible for targeted pure-Python tests, but
  dependency installation and import/startup are heavier; some optional numeric
  dependencies and test discovery can push cold setup/runtime upward. Expect a
  cached environment to be necessary; do not assume the 2–3 second Django
  numbers generalize.
- **scikit-learn:** less favorable. NumPy/SciPy/scikit-learn and compiled wheels
  make installation larger and cold startup materially slower; targeted tests
  may still fit 60 seconds only with prebuilt/cached dependencies. Full or
  broad suites are unsuitable inside every agent turn.
- **matplotlib:** less favorable still for setup: NumPy, font/rendering
  dependencies, and compiled/native components increase installation and test
  variance. Headless targeted tests may fit after caching, but a clean no-manual-
  setup oracle is not dependable across hosts.

These are engineering estimates, not benchmark measurements from this spike.

## Reproducibility / cleanup

The experiment did not modify `src/`, `tests/`, `tools/`, `.env`, or the
`datasets/repos/**` main checkouts. The candidate worktrees and temporary
virtual environments are under `.oracle_tmp/` and should be removed after this
report. No commit was created.
