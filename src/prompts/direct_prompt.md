You are an expert Python software engineer.

Your task is to fix the following bug.

{{issue}}

Analyze the root cause and provide a fix.

Output ONLY valid JSON. Do NOT wrap in markdown code blocks. Do NOT add any text before or after. Use this exact format:
{
  "root_cause": "<one paragraph explanation>",
  "fix_strategy": "<one paragraph approach>",
  "patch": "diff --git a/file.py b/file.py\n--- a/file.py\n+++ b/file.py\n@@ -N,M +P,Q @@\n context_line\n+added_line\n-removed_line"
}

IMPORTANT:
- The "patch" field must be a valid unified diff with correct hunk line counts.
- Every hunk header like @@ -N,M +P,Q @@ must exactly match the number of context, added, and removed lines that follow.
- Do NOT truncate the diff. Include ALL context lines for each hunk.
