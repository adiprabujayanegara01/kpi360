import re


def password_problem(pw):
    if len(pw or "") < 8:
        return "Password minimal 8 karakter."
    if not re.search(r"[a-z]", pw) or not re.search(r"[A-Z]", pw) or not re.search(r"\d", pw):
        return "Password harus memuat huruf besar, huruf kecil, dan angka."
    return None
