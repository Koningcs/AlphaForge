from pathlib import Path
import runpy
import sys

SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))
runpy.run_path(str(SKILL_DIR / "run_guba_sentiment.py"), run_name="__main__")
