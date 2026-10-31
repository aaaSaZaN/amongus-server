"""Server settings: environment variables first, then a .env file next to the code.

Docker and systemd pass the variables themselves. A plain run, which is how the
server starts on Windows, has nothing to do that, so the file is read here.
"""
import os

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def load(path=ENV_PATH):
    try:
        # utf-8-sig: Windows editors may add a byte order mark.
        with open(path, encoding="utf-8-sig") as file:
            lines = file.read().splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        name, separator, value = line.partition("=")
        if separator and not name.lstrip().startswith("#"):
            os.environ.setdefault(name.strip(), value.strip())


def text(name, default=None):
    return os.environ.get(name, default)


def number(name, default):
    return float(os.environ.get(name, default))


load()
