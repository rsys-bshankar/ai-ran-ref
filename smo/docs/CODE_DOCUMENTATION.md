# Documenting the code inside the files

This is the rule for the comments and docstrings that live **in** the source files. The READMEs, `ARCHITECTURE.md`, `HISTORY.md` and the other
Markdown files say what the system is and why it was decided; this file says how a source file explains itself to the next person who opens it:
a developer fixing a bug, a maintainer reviewing a change, a new colleague learning the module.

The aim is that someone who has not read the Markdown can open any file, learn what it is for, what each function promises and why the tricky parts
are written the way they are, and, for a test, what behaviour it pins down.

## The rule in one table

| Where | What is required |
|---|---|
| Every source file (code, test, script, migration, workflow, Dockerfile, chart template) | A **file description** at the top |
| Every class and every function or method that is public, or longer than five lines, or has a side effect | A **description** (docstring or doc comment) |
| Every non-obvious piece of logic: a branch that exists for a reason, an ordering that matters, a retry, a lock, a workaround, a magic number, a regular expression, a security check | A **why comment** next to it |
| Every test file | A description of **what is covered and how to run it**, and the fixtures it relies on |
| Every test function | A one-line description of **the behaviour it pins down** (the name says what; the description says why it matters or what would go wrong) |

"Trivial" is the only exemption: a one-line accessor, a property that returns a field, a `__repr__`, a pass-through wrapper whose name says everything.
When in doubt, write the description. A function that is simple enough to need none is simple enough that writing it takes ten seconds.

## What a good description says

Write for a reader who knows the language and the framework but not this system.

**File description** (Python: the module docstring; other languages: the header comment). Four things, in this order, each in a sentence or two:

1. *What the file is*: its job in the module (for example "the HTTP routes of rApp Management", "the Alembic revision that adds the approval tables").
2. *Where it sits*: which other files or modules call it or are called by it, and the standard or design record it implements (cite the clause or the
   `HISTORY.md` entry that has the rationale; do not copy it).
3. *What it owns and what it deliberately does not*: for example "reads only, never writes the kill-switch table".
4. *Anything a maintainer must know before editing it*: an invariant, a rule that CI enforces, a coupling to another file.

**Function or method description.** Say:

- what it does, as a promise ("Returns the elements the caller's scope covers", not "Loops over elements");
- the inputs that are not obvious (units, whether `None` is allowed, which are caller-supplied and untrusted);
- what it returns, including the empty or failure case;
- what it raises or which HTTP status it answers, and with which code;
- side effects: database writes, commits, outbox rows, calls to other modules, metrics, logs, anything that happens outside the return value;
- the transaction it expects or opens, when that matters.

Do not restate the code line by line; the reader has the code. Do not describe history ("changed in 0.6.0"); that is `HISTORY.md` and `CHANGELOG.md`.

**Why comments** go on the line or block they explain, and answer "why is it done this way?" or "what goes wrong if this is changed?". They are for the
parts that a careful reader could mistake for a bug or "tidy up": a `commit()` before a call, a comparison done in constant time, a second query that
looks redundant, a fixed `sleep`, a catch-all `except` that is deliberate, a field that must never be logged. A block longer than about twelve lines of
branching logic gets a short comment at the top saying what the block decides, then why comments inside where needed.

**Test files and test functions.**

- File description: which module or behaviour the file covers, the main fixtures it uses (and where they come from), and how to run it
  (`PYTHONPATH=.:../shared python -m pytest tests/test_x.py -q`); say if it needs Postgres, a stub server, or a built GUI.
- Each test function: one line that states the behaviour or rule being protected and, when it is not obvious, the failure it guards against
  ("a revoked token must stop working on the replica that carried the revocation"). A test that exists because of a past bug names the symptom, not the
  ticket. Table-driven tests describe the table once, above it.
- Helpers and fixtures: say what they build and what the tests may assume about the state they leave.

## Language conventions

| Language | File description | Function / class description | Why comments |
|---|---|---|---|
| Python | Module docstring (`"""..."""`) as the first statement | Docstring as the first statement; first line a summary sentence ending in a full stop, a blank line, then the detail | `#` comments, full sentences, no commented-out code |
| TypeScript / TSX | `/** ... */` block before the first import | TSDoc `/** ... */` on every exported function, component, hook and type; props documented on the props type | `//` comments |
| Go | Package comment (`// Package x ...`) in one file per package, and a short header comment in every other file | godoc comment starting with the name (`// Execute sends ...`) on every exported and every longer unexported identifier | `//` comments |
| Java | Javadoc block before the `package` line or on the top-level type | Javadoc on every type and every public or protected method, `@param`/`@return`/`@throws` where the meaning is not obvious | `//` comments |
| Shell | Header block after the shebang: purpose, usage, arguments, environment variables read, exit codes | A comment above every function | `#` comments |
| Dockerfile, YAML (workflows, compose, Helm values, GitOps) | Header comment: what the file builds or runs, and what changes it needs when something else changes | A comment above each stage, job, service or top-level key whose purpose is not obvious from its name | `#` comments |
| Helm templates | A `{{- /* ... */ -}}` comment at the top: what the template renders and which values drive it | A comment above each conditional block that explains when it renders | same |
| Alembic revisions | Module docstring: what the revision changes, why, the expand/contract step it is, and what the downgrade does | Docstrings on `upgrade()` and `downgrade()` that list the statements by purpose | comments on any statement that is not a plain `create_table`/`add_column` |

## What must not happen

- A comment or docstring must be **true**. A wrong description is worse than none: check it against the code, and when you change the code, change the text.
- No change in behaviour. A documentation change touches comments, docstrings and nothing else; CI proves it (see below).
- No secrets, no personal data, no internal host names in comments.
- No commented-out code, no `TODO` without an owner and a reason (open work belongs in `OPEN_ITEMS.md`).
- No filler such as "This function does X." above `def x()`. If the description cannot say more than the name does, say why the function exists, or what it does not do.

## How it is checked

- `scripts/verify_docs_only.py` compares two versions of the code with all comments and docstrings removed and fails if anything else differs. A documentation
  pull request must pass it; the pull request description says so.
- `scripts/check_code_docs.py` counts, per module, the source files without a file description and the functions that need a description and have none. A
  baseline in `scripts/code_docs_baseline.json` records today's counts per module; the check fails if a module's counts go **up**, and a new file or a new
  function must meet the rule from the day it is added. Lowering a module's count means lowering the number in the baseline in the same change. The target is
  zero everywhere.

## Order of the work

The work is done module by module, one pull request each, so a reviewer can read a module's change in one sitting: first the shared library and the
smaller services, then the larger ones; tests together with the code they cover; the GUI, the SDKs and the scripts in their own pull requests. Each pull
request touches only comments and docstrings, with the verifier's output in its description.
