import json
import re


def extract_diff(response: str) -> str:
    """Ambil diff/patch dari response LLM.

    Prioritas (Kritik #5): coba parse JSON terlebih dahulu (prompt dipaksa
    menghasilkan format terstruktur), lalu fallback ke markdown code block,
    terakhir kembalikan response mentah.
    """
    if not response:
        return ""

    # 1. Coba JSON (field "patch")
    try:
        data = json.loads(response)
        if isinstance(data, dict) and "patch" in data:
            return str(data["patch"]).strip()
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. Fallback: markdown code block
    for pattern in [r"```diff\n(.*?)```", r"```patch\n(.*?)```", r"```\n(.*?)```"]:
        match = re.search(pattern, response, re.DOTALL)
        if match:
            return match.group(1).strip()

    # 3. Last resort: raw text
    return response.strip()
