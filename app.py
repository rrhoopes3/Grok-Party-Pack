"""Launch the Party Pack from this folder.

    python app.py

BattleChess is http://127.0.0.1:5000/battlechess/
"""
import os
import runpy
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
runpy.run_path(os.path.join(ROOT, "forge", "app.py"), run_name="__main__")
