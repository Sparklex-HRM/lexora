"""Make the ops service dir + backend root importable for the pure-logic tests
(mirrors /app in the image). Run:  cd backend/services/ops && python -m pytest -q
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(os.path.dirname(_HERE))
for p in (_BACKEND, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
