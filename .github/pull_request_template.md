## What this changes

## How you verified it

Be specific. If you looked at rendered output, called a real API, or tested on a real
project, say so — several bugs in this codebase were only findable that way.

- [ ] `python tests/run_all.py` passes
- [ ] New behaviour has a test, including a negative case where one applies
- [ ] `python docs/build.py --check` passes, if diagrams could be affected

## Anything you did not fix

Known limitations are worth writing down. An acknowledged gap is worth more than a
silent one.

## If this touches the specification

- [ ] Schema, `SPEC.md` version table, `model.py`, and `CONFORMANCE.md` updated together
- [ ] Round trip still lossless against the reference document
- [ ] Additive only, or opened as an issue first
