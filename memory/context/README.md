# Memory context files

This directory holds the per-installation context the agents read on every call.
The files in this directory are **user-specific** and are *not* committed to the
repo — each installation (you, your partner, a future contributor) keeps their
own. `git pull` will never overwrite them.

## What ships in the repo

`*.md.template` files. These are seeded into the repo so a new install has
something to start from. They contain example structure and HTML comments
explaining what to fill in — *no* real user data.

## What you create on your machine

When you (or `setup.sh`) copy a template to its real `.md` name, the file
becomes yours. Once it exists, `.gitignore` keeps it out of `git status` and
`git pull`. From that point on, the file is yours to edit freely — it will
not be touched by upstream changes.

## The four files

### `personal.md`
Background context injected into every system prompt. Keep it short — it's
read on every LLM call. Cover: who you are, how you work, what the agents
should always know about your situation.

### `preferences.md`
Operational preferences: timezone, communication style, notification rules,
work hours. Read alongside `personal.md` on every call.

### `projects.md`
Your active projects with the repos and services associated with them. The
DevOps agent uses this to know which GitHub repos to monitor and which
Railway services to manage. Update it whenever a project's repo, service,
or status changes.

### `reader_profile.md`
Drives the `email-digest` skill. Defines your current focus, the active
projects to match against, a "Watching for" keyword list (used as a
subject-line pre-filter), and an "Ignore" list. Update at least weekly —
a stale profile produces a stale digest. See
`agents/business/skills/email-digest.md` for how it's used.

## First-time setup

The fastest way to seed the files is via `setup.sh`:

```bash
./setup.sh
```

It copies each `*.md.template` to its real `.md` name if no copy exists yet.
Existing files are left untouched — running `setup.sh` again will not clobber
your data.

To seed a single file by hand:

```bash
cp memory/context/personal.md.template memory/context/personal.md
$EDITOR memory/context/personal.md
```

## Adding another agent's context later

If you add a fifth context file (say `memory/context/team.md`), commit a
`team.md.template` to the repo and follow the same pattern: the template is
tracked, the real file is gitignored. Don't forget to add the new file's
name to the `CONTEXT_FILES` array in `setup.sh` so it gets seeded on
first run.
