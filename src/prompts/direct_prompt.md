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

CRITICAL RULES FOR THE PATCH FIELD:
1. Every hunk header @@ -N,M +P,Q @@ MUST match the EXACT count of lines below it.
2. M = number of context lines + removed lines (for -N,M)
3. Q = number of context lines + added lines (for +P,Q)
4. Empty lines in the file count as context lines — they MUST appear with a leading space.
5. Count lines BEFORE writing the @@ header. Double-check your count.
6. Do NOT truncate. Include ALL context lines for each hunk until the change is complete.
7. COUNT PRECISELY: The @@ line tells you exactly how many lines must follow. Write the body first, count every line (context " ", added "+", removed "-"), THEN write the header numbers to match. Blank lines in the body are still one line each.
8. If you ADD one line with "+", Q increases by 1 but the surrounding context stays; if you REMOVE one line with "-", M increases by 1. The header count is the TOTAL lines in that hunk, not just the changed ones.
9. MISMATCHED COUNTS MAKE THE WHOLE PATCH INVALID. Before outputting, recount each hunk body and verify the @@ header numbers equal your count. Never emit a header you have not verified.

CRITICAL: Double-check the line counts in your Hunk Header (@@ -N,M +P,Q @@). You must ensure that the values of M and Q match exactly with the actual number of code lines you output below it. Do not miscount, as any discrepancy will break the automated evaluation system!
