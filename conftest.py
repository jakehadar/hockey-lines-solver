# Neither solver/ nor studio/ is a package - both are flat directories whose
# files are imported as top-level modules (mirroring how each is actually
# run in production: `cd solver && uvicorn api:app`, `cd studio && python
# app.py`, each establishing that directory as the import root). This file's
# presence puts the repo root on sys.path for pytest, and these two explicit
# entries do the same for each service, so tests/test_api.py/test_dof.py/
# test_solver.py can `import api`/`dof`/`solver`/`schemas`, and
# tests/test_studio.py can `import app`/`db`/`local_schemas`. studio's
# schemas file is named local_schemas.py specifically to avoid colliding
# with solver/schemas.py here, where both directories are on sys.path at
# once - see studio/local_schemas.py's module docstring.
import sys
from pathlib import Path

_ROOT = Path(__file__).parent
sys.path.insert(0, str(_ROOT / "solver"))
sys.path.insert(0, str(_ROOT / "studio"))
