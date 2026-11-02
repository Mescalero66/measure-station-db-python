# Development

How to set up a development copy of the server's code, and run its tests and checks. The
code runs on any OS; only the database tests need the server Pi (or another machine with
PostgreSQL + TimescaleDB).

## Set Up

From the repository's folder:

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"     # Windows: .venv\Scripts\pip
```

On Windows, if `python` isn't found, use the launcher: `py -3.14 -m venv .venv`.

`-e` installs the package "editable": the virtual environment uses the files in `src/`
directly, so changes take effect without installing again.

## Run the Checks

All three must pass before a change is finished:

```bash
.venv/bin/pytest                      # the tests
.venv/bin/ruff check .                # mistakes and style
.venv/bin/ruff format --check .       # formatting (also of Python blocks in Markdown)
```

On Windows, use `.venv\Scripts\` instead of `.venv/bin/`. `ruff format .` (without
`--check`) fixes the formatting.

Run the server's programs with `python -m measure_station_db.<program>`, never by file
path. Running a file inside the package would put its folder on `sys.path`, and then
`sysconfig.py` would shadow the standard library's `sysconfig` module.

## Database Tests

Tests that use the `test_db` fixture (in `tests/test_store.py`) need a real PostgreSQL +
TimescaleDB, so they run on the server Pi. Elsewhere they're skipped, and `pytest`
reports them as `s`.

They use a **separate test database**, never the live `measure_station_db`: the fixture
refuses to connect to that. Before each test it drops the server's tables from the test
database, and the test creates them again, so every test starts from empty.

**Once, on the server Pi:** create the test database, owned by `ingest` like the live
one, and switch on TimescaleDB in it:

```bash
sudo -u postgres psql -c "CREATE DATABASE measure_station_db_test OWNER ingest;"
sudo -u postgres psql -d measure_station_db_test -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"
```

Then add the test tools to the installed software (the installer leaves them out):

```bash
sudo /opt/measure-station-db/.venv/bin/pip install -e "/opt/measure-station-db[dev]"
```

**To run them**, from the software's folder, pointing the tests at the test database:

```bash
cd /opt/measure-station-db
MEASURE_STATION_DB_TEST_DSN="host=localhost dbname=measure_station_db_test user=ingest password=the-database-password-of-ingest" .venv/bin/pytest -p no:cacheprovider
```

(`-p no:cacheprovider` stops pytest trying to save its cache in a folder only root can
write to.) Every test should pass, with none skipped.

## Test Data

`tests/fixtures/station-examples.json` holds the example messages from the station's
documentation, as the station sends them. Each entry names the page it came from. The
station's interface is fixed, so they're the reference for what the server must accept;
`tests/test_station_examples.py` checks they still follow its rules. They're copied
rather than read from the station repo, because the database tests run on the server Pi,
where that repo isn't present. If the station's docs ever gain an example (e.g. `cpu`),
copy it in.
