from experiments.swebench_adapter import extract_diff


def test_extract_diff_from_python_fenced_block():
    response = """Here is the proposed fix:
```python
diff --git a/app.py b/app.py
index 1111111..2222222 100644
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-print("old")
+print("new")
```"""

    result = extract_diff(response)

    assert result.status == "VALID"
    assert "diff --git" in result.patch
    assert "print(\"new\")" in result.patch


def test_extract_diff_marks_truncated_responses():
    result = extract_diff("partial patch", finish_reason="length")

    assert result.status == "TRUNCATED"
    assert result.patch == ""
