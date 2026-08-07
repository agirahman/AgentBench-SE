"""Packaging for AgentBench-SE.

Install in editable/dev mode::

    pip install -e .

Provides the ``agentbench`` console entry point.
"""

from setuptools import find_packages, setup

setup(
    name="agentbench-se",
    version="0.1.0",
    description=(
        "Framework for AI Agent Orchestration Strategy Evaluation "
        "(interactive shell edition)"
    ),
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    author="Agi Rahman Setiadi",
    author_email="agi.rahman.s@gmail.com",
    url="https://github.com/agirahman/agentbench-se",
    packages=find_packages(exclude=("tests", "tests.*", "tools", "docs")),
    include_package_data=True,
    python_requires=">=3.10",
    install_requires=[
        "click>=8.1.0",
        "rich>=13.0.0",
        "pyyaml>=6.0.0",
        "textual>=0.80",
        "pyfiglet>=1.0",
        # existing core dependencies (see requirements.txt for the full set)
        "google-genai",
        "openai",
        "python-dotenv",
        "loguru",
        "pandas",
        "datasets",
    ],
    entry_points={
        "console_scripts": [
            "agentbench=agentbench.cli.main:cli",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.11",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
)