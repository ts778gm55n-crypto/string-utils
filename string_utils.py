def reverse(s):
    return s[::-1]


def is_palindrome(s):
    cleaned = s.lower().replace(" ", "")
    return cleaned == cleaned[::-1]


def word_count(s):
    if not s.strip():
        return 0
    return len(s.split())
