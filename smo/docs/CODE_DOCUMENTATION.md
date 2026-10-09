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

"Public" means: no leading underscore, or decorated as a route, a fixture or a test. A private helper needs a description when it is longer than five lines or has a
side effect. Nested functions, closures and test stubs need only a `#` why-comment, not a docstring.

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
- Each test function: one sentence (two lines at most) that states the behaviour or rule being protected and, when it is not obvious, the failure it guards against
  ("a revoked token must stop working on the replica that carried the revocation"). A test that exists because of a past bug names the symptom, not the
  ticket. A parametrized test describes the table once, in a `#` comment above the decorator.
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

## FastAPI routes and request models: the docstring is published

FastAPI puts the docstring of a **route function** and of a **pydantic request or response model** into the OpenAPI document (`docs/openapi/*.json`), and
`tests_integration/test_openapi_specs.py` fails when the committed specs and the live schema differ. So in a comment-only change:

- do not add, remove or edit the docstring of a route function or of a request or response model;
- put the maintainer detail (status and error codes, ordering, the transaction, what is deliberately not checked) in a `#` block directly under the route's
  docstring (or directly after the `def` line when there is none), and for a model in a `#` block above the class;
- write a docstring there only when the API consumer should read it, and then regenerate the specs in the same change (`scripts/generate_openapi_specs.py`).

A route or model with such a `#` note counts as documented for `check_code_docs.py`.

## Existing text

Keep a good existing docstring. Rewrite one that is wrong, vague, or written as history ("added in this pass", "was absent before"): state what is true now, in
the present tense, and keep the clause and `HISTORY.md` citations. When a file has no docstring and starts with imports, the file description goes before the
first import.

## What must not happen

- A comment or docstring must be **true**. A wrong description is worse than none: check it against the code, and when you change the code, change the text.
- No change in behaviour. A documentation change touches comments, docstrings and nothing else; CI proves it (see below).
- No secrets, no personal data, no internal host names in comments.
- No commented-out code, no `TODO` without an owner and a reason (open work belongs in `OPEN_ITEMS.md`).
- No filler such as "This function does X." above `def x()`. If the description cannot say more than the name does, say why the function exists, or what it does not do.

## How it is checked

Two scripts in `smo/scripts/`, both run by the CI job **Code documentation** (`.github/workflows/smo-tests.yml`, job `code-docs`) and both runnable locally from `smo/`.
They need `git`, `go` (Go files) and `node` with `smo/gui/node_modules` (`cd smo/gui && npm ci`; TypeScript files).

**`check_code_docs.py`: the gate.** Runs on every pull request and every push.

```bash
python scripts/check_code_docs.py              # the gate
python scripts/check_code_docs.py --report     # a table by module: files, files without a description, items that need one, missing, tests, tests missing
python scripts/check_code_docs.py --list sme   # every finding of one module, file:line and name
python scripts/check_code_docs.py --write-baseline
```

It counts, per module (the first directory under `smo/`; `gui/src` and `gui` apart; one module per `samples/<name>`), three numbers: `files_without_description`,
`functions_without_description` (classes, functions and methods that need a description under the table above and have none) and `tests_without_docstring`.
`scripts/code_docs_baseline.json` holds today's numbers. The gate fails when

1. a module's number is **higher** than the baseline, or
2. a file or function that is **new** against the merge base with `origin/main` (`--base REF` to choose another) has no description. This holds whatever the
   baseline says, so lowering one count cannot pay for adding an undocumented function.

A number that is **lower** than the baseline passes and is reported; lower the baseline in the same change with `--write-baseline` and commit it (the integration
test `test_gate_the_committed_baseline_is_current` fails otherwise). The target is zero everywhere.

How "needs a description" is measured is the table above, with these approximations (they err towards counting, never below the truth). "Longer than five lines" stands in
for "has a side effect". Trivial means at most five lines and one statement. Python is read with `ast`; a route function and a pydantic model are documented by a docstring or by
the `#` note described under "FastAPI routes and request models"; a parametrized test by a `#` comment above its decorators; nested functions, lambdas and the stubs of test
files are exempt. TypeScript is read with the Oxc parser from the GUI's toolchain (exported declarations and `it(`/`test(` calls, needing `/** */` and a comment above); Go with
`go/ast` (exported identifiers, longer unexported ones, and `Test*` functions need a doc comment); Java with a lexer and a stack of blocks (types, public and protected methods,
`@Test` methods need Javadoc); shell by the comment above `name() {`; Dockerfile, YAML, Helm templates and SQL by a leading comment. Data files (OpenAPI JSON, rApp package
descriptors, `docs/`) and vendored files are not source files here.

**`verify_docs_only.py`: the proof that a change is only documentation.**

```bash
python scripts/verify_docs_only.py --base <git ref> [--head <git ref>] [--allow-unverified-extension .md]
```

It lists the files that differ between `--base` and `--head` (omitted: the working tree, untracked files included) and, for each, compares the two versions with the
comments removed: Python by `ast.dump` after dropping docstrings (so reformatting passes), TypeScript and JavaScript by the syntax tree with types and without positions,
Go by the token stream after `go/parser` accepted the file, Java by tokens after a lexer removed comments (strings, chars and text blocks respected), shell, Dockerfile, YAML and
Helm templates by the text without `#` and `{{- /* */ -}}` comments, SQL by tokens without `--` and `/* */`. A comment that is a tool directive (`# noqa`, `# type: ignore`,
`# shellcheck disable=`, `//go:build`, `//go:embed`...) is compared too, so editing or moving one fails. It exits 1 with a reason per file when anything else differs,
when a code file is added or deleted, or when a file cannot be classified ("not verified"); an extension is let through only with `--allow-unverified-extension`. The comparison
also notes (without failing) a changed docstring on a route or request model, because that text is published in the OpenAPI document.

**The pull request title convention.** A pull request that changes only comments and docstrings is titled `Docs(code): <what>`. CI then runs the proof against the merge
base (`--allow-unverified-extension .md`: a README may change with the code it describes) and the job fails if it does not hold; the pull request description quotes the
verifier's last line. Any other title runs the gate only. The label `docs-tooling` switches the proof off, for the one change that cannot satisfy it: a change to the verifier
or the gate themselves. Run it before pushing: `python scripts/verify_docs_only.py --base origin/main --allow-unverified-extension .md`.

Limits worth knowing: the verifier proves "the same program", not "a true description"; a reviewer still reads the text. Inside a YAML block scalar nothing is stripped, except the
full-line comments of a workflow's `run:` script. Java and shell are read with a lexer and rules, not a full parser; a construct they misread fails the proof (a false alarm), not
passes it.

## Order of the work

The work is done module by module, one pull request each, so a reviewer can read a module's change in one sitting: first the shared library and the
smaller services, then the larger ones; tests together with the code they cover; the GUI, the SDKs and the scripts in their own pull requests. Each pull
request touches only comments and docstrings, with the verifier's output in its description.
