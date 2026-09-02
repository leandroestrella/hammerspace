# hammerspace

A personal collection of small scripts that automate the repetitive setup work
I do every time I spin up a new webapp — DNS, hosting, and whatever else earns
its place here over time.

## Why "hammerspace"?

In animation, comics, and video games, **hammerspace** is the imaginary
extradimensional storage that lets a character pull out exactly the tool they
need, out of nowhere, the moment they need it — [Wikipedia has the full
history](https://en.wikipedia.org/wiki/Hammerspace), tracing it from Looney
Tunes gags to the anime fandoms (*Urusei Yatsura*, *Ranma ½*) that coined the
term. [TV Tropes](https://tvtropes.org/pmwiki/pmwiki.php/Main/Hammerspace) has
a deeper catalogue of examples if you want to go down that rabbit hole.

That's the idea here: reach in, pull out the right script, get back to
building.

## Tools

| Tool | What it does |
| --- | --- |
| [`create_subdomain.py`](./create_subdomain.py) | Automates creating a new subdomain for a webapp: cPanel subdomain creation, DNS (optional, skipped if you use a wildcard record), AutoSSL, and forced HTTPS redirect. |

Each tool is self-contained; see its own header comment for usage details.

## Setup

```
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your own credentials, never commit this file
```

## Running via GitHub Actions

These scripts are also wired up as manually-triggered GitHub Actions
workflows (`.github/workflows/`), so they can run without needing to have
this repo checked out locally. Configure the required secrets under
**Settings → Secrets and variables → Actions**, then trigger a run from the
**Actions** tab (`Run workflow`).
