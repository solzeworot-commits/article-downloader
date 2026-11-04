"""Run app.py with one fake database instead of the real ones."""
import sys
from pathlib import Path

scripts = Path(sys.argv[1])
sys.path.insert(0, str(scripts))
sys.argv = [str(scripts / "app.py")] + sys.argv[2:]
import app

app.BASES[:] = [("fake", "Fake Publisher", "http://127.0.0.1:8000/home",
                 "http://127.0.0.1:8000/home", ("10.9999",))]
app.BASE_BY_KEY.clear()
app.BASE_BY_KEY.update({b[0]: b for b in app.BASES})
app.S.settings["bases"] = {"fake": True}
app.main()
