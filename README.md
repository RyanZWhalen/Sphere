# Sphere
All things, from incompatible dependencies, conflicting contexts, missing packages, etc. should all be made visible and comprehensible to developers. Sphere's ultimate purpose is to turn any machine from a black-box to a fully modifiable sandbox that you can edit quickly, clearly, and cleanly.

## Setup

Sphere supports one installation path: a dedicated project-local virtual environment.
Do not install Sphere or its server dependencies into a system, framework, Homebrew,
pyenv, conda, or project interpreter—those are the runtimes Sphere is meant to observe.

From the repository root, prepare everything for the demo with one command:

```bash
make setup
```

This creates `.sphere-venv`, installs Sphere and its `serve` extra inside it, and runs
`demo.sh` to build the three states used by the walkthrough:

- `demo/.venv-good` satisfies every declaration.
- `demo/.venv-broken` has one version mismatch and two missing packages.
- `demo/sample-project` has no environment, so the folder resolves to a bare/shared
  interpreter that Sphere refuses to modify.

When setup finishes, run Sphere using its isolated entry point:

```bash
.sphere-venv/bin/sphere demo/sample-project --search-root demo
```

The command starts the localhost-only server on a free port and opens the graph in
your browser. Scanning and diagnosis are read-only. Sphere only changes a selected
environment after it shows the exact package plan and you choose **Approve & run**.
`.sphere-venv` is local state and must never be committed.

Those two commands, in that order, are the complete judge/demo path. Running Sphere
without `make setup` first does not guarantee the three-state fixture exists.

## Demo checklist

The initial folder target is the bare/shared interpreter case. It remains red because
Sphere refuses to pollute it. Click `.venv-broken` to see the mismatch/missing contrast,
then click `.venv-good` to see the fully satisfied target.

Every requirement row has a collapsed **Show evidence** control. Open one to inspect
the exact interpreter path, rerunnable metadata command, verbatim distribution-list
result, per-requirement proof, and live topology timestamp. On `.venv-broken`, `six`
demonstrates version-mismatch evidence; `idna` demonstrates honest absence from the
returned list.

## Local repair agent

Sphere's repair agent runs entirely on the machine. It reads the exact
repository-to-runtime verdict already shown by the graph, explains missing and
version-mismatched packages, and compiles a previewable series of defined `pip`
actions. It does not download a language model, send topology data to a remote API,
or generate arbitrary shell commands.

Shared and bare interpreters remain protected. When the current folder resolves to
one, Sphere recommends creating a project-local `.venv` instead of installing into
the shared runtime. Every approved action produces a receipt, and Sphere re-scans the
target afterward to verify the result.
