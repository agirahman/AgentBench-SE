You are an expert Python software engineer.

Your task is to fix the following bug.

{{issue}}

Analyze the root cause and provide a fix.

Output ONLY valid JSON in this exact format (no markdown, no extra text):
{
  "root_cause": "<one paragraph explanation>",
  "fix_strategy": "<one paragraph approach>",
  "patch": "diff --git a/file.py b/file.py\n--- a/file.py\n+++ b/file.py\n@@ ... @@\n ..."
}
