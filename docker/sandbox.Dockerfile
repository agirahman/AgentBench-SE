FROM python:3.9-slim

# Install ONLY pytest for test execution; NO target packages (django, sympy, matplotlib, sklearn, seaborn, requests, numpy)
RUN pip install --no-cache-dir pytest

# Create non-root sandbox user
RUN useradd -m -u 1000 -s /bin/bash sandbox

# Configure git identity for sandbox user in case git operations are run
RUN git_cfg() { \
        git config --global user.name "Sandbox Agent" && \
        git config --global user.email "agent@sandbox.local" && \
        git config --global --add safe.directory /work; \
    }; \
    if which git >/dev/null 2>&1; then git_cfg; fi

USER sandbox
WORKDIR /work
