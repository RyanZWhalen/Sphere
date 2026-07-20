# Sphere

Sphere makes a machine's Python state visible: every interpreter, environment, and
installed distribution is rendered as a live repository-to-runtime dependency graph.
It shows what the current folder would run, which requirements are missing or at the
wrong version, the evidence behind every verdict, and a guarded local repair plan.

Sphere is a localhost application. Its scanner and deterministic diagnosis run on the
machine; topology data is not sent to a hosted service.

## Judge quick start

### Prerequisites

- macOS or Linux (Windows is not currently supported)
- Python 3.11 or newer, available as `python3`
- Git, Make, and a POSIX shell
- Internet access during initial setup so `pip` can download dependencies and the
  small demo packages
- A browser

On Debian/Ubuntu, install the OS package that provides `venv` if `python3 -m venv`
is unavailable (commonly `python3-venv`). Confirm the selected Python first:

```bash
python3 --version
```

### Clone, prepare, and run

```bash
git clone https://github.com/RyanZWhalen/Sphere.git
cd Sphere
make setup
.sphere-venv/bin/sphere demo/sample-project --search-root demo
```

The two commands after `cd` are the complete demo path, in order. `make setup`:

1. Creates the dedicated `.sphere-venv` runtime.
2. Installs Sphere and its optional server dependencies only inside that runtime.
3. Runs `demo.sh` to create the reproducible three-state fixture.

Sphere then chooses a free `127.0.0.1` port and opens the graph. Nothing needs to be
built with Node: the production frontend is committed in `sphere/web/dist` and ships
inside the Python package.

If `python3` is not the desired Python, select an explicit 3.11+ executable:

```bash
make setup PYTHON=/absolute/path/to/python3.12
```

### Expected demo topology

The setup script creates, but does not commit, these local fixtures:

- `demo/sample-project`: no project environment; the folder resolves to a bare/shared
  interpreter that Sphere refuses to modify.
- `demo/.venv-broken`: `six==1.15.0` is installed, while the repository requires
  `six==1.16.0`; `idna` and `typing-extensions` are missing.
- `demo/.venv-good`: all three declared requirements are satisfied.

The first screen should therefore contain a red **Runs now** interpreter, a red
`.venv-broken` circle, and a green `.venv-good` circle marked **The fix**. Other bare
interpreters are collapsed into the expandable **Other interpreters on this machine**
group.

To reset only the sample data later:

```bash
make demo
```

## Demo walkthrough

1. Start on the red **Runs now** interpreter. The inspector shows that the folder's
   current Python is missing all three requirements, and Sphere recommends a local
   environment instead of polluting the shared interpreter.
2. Click `.venv-broken`. Its edge and node show one version mismatch and two missing
   packages.
3. Expand **Show evidence** under `six`. Sphere displays the exact interpreter path,
   rerunnable `python -I -c` metadata command, verbatim distribution list, the
   `reported 1.15.0 · requires ==1.16.0 · fails` proof, and the live scan timestamp.
4. Click **Preview fix**. The local repair agent shows the three exact `pip` commands
   before anything runs. Do not choose **Approve & run** during a read-only demo unless
   you intentionally want to modify `.venv-broken`.
5. Click `.venv-good` to show the fully satisfied alternative.

Interpreter and environment circles are draggable; the deterministic column/arc is
only their initial layout.

## Use Sphere on another repository

After `make setup`, point the isolated Sphere executable at any repository:

```bash
.sphere-venv/bin/sphere /absolute/path/to/repository \
  --search-root /absolute/path/to/search
```

The positional directory controls repository parsing and `python`/`python3` context
resolution. Each `--search-root` is recursively searched for `pyvenv.cfg`; the option
may be repeated:

```bash
.sphere-venv/bin/sphere ~/code/project \
  --search-root ~/code \
  --search-root ~/work
```

Useful server options:

```text
--port PORT       use a specific localhost port instead of a free one
--no-browser      start the server without opening a browser
```

For machine-readable output without the web server:

```bash
.sphere-venv/bin/python -m sphere.introspect \
  --indent 2 \
  --search-root /absolute/path/to/search \
  /absolute/path/to/repository
```

## What Sphere discovers

Interpreter sources include `PATH`, pyenv, conda, uv-managed Pythons, Homebrew, and
macOS framework/system locations. Environment discovery covers venv/virtualenv,
conda, uv project environments, common locations, and explicit search roots. Symlink
aliases are deduplicated through canonical real paths.

Repository declarations are read from:

- `requirements.txt`
- `[project].dependencies` in `pyproject.toml`
- `[tool.poetry.dependencies]` in `pyproject.toml`

Every target's installed packages are queried by running that target's own interpreter
with an isolated `importlib.metadata` subprocess. Sphere never imports discovered
packages into its own process.

## Evidence and repair safety

Every requirement row has a collapsed **Show evidence** receipt sourced from the same
authoritative repository-to-target edge as the visible verdict. Sphere never merges
package evidence from another interpreter or environment. If a query did not produce
enough evidence, the inspector says so rather than constructing a plausible result.

Scanning, topology, evidence, diagnosis, and fix preview are read-only. Writes require
an explicit **Approve & run** action. Sphere:

- refuses to modify its own `.sphere-venv`;
- refuses to install into shared system, framework, Homebrew, pyenv, or uv-managed
  interpreters;
- fingerprints previewed commands and rejects a stale plan;
- targets an environment through its exact interpreter path;
- records command output and a per-step receipt; and
- re-scans afterward to verify the resulting graph verdict.

The local diagnosis and repair planning are deterministic. Sphere does not download a
language model, call GPT at runtime, or permit a model to generate arbitrary shell
commands.

## Test without rebuilding the frontend

The judge path itself is the fastest integration test:

```bash
make setup
.sphere-venv/bin/sphere demo/sample-project --search-root demo
```

Run the Python test suite with the isolated runtime:

```bash
.sphere-venv/bin/python -m unittest discover -s tests -v
```

Node is needed only when changing the React source. Frontend contributors can run:

```bash
npm --prefix frontend ci
npm --prefix frontend test
npm --prefix frontend run build
```

The final command refreshes the committed `sphere/web/dist` bundle.

## Troubleshooting

- **`python3 -m venv` fails:** install the platform's Python venv support or rerun
  setup with `PYTHON=/absolute/path/to/a/python3.11+`.
- **A demo environment is missing:** run `make demo`, then restart Sphere so it
  performs a fresh scan.
- **The browser does not open:** add `--no-browser`, copy the printed localhost URL,
  and open it manually.
- **A port is occupied:** omit `--port` to let Sphere choose a free port.
- **A project environment is absent from the graph:** include its parent directory as
  a `--search-root`.
- **A discovery source is damaged:** check the topology's `warnings` array. Discovery
  sources fail independently so one unusual Python installation cannot abort a scan.

## Project structure

- `sphere/introspect.py`: stdlib-only machine and package topology scanner
- `sphere/requirements.py`: declaration parsing and package-version diffing
- `sphere/diagnose.py`: deterministic, edge-local plain-language diagnosis
- `sphere/fixplan.py`: guarded command-plan intermediate representation
- `sphere/apply.py`: approved execution, receipts, and verification scan
- `sphere/serve.py`: localhost API and prebuilt frontend server
- `frontend/`: React/React Flow source; not required for judge setup
- `demo.sh`: reproducible sample-data builder

`packaging` is the only core runtime dependency. FastAPI and Uvicorn are isolated in
the optional `serve` extra installed into `.sphere-venv`.

## How Codex and GPT-5.6 were used

Sphere was developed iteratively in Codex with GPT-5.6 as the collaborative coding and
reasoning model. The human author set the product direction, safety boundaries, demo
story, and acceptance criteria; Codex/GPT-5.6 helped turn those decisions into tested
code. In particular, it was used to:

- trace Python discovery and subprocess data through the graph schema;
- implement and test interpreter, environment, and dependency introspection;
- design the exact-edge evidence contract and catch an inspector edge-merging bug;
- build the React Flow visualization and verify drag behavior in the rendered app;
- design the deterministic plan/approval/receipt/re-scan repair loop;
- maintain Sphere's dedicated isolated installation path; and
- run unit, browser, and clean-clone validation from the documented judge workflow.

GPT-5.6 is part of the development process, not a hidden runtime dependency. The
shipped application remains local and deterministic so judges can reproduce every
claim without an API key.

## Supported platforms and current scope

Sphere currently supports macOS and Linux with Python 3.11+. Windows discovery and
activation semantics are outside this release. The application binds only to
`127.0.0.1`; it is not intended to be exposed as a remote multi-user service.
