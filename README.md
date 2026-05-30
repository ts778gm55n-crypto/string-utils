# string-utils

A small collection of Python string utility functions.

## Functions

- `reverse(s)` — reverses a string
- `is_palindrome(s)` — checks whether a string is a palindrome
- `word_count(s)` — counts the number of words in a string

## Usage

```python
from string_utils import reverse, is_palindrome, word_count

reverse("hello")        # "olleh"
is_palindrome("racecar") # True
word_count("hello world") # 2
```

## Running tests

```bash
python -m pytest tests/
```
