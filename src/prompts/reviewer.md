You are a code reviewer evaluating a bug fix.

Bug:
{{issue}}

Plan:
{{plan}}

Proposed Patch:
{{patch}}

Review the patch and answer. Output ONLY valid JSON in this exact format:
{
  "review_summary": "<evaluation>",
  "issues_found": ["<issue or 'None'>"],
  "improvement_suggestions": ["<suggestion or 'None'>"],
  "verdict": "APPROVED|NEEDS_REVISION"
}
