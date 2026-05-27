# scripts/reorganize.py
"""Reorganize the stock_trader package into categorical subfolders.

- adapters/: broker adapters (mock_adapters.py)
- api/: external API wrappers (korea_investment_api.py)
- core/: core modules (broker_interface.py, broker_factory.py, config.py, etc.)
- tests/: test suite (already exists)
- scripts/: utility scripts (this file, temp_test_kis.py, ...)
"""
import os
import shutil
import glob

BASE = os.path.abspath(os.path.dirname(__file__))  # scripts folder
ROOT = os.path.abspath(os.path.join(BASE, ".."))

# Ensure target directories exist
for sub in ["adapters", "api", "core"]:
    os.makedirs(os.path.join(ROOT, sub), exist_ok=True)

# Move specific files
moves = {
    os.path.join(ROOT, "mock_adapters.py"): os.path.join(ROOT, "adapters", "mock_adapters.py"),
    os.path.join(ROOT, "korea_investment_api.py"): os.path.join(ROOT, "api", "korea_investment_api.py"),
}

for src, dst in moves.items():
    if os.path.exists(src):
        shutil.move(src, dst)
        print(f"Moved {os.path.basename(src)} -> {os.path.relpath(dst, ROOT)}")

# Move core Python files (exclude those already moved, tests, scripts, and __init__ if any)
exclude = set(moves.values())
exclude.update({os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests")})
for py_file in glob.glob(os.path.join(ROOT, "*.py")):
    if py_file in moves:
        continue
    if py_file.startswith(os.path.join(ROOT, "tests")):
        continue
    if py_file.startswith(os.path.join(ROOT, "scripts")):
        continue
    dst = os.path.join(ROOT, "core", os.path.basename(py_file))
    shutil.move(py_file, dst)
    print(f"Moved core {os.path.basename(py_file)}")

print("Reorganization complete.")
