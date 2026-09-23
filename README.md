# codetools (`ct`)

Manual Claude Code for any web chatbot. The bot replies with a **batch** of ops (reads, greps, finds,
edits, writes, moves, deletes, commands). You copy the reply, and codetools parses it, runs the read-only ops,
preflights every change against the real files, and copies one report back to your clipboard for you to
paste into the chat. Files change and commands run only when you type `apply N` or `applypartial N`.

## Install

Put this repo's `bin/` folder on PATH. It holds `ct.cmd` for PowerShell and cmd, and `ct` for Git Bash.
Both find `ct.py` relative to themselves, so the repo can live anywhere.

The first `ct` creates `~/venvs/.venv_codetools` and installs `requirements.txt` into it. Every later launch
compares the file's hash with the one stored in the venv and reinstalls only when `requirements.txt` has
changed. Versions are pinned, so dependencies change only when you edit that file. `ct` uses the current
directory as the project root, or takes a path: `ct C:\code\myproject`.

## A session, start to finish

**1. Open the project.** `cd` into the project and run `ct`. The first run creates `.codetools/`:

| file | purpose | commit it? |
| --- | --- | --- |
| `settings.toml` | context contents, command timeout, extra secret patterns | yes |
| `pins.txt` | files the context always includes in full | yes |
| `notes.md` | a project brief for the bot: what it is, conventions, how to test | yes |
| `.gitignore` | keeps `batches/` and `log.txt` out of git | yes |
| `batches/NNNN/` | every batch: the whole reply, each report, backups, undo manifest | no |
| `log.txt` | one timestamped line per event | no |

**2. Brief the bot once per chat.** Type `context`. The clipboard now holds:

- the environment: the full project root path (every batch path is relative to it, and `run` ops start
  there), local date, time, and UTC offset, the platform, and the shell `run` ops use;
- the protocol (how to write batches);
- `notes.md`;
- the git branch, uncommitted changes, and recent commits;
- the project tree with a line count beside every file;
- outlines of anything listed under `outline` in `settings.toml`;
- every pinned file in full, with line numbers.

Open a new chat, paste it, and type your task below it. Pin the files the bot will need nearly every time,
such as the main module, the config, or a README with conventions (`pin src/app.py`, `pin 'docs/*.md'`).
Leave everything else for the bot to read on demand, since the tree tells it what exists and how big
each file is. `pins` shows what the pins currently resolve to.

**3. Let the bot explore.** Its first reply is usually a batch of reads, greps, and outlines. `=== outline`
lists the classes, functions, and headings in files or whole directories with line numbers, so the bot
can read only the ranges it needs. Python outlines are exact, since they come from the parser, and show
full signatures. Markdown shows heading sections. JS/TS, Go, Rust, Java, C#, Kotlin, C/C++, shell, and
PowerShell list the declaring lines, found by pattern.

Copy the reply with the chat's copy button. You don't need to switch windows: `ct` sees the batch on the clipboard, runs
it, and puts the results on the clipboard. Paste into the chat and send. Repeat until the bot has
what it needs. Each round trip is one copy and one paste.

**4. Review the changes.** When a batch contains changes, `ct` preflights them in order against a staged
copy of the project, so a later op sees an earlier op's edit. It shows the colored diff of every file that would
change and copies a report like:

```
[codetools] batch 14: preflight 27 of 30 ops passed, 3 failed (ops 7, 12, 19).
Awaiting operator: `applypartial 14` applies the 27 passing ops and rejects ops 7, 12, 19; ...
```

Then choose:

- **Everything passed and the diffs look right:** `apply 14`. The files are written, any `run` ops execute
  (tests, builds), and the apply report is copied. Paste it.
- **Some ops failed, but the passing ones stand on their own:** `applypartial 14`. The apply report lists
  what went in, plus each rejected op with the reason and the closest match in the file. Paste it; the bot
  resends only the fixes.
- **Some ops failed, and the rest shouldn't go in without them:** don't apply. The preflight report is
  already on the clipboard; paste it and the bot resends the corrected batch.
- **The approach is wrong:** `reject 14 use the existing retry helper instead`. The rejection and your
  note are copied for the bot.

**5. Recover.** `undo 14` restores every file batch 14 changed. It refuses if something changed those
files since, including a later batch or your own edits. Undo the later batch first.

Every report is self-contained, so always paste only the latest one.

Every copy (reports, `context`, `protocol`) has the same shape: a one-line headline, then the body inside a
code fence. Chat UIs that collapse code blocks, such as AI Studio, fold the body so long pastes stay compact.
The fence is always longer than any fence inside the body. Each copy ends with blank lines, so after pasting
you can keep typing below it. Set `fold = false` under `[clipboard]` in `settings.toml` for plain text
instead.

## How a batch is read

- The batch runs from `=== batch` to `=== end`. Prose and code fences around it are ignored, so copy
  the whole reply.
- **Cut off:** a batch with no `=== end` is assumed to be still streaming. `ct` waits and copies nothing;
  copy the reply again when it finishes. `paste` forces it through, and it's rejected as cut off.
- **Malformed:** if any op is malformed, the whole batch is rejected, nothing runs, and a report listing
  every error by line is copied.
- **Escaping:** content that contains lines like `=== end` uses a heredoc header (`=== write docs/x.md <<EOF`
  ... `EOF`), so a file can contain the markers themselves.
- **Duplicates:** copying the same batch again is ignored; `paste` ingests it again on purpose.
- **Your own copies:** reports `ct` copies are never re-ingested, even when they quote batch text.

### Edits

`=== edit PATH` takes SEARCH/REPLACE pairs. SEARCH must match exactly one place, including indentation.
Matching ignores trailing whitespace, because the bot can't see it reliably in chat. Lines that REPLACE
leaves unchanged keep the file's exact text, so trailing whitespace (such as a Markdown line break) is lost
only on lines the edit actually changes. CRLF files stay CRLF. Near misses are never applied. The report
says why a SEARCH missed:

- line-number prefixes copied from read output;
- same text, different indentation (with a diff of the real lines);
- otherwise, the most similar region of the file as a diff with its similarity score.

`=== patch` accepts a unified diff and applies each hunk the same way. The `@@` line numbers are ignored.
`=== write` only creates files; `=== overwrite` replaces an existing file entirely.

## Commands

| command | effect |
| --- | --- |
| `context` | copy protocol + notes + git state + tree + pinned files |
| `pin PATH\|GLOB`, `unpin`, `pins` | manage the files `context` includes |
| `protocol` | copy only the batch format instructions |
| `apply N` / `applypartial N` | apply batch N (every op must pass / the passing ops only) |
| `reject N [note]` | discard batch N, copy a rejection with your note |
| `undo N` | restore the files batch N changed |
| `diff N`, `show N`, `copy N` | redisplay diffs, print the report, re-copy the report |
| `paste` | ingest the clipboard now (a repeat, or a batch with no `=== end`) |
| `watch [on\|off]` | toggle clipboard watching |
| `kill` | kill the running command and everything it started |
| `clear`, `help`, `quit` | |

Up and down arrows recall earlier commands.

## The clipboard

`ct` reads the clipboard through the Windows clipboard API: `paste` and watch mode both read it directly,
and no shell ever sees the text, so there is no quoting or escaping to worry about. Only one `ct` watches the
clipboard at a time. A second `ct` (another project) starts with watch off, and `watch on` there takes
over; the first one reports that it stopped watching. This prevents one copied batch from landing in two
projects.

## Safety model

- Nothing that changes files or runs a command happens without you typing its batch id.
- Paths must be relative, can't use `..`, can't touch `.git/` or `.codetools/`, and can't resolve outside the
  root through symlinks.
- Secret files (`.env`, `*.pem`, `*.key`, `id_rsa*`, and similar, plus `extra_patterns` in settings) are never
  read, grepped, edited, or pinned, so their contents never reach the chat.
- Before applying, `ct` checks that every file it's about to touch is unchanged since preflight. If one
  changed, the batch is re-preflighted and you review it again.
- Files are backed up into the batch's folder before being changed, and a failed write rolls back the
  whole batch.
- Commands run in Git Bash in the project root with no stdin, pagers off, and the timeout from settings. On
  Windows each command runs in a Job Object, so a timeout or `kill` also stops every process it started.

## Development

```
pip install -r requirements-dev.txt
python -m pytest
```
