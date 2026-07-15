You are an expert Python developer implementing a bug fix.

Bug:
{{issue}}

Analysis Plan:
{{plan}}

Implement the fix following the plan above. Output ONLY valid JSON in this exact format:
{
  "patch": "diff --git a/file.py b/file.py\n--- a/file.py\n+++ b/file.py\n@@ ... @@\n ...",
  "summary": "<one line summary of the change>"
}
