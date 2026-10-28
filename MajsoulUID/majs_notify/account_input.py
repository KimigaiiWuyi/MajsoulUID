def parse_account_password(text: str) -> tuple[str, str] | None:
    """「用户名, 密码」「用户名 密码」「用户名，密码」都认；拆出来不是两段就返回 None。"""
    parts = text.replace("，", ",").replace(",", " ").split()
    if len(parts) != 2:
        return None
    return parts[0], parts[1]
