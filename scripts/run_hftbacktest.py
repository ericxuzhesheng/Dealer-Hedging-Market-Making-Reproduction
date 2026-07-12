"""Project entry point for the hftbacktest dealer-hedging reproduction."""
import runpy
from pathlib import Path
import sys

suite = Path(__file__).resolve().with_name("hftbacktest_suite.py")
sys.argv = [str(suite), "--project", "dealer"]
runpy.run_path(str(suite), run_name="__main__")
