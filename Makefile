PY ?= .venv/Scripts/python.exe
ifeq ($(OS),)
PY = .venv/bin/python
endif

.PHONY: install run worker demo test gmail-auth backup

install:
	uv venv .venv --python 3.12
	uv pip install -r requirements.txt --python $(PY)

run:
	$(PY) -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

worker:
	$(PY) -m app.jobs

demo:
	$(PY) scripts/seed_demo.py

test:
	$(PY) -m pytest tests/ -q

gmail-auth:
	$(PY) scripts/gmail_auth.py

backup:
	$(PY) -c "import shutil, pathlib, datetime; p = pathlib.Path('backups'); p.mkdir(exist_ok=True); dst = p / datetime.datetime.now().strftime('scoutreel-%Y%m%d-%H%M%S.db'); shutil.copy2('scoutreel.db', dst); print(dst)"
