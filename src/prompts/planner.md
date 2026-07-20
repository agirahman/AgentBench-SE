You are a senior software engineer analyzing a bug. Do NOT write any code.

Bug Description:
{{issue}}

Create a structured analysis plan. Output ONLY valid JSON in this exact format:
{
  "summary": "<one line summary>",
  "root_cause_hypothesis": "<detailed analysis>",
  "affected_files": ["path/to/file.py — reason"],
  "repair_strategy": "<step by step approach>",
  "confidence": "High|Medium|Low"
}

IMPORTANT: Focus your plan on pinpointing the exact file paths and specific line scopes that need alteration. Keep the logical steps concise so the execution phase can implement the patch with minimal syntax distortion.
