import re


class PrivacySanitizer:
    """隐私信息脱敏处理类。"""
    patterns = [
        re.compile(r"1[3-9]\d{9}"),
        re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
        re.compile(r"\b\d{17}[\dXx]\b"),
    ]

    def sanitize(self, text: str) -> str:
        """对文本中的敏感信息进行脱敏替换。"""
        sanitized = text or ""
        for pattern in self.patterns:
            sanitized = pattern.sub("[已脱敏]", sanitized)
        return sanitized
