import re


def extract_diff(response: str) -> str:
    for pattern in [r"```diff\n(.*?)```", r"```patch\n(.*?)```", r"```\n(.*?)```"]:
        match = re.search(pattern, response, re.DOTALL)
        if match:
            return match.group(1).strip()
    return response.strip()
