# Sphere
All things, from incompatible dependencies, conflicting contexts, missing packages, etc. should all be made visible and comprehensible to developers. Sphere's ultimate purpose is to turn any machine from a black-box to a fully modifiable sandbox that you can edit quickly, clearly, and cleanly.

## Setup

Sphere supports one installation path: a dedicated project-local virtual environment.
Do not install Sphere or its server dependencies into a system, framework, Homebrew,
pyenv, conda, or project interpreter—those are the runtimes Sphere is meant to observe.

From the repository root, create and install the isolated environment:

```bash
make setup
```

This creates `.sphere-venv` and installs the editable project with its `serve` extra
inside it. Run Sphere using that environment's entry point directly:

```bash
.sphere-venv/bin/sphere demo/sample-project --search-root demo
```

The command starts the read-only local server on a free localhost port and opens the
graph in your browser. `.sphere-venv` is local state and must never be committed.
