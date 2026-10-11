
import re


def extract_sql(text: str) -> str:
    text = text.strip()

    match = re.search(r"```(?:sql)?\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()

    return text
