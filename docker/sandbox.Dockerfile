# Sandbox image: the agent may WORK here (install deps, run tests), but the
# evaluation-relevant channels stay closed.
#
# WHAT CHANGED (2026-10-08, operator decision):
#   Before: pytest only, no target packages. Every test run died on
#   `ModuleNotFoundError`, so `run_tests` was a dead end and the agent could only
#   reason about the code. Measured on the current image (6 repos):
#     django      MISSING_MODULE(django)
#     sympy       MISSING_MODULE(mpmath)
#     matplotlib  MISSING_MODULE(numpy)
#     seaborn     MISSING_MODULE(numpy)
#     requests    RAN (its deps are vendored)
#     sklearn     MISSING_MODULE(sklearn.__check_build._check_build)
#   Now: dependencies are pre-installed, so a test run is a real regression
#   signal. It is still NOT an oracle -- the answer-bearing test lines are absent
#   from the checkouts (verified: django-10914 missing 1 assert, django-10924
#   missing the whole test file, django-11001 missing 17 lines), so running the
#   suite cannot reveal the expected fix.
#
# WHAT MUST NOT LEAK (unchanged, and probed by tests/test_docker_sandbox.py):
#   1. git history -- `git` is NOT installed and /work/.git is masked with --tmpfs
#   2. other strategies' patches -- results/ is outside the mounted checkout
#   3. API keys / .env -- not mounted, and symlink escape is refused
#
# WHY NOTHING FROM PyPI FOR THE SIX TARGET PACKAGES: pre-installing `django`
# would put a RELEASED Django in site-packages, and a released version is a later
# revision than the base commit -- i.e. it CONTAINS the fix the agent is asked to
# write. That is the oracle this design refuses. The target package must come from
# the checkout itself (`pip install -e .`), so the only source the agent can read
# is the code it is already editing. Dependencies are different: numpy does not
# contain Django's fix, so pre-installing it leaks nothing.

FROM python:3.9-slim

# --- build toolchain -------------------------------------------------------
# matplotlib and scikit-learn compile C extensions during `pip install -e .`.
# Measured: without gcc/make/freetype headers, matplotlib failed with
# `ImportError: cannot import name '_c_internal_utils'` -- its build never ran.
# NOTE: git is deliberately NOT in this list. Its absence is load-bearing for the
# history-leak defence (see tests/test_docker_sandbox.py::test_git_binary_is_absent_by_design).
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        make \
        pkg-config \
        python3-dev \
        libfreetype6-dev \
        libpng-dev \
        patch \
        diffutils \
    && rm -rf /var/lib/apt/lists/*

# --- dependency set --------------------------------------------------------
# The union of tests/requirements/py3.txt + setup.py install_requires across the
# six repos, MEASURED from the real checkouts (logs/_DEPS_MEASURED.json), minus
# the six target packages themselves.
#
# Pins that are load-bearing, each from a measured failure:
#   setuptools<60   two independent reasons, both measured:
#                     * matplotlib turns warnings into errors, and newer
#                       setuptools emits a DeprecationWarning from pkg_resources
#                       at import time (mpl_toolkits calls declare_namespace), so
#                       collection aborts: "Interrupted: 1 error during collection"
#                     * the old sklearn/matplotlib setups import numpy.distutils,
#                       which imports distutils.msvccompiler -- removed in
#                       setuptools>=60 ("No module named 'distutils.msvccompiler'")
#   pyparsing<3     old matplotlib + new pyparsing raises PyparsingDeprecationWarning
#                   during collection -> "Interrupted: 1 error during collection"
#   Cython<3        the 2018-2019 .pyx sources fail to cythonize under Cython 3
#   numpy<1.24      numpy.distutils (used by the old sklearn/matplotlib setups) was
#                   removed after 1.23
#   setuptools_scm  matplotlib derives its version from git metadata; with .git
#                   masked the build needs the fallback this package provides
RUN pip install --no-cache-dir --disable-pip-version-check \
        "setuptools<60" \
        wheel \
        pytest \
        pytest-mock \
        pytest-httpbin \
        httpbin \
        flask \
        werkzeug \
        pytz \
        sqlparse \
        mpmath \
        numpy==1.23.5 \
        pandas \
        scipy \
        pillow \
        packaging \
        contourpy \
        cycler \
        "pyparsing<3" \
        kiwisolver \
        python-dateutil \
        setuptools_scm \
        joblib \
        "Cython<3" \
        threadpoolctl \
        pyyaml \
        jinja2 \
        docutils \
        tblib \
    && rm -rf /root/.cache/pip

# --- wheelhouse ------------------------------------------------------------
# For anything NOT pre-installed. The container runs --network=none, so
# `pip install` must resolve entirely from here:
#     pip install --no-index --find-links=/opt/wheelhouse <pkg>
# Built at IMAGE BUILD time, when the network is still available.
#
# NO TARGET PACKAGE MAY APPEAR HERE. A wheel is a ZIP: `zipfile` reads it with no
# install, no network and no checkout, so a wheel in this directory is readable
# source. Measured on the previous image, which shipped matplotlib-3.5.3:
#     * 10/50 instances had their grading-test lines readable from the wheel
#     * 4/50 were a COMPLETE oracle (100% of the test_patch's added lines)
#     * 4/10 matplotlib instances had the FIX ITSELF readable from the wheel's
#       non-test modules (e.g. widgets.py, 12/12 added lines)
# matplotlib is one of the six repos under test, and 3.5.3 is a LATER release than
# every base commit in the dataset, so it contains the fixes being asked for.
#
# The obvious alternative -- pre-install matplotlib into site-packages instead --
# does NOT work, and was measured rather than assumed: the wheel ships 96 test
# files, so `pip install` would put the grading tests in site-packages where plain
# `cat` reads them, and the fix-bearing main modules come along too. The only way
# a released matplotlib stops being an oracle is for it not to be in the image.
#
# COST, stated plainly: seaborn's 4 instances declare matplotlib as a dependency,
# so their conftest cannot import and their suite will not run. That returns them
# to the pre-2026-10-08 behaviour ("deps unavailable") for those 4 of 50. The other
# 46 instances -- including all 10 matplotlib ones, which import matplotlib from
# the CHECKOUT via PYTHONPATH -- are unaffected; measured: matplotlib's own suite
# runs 11 passed with this directory emptied.
RUN mkdir -p /opt/wheelhouse \
    && pip download --no-cache-dir --disable-pip-version-check \
         --dest /opt/wheelhouse \
         "pyparsing<3" \
         "Cython<3" \
         "setuptools<60" \
         wheel \
         six \
         decorator \
         itsdangerous \
         markupsafe \
         babel \
         snowballstemmer \
         alabaster \
         imagesize \
         coverage \
         invoke \
         nose \
    && chmod -R a+r /opt/wheelhouse \
    && python -c "import glob,os,sys; T=('django','sympy','matplotlib','seaborn','sklearn','scikit-learn','scikit_learn','requests'); bad=[os.path.basename(w) for w in glob.glob('/opt/wheelhouse/*') if any(os.path.basename(w).lower().startswith(t) for t in T)]; print('FATAL: target package(s) in the wheelhouse: ' + ', '.join(bad) + ' -- a wheel is a ZIP, so its source is readable without installing it', file=sys.stderr) if bad else print('wheelhouse OK: ' + str(len(glob.glob('/opt/wheelhouse/*'))) + ' files, no target package'); sys.exit(1 if bad else 0)"

# --- offline pip config ----------------------------------------------------
# Makes a bare `pip install <pkg>` work without the agent having to remember
# --no-index/--find-links, and makes an accidental network install fail loudly
# instead of hanging until the 180s timeout.
RUN printf '[global]\nno-index = true\nfind-links = /opt/wheelhouse\n' \
        > /etc/pip.conf

# --- strip the pip-vendored oracle -------------------------------------------
# WHY THIS IS NECESSARY, not paranoia. pip vendors a full copy of `requests`
# (pip/_vendor/requests/sessions.py). A site-packages tree is readable source,
# just like a wheel is a ZIP: the vendored copy carries the fix for any instance
# whose base commit is an OLDER requests. Measured on psf__requests-1963 (base
# requests 2.3.0, vendored copy 2.28.2): the 2-line gold fix is 100% readable
# from the vendored file and 0% from the checkout. That is a complete code
# oracle, readable with plain `grep`.
#
# WHY NOT JUST DELETE IT: measured, `rm -rf` breaks pip at startup
# (ModuleNotFoundError: pip._vendor.requests, imported by pip._internal).
# The replacement in docker/pip_vendor_stub/ is a SOURCE-FREE stand-in: it keeps
# the import surface pip needs (Session subclass, exceptions, adapters) and none
# of the real implementation. Safe because the sandbox runs --network=none, so
# pip's HTTP path is never exercised; only the class hierarchy must work.
RUN rm -rf /usr/local/lib/python3.9/site-packages/pip/_vendor/requests \
    && mkdir -p /usr/local/lib/python3.9/site-packages/pip/_vendor/requests
COPY docker/pip_vendor_stub/requests/ /usr/local/lib/python3.9/site-packages/pip/_vendor/requests/
RUN ls /usr/local/lib/python3.9/site-packages/pip/_vendor/requests/

# --- build-time gate: the stub must stay source-free ---------------------------
# The stub is what keeps the oracle closed, so its CONTENT is load-bearing and is
# checked by RUNNING the scanner, not by trusting an edit. The scanner is shared
# with tests/test_docker_sandbox.py, which runs it against the stub SOURCE on the
# host -- so a bad edit fails the suite even with no daemon, and this gate covers
# the image even when the suite is not run.
#
# It scans EVERY .py in the installed stub (not just __init__.py) and requires the
# gold fix as a CONTIGUOUS PAIR: a mutation that restores `# Override the original
# request.` + `req = prepared_request` into any stub file -- measured on
# session.py, which the previous single-file check did not look at -- fails here.
# A lone mention is tolerated: the stub legitimately explains the oracle it replaces.
#
# NOTE: the stub itself must never quote the oracle lines as a PAIR. (Hit once: the
# stub's docstring quoted both gold lines verbatim.)
COPY docker/scan_pip_vendor_stub.py /tmp/scan_pip_vendor_stub.py
RUN python /tmp/scan_pip_vendor_stub.py /usr/local/lib/python3.9/site-packages/pip/_vendor/requests

# --- the SAME oracle inside ensurepip's bundled wheel --------------------------
# WHY THIS IS NEEDED, and why stripping site-packages was not enough. A wheel is
# a ZIP, so it is readable source: `zipfile` reads it with no install, no network
# and no checkout. The base image ships a complete pip wheel through ensurepip,
# and MEASURED it carried the SAME gold fix as the vendored copy above:
#     /usr/local/lib/python3.9/ensurepip/_bundled/pip-23.0.1-py3-none-any.whl
#       -> pip/_vendor/requests/sessions.py:259  "# Override the original request."
#                                               "req = prepared_request"
# Scanning the whole filesystem for what the AGENT CAN READ (not what we added)
# is what surfaced it -- the site-packages fix left this path untouched.
#
# WHY NOT DELETE THE WHEEL: measured as root, moving it away breaks venv creation
# (`python -m venv` FAILED; `python -m ensurepip --default-pip` raised
# FileNotFoundError for that exact path). ensurepip needs its bundled wheel, so
# the archive stays and only its vendored requests is replaced -- with the same
# source-free stub, and with RECORD recomputed so the wheel stays well-formed.
COPY docker/strip_ensurepip_oracle.py /tmp/strip_ensurepip_oracle.py
RUN python /tmp/strip_ensurepip_oracle.py \
        /usr/local/lib/python3.9/ensurepip/_bundled/pip-23.0.1-py3-none-any.whl \
        /usr/local/lib/python3.9/site-packages/pip/_vendor/requests

# Gate: the scanner opens ARCHIVES too, so the wheel is checked rather than
# trusted. If the stripper ever no-ops (wheel renamed, layout changed), this fails
# the build instead of silently restoring the oracle.
#
# ONE TARGET PER RUN, deliberately. An earlier version passed BOTH paths here, and
# that made the second run cover for the first: measured (reviewer, final audit),
# redirecting the site-packages RUN to another path left the suite GREEN, because
# this run still mentioned site-packages. With each location scanned by its own RUN,
# the pinned property ("every mandatory target is covered by some RUN") is exactly
# the security property -- no run can silently stop covering a location while
# another one masks the gap.
RUN python /tmp/scan_pip_vendor_stub.py \
        /usr/local/lib/python3.9/ensurepip/_bundled/pip-23.0.1-py3-none-any.whl

# --- import path -----------------------------------------------------------
# WHY THIS IS NECESSARY, not a convenience. `pip install -e .` does NOT reliably
# work against the mounted checkout, and the failure is a property of the mount,
# not of the packages: measured on django,
#     error: Cannot update time stamp of directory 'Django.egg-info'
# The bind mount is a Windows filesystem, which does not let the build set a
# directory's mtime, so the editable install aborts before writing its .pth.
# Consequence WITHOUT this line: `python -c "import django"` succeeds from /work,
# but `cd tests && python runtests.py` (the command _guess_test_command emits, and
# Django's own documented invocation) fails with
#     ModuleNotFoundError: No module named 'django'
# because moving to tests/ leaves /work off sys.path. Measured both ways.
#
# WHY IT IS SAFE (not an oracle): /work is the CHECKOUT the agent is editing --
# the base-commit source. Putting it on the import path exposes exactly the code
# the agent can already read and write. No released copy of ANY of the six target
# packages exists in this image (not in site-packages, not in the wheelhouse), so
# there is nothing later-revision for it to shadow or to read. For the five repos
# whose package sits at the root this is also what makes the target importable at
# all; matplotlib keeps its package under lib/, added below.
#
# /work/lib is appended for matplotlib, the ONE repo that keeps its package there
# (measured: lib/ exists in matplotlib only; the other five have no lib/ at all).
# Without it, `python -m pytest lib/matplotlib/tests/...` cannot import matplotlib
# unless the agent discovers and passes PYTHONPATH itself. Appended rather than
# prepended so /work still wins if both ever match.
ENV PYTHONPATH=/work:/work/lib

# --- non-root sandbox user -------------------------------------------------
RUN useradd -m -u 1000 -s /bin/bash sandbox

# No git identity is configured: git is absent from this image by design, and a
# `git config` block would silently become live if git were ever added back.
# tests/test_docker_sandbox.py pins the absence.

USER sandbox
WORKDIR /work
