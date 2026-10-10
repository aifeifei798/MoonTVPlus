"""Print the raw VALUE of the auth cookie for the running server.

Usage: backend/.venv/bin/python <skill>/scripts/login_cookie.py > /tmp/opencode/auth.txt

Pass it to the CDP harnesses as argv[2] (they set name='auth' themselves), e.g.
$(cat /tmp/opencode/auth.txt). Never prefix another `auth=`.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from _harness import auth_cookie

print(auth_cookie())
