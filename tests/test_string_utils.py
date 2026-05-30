from string_utils import reverse, is_palindrome, word_count


def test_reverse():
    assert reverse("hello") == "olleh"
    assert reverse("") == ""
    assert reverse("a") == "a"


def test_is_palindrome():
    assert is_palindrome("racecar") is True
    assert is_palindrome("hello") is False
    assert is_palindrome("A man a plan a canal Panama") is True


def test_word_count():
    assert word_count("hello world") == 2
    assert word_count("") == 0
    assert word_count("  spaces  ") == 1
