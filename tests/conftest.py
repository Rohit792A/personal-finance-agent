# tests/conftest.py
"""Shared test setup.

app/ is the import root of the backend (Cloud Shell, Docker image and tests alike, DEC-025),
so tests import modules by their plain names (`import api`, `from security import ...`).
Importing the same file as both `app.x` and `x` would create two copies of each module.
"""
import os
import sys

APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
