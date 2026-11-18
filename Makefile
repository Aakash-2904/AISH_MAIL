# Enron dedup pipeline. Windows users without make: run the commands shown
# after each target directly, e.g. `.venv\Scripts\python.exe main.py --fresh`.

ifeq ($(OS),Windows_NT)
    BOOTSTRAP ?= py -3.11
    PYTHON := .venv/Scripts/python.exe
else
    BOOTSTRAP ?= python3
    PYTHON := .venv/bin/python
endif

.PHONY: setup download sizes run run-live test clean

setup:
	$(BOOTSTRAP) -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt

download:
	$(PYTHON) scripts/download_dataset.py
	$(PYTHON) scripts/mailbox_sizes.py

sizes:
	$(PYTHON) scripts/mailbox_sizes.py

# Full dry-run pipeline on a fresh DB (no email is sent).
run:
	$(PYTHON) main.py --fresh

# Live notices to NOTIFY_OVERRIDE_TO (needs .env); run `make run` first.
run-live:
	$(PYTHON) main.py --send-live --max-send 3

test:
	$(PYTHON) -m pytest -q

# Generated artefacts only; keeps data/ (tarball + maildir) and committed deliverables.
clean:
	$(PYTHON) -c "import pathlib, shutil; [p.unlink() for p in pathlib.Path('output/replies').glob('*.eml')]; [p.unlink(missing_ok=True) for p in map(pathlib.Path, ['data/enron.db', 'data/enron.db-wal', 'data/enron.db-shm', 'output/send_queue.jsonl'])]; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__') if '.venv' not in p.parts]; shutil.rmtree('.pytest_cache', ignore_errors=True)"
