<img src="assets/jim.gif" alt="pulling a tool out of nowhere" width=35%>

🇬🇧 English · 🇮🇹 [Italiano](README.it.md) · 🇪🇸 [Español](README.es.md)

# hammerspace

🧰 small scripts automating the repetitive setup work i do every time i spin up a new webapp — dns, hosting, and whatever else earns its place here over time.

in animation, comics and video games, **hammerspace** is the imaginary
extradimensional storage that lets a character pull out exactly the tool they
need, out of nowhere, the moment they need it — [wikipedia has the full
history](https://en.wikipedia.org/wiki/Hammerspace), tracing it from looney
tunes gags to the anime fandoms (*urusei yatsura*, *ranma ½*) that coined the
term. [tv tropes](https://tvtropes.org/pmwiki/pmwiki.php/Main/Hammerspace) has a
deeper catalogue if you want to go down that rabbit hole.

that's the idea here: reach in, pull out the right script, get back to building.

## how it works?

one python script per job, driven either from the github actions tab — nothing
installed, credentials living in repository secrets — or from a terminal when
you want to watch it work.

```mermaid
%%{init: {'theme': 'dark'}}%%
flowchart LR
    YOU[you] -->|actions tab| GHA[github actions]
    YOU -->|terminal| CLI[the scripts]
    GHA --> CLI
    CLI -->|subdomain, .htaccess, ftp account| UAPI[cpanel uapi]
    CLI -->|delete subdomain, delete files| API2[cpanel api2]
    CLI -->|secrets, deploy workflow| GH[github api]
    CLI -.->|autossl, optional| WHM[whm api]
    CLI -.->|a record, optional| NC[namecheap api]
    UAPI --> SRV[(your server)]
    API2 --> SRV
    GH -->|push| SRV
```

the split between the two cpanel apis isn't a style choice: uapi simply has no
delete function, for subdomains or for files — though it does have one for ftp
accounts. see [cpanel api notes](docs/cpanel-api.md) and [github api
notes](docs/github-api.md).

## tools

| tool | what it does |
| --- | --- |
| [`create_subdomain.py`](./create_subdomain.py) | creates a subdomain on cpanel, points its document root at `~/<name>`, and forces an https redirect. optionally triggers autossl and a dedicated dns record. deletes it again too, with or without its files. |
| [`setup_autodeploy.py`](./setup_autodeploy.py) | wires a github repo to that subdomain: creates the ftp account, writes the three `FTP_*` secrets on the target repo, and commits a deploy workflow so every push publishes. tears the whole thing down too. |

## features

- 🚀 **one click from the actions tab** — no checkout, no local python, credentials never leave repository secrets
- 🧯 **create and delete are separate workflows** — a destructive run is never one mis-clicked checkbox away from a routine one, and each one only receives the credentials it needs
- ✍️ **destructive runs ask you to retype the name** — checked before anything is even installed
- 🔍 **`--dry-run` everywhere** — prints every call it would make and genuinely makes none, not even read-only ones
- 🛫 **checks before it writes** — the auto-deploy run verifies the repo, the branch, the workflow and the ftp name up front, so a failure halfway through doesn't leave an orphan account behind
- 🔒 **forced https, encrypted ftp** — a `mod_rewrite` block appended to the subdomain's `.htaccess`, and deploys over ftps rather than the plaintext ftp the original procedure used
- 🗝 **secrets sealed, never printed** — repo secrets are libsodium sealed boxes, and a generated ftp password stays out of the logs unless you ask for it
- 🗑 **files to the trash by default** — recoverable from cpanel's file manager; `--purge` when you really mean it
- 🛡 **paths read, never guessed** — the document root comes from cpanel itself, and anything outside the home directory (or `public_html`) is refused
- 🚫 **won't clobber what it didn't write** — an existing `.htaccess` or deploy workflow stops the run instead of being replaced
- 🌐 **dns usually unnecessary** — a one-time wildcard record means every subdomain resolves the moment it exists
- 💥 **fails loudly and early** — a missing credential names itself instead of turning into a confusing api error later

## setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # fill in your own credentials, never commit this file
chmod 600 .env         # it holds live api tokens
```

full credential list, and the github secrets to configure, in
[setup](docs/setup.md).

## usage

from the **actions** tab, or:

```bash
python3 create_subdomain.py lab                                # create
python3 create_subdomain.py lab --dry-run                      # rehearse it
python3 create_subdomain.py lab --delete                       # remove, keep the files
python3 create_subdomain.py lab --delete --with-files          # + folder to the trash
python3 create_subdomain.py lab --delete --with-files --purge  # + folder gone for good

python3 setup_autodeploy.py lab --repo you/lab                 # push to deploy
python3 setup_autodeploy.py lab --repo you/lab --branch web    # from another branch
python3 setup_autodeploy.py lab --repo you/lab --delete        # unhook it again
```

every flag, and what each step actually does, in [usage](docs/usage.md).

## repository layout

```
create_subdomain.py       the subdomain tool
setup_autodeploy.py       the push-to-deploy tool
test_*.py                 tests for the pure functions
.github/workflows/        a create and a delete workflow per tool
docs/                     setup, usage, api notes, troubleshooting
assets/                   art
```

## docs

- [setup](docs/setup.md) — credentials, the two token levels, github secrets, who can run what
- [usage](docs/usage.md) — every flag and workflow input, and what a run actually does
- [cpanel api notes](docs/cpanel-api.md) — the gaps and surprises that cost real time to find
- [github api notes](docs/github-api.md) — sealed-box secrets, the scope everyone forgets, and why a 404 isn't a typo
- [troubleshooting](docs/troubleshooting.md) — what to do when a run goes sideways

## development

work happens on `develop`; `master` is what the workflows run from.

```bash
pip install -r requirements-dev.txt
pytest -q
```

the tests cover the pure functions — name validation, the document-root safety
guard, response parsing, dns parameter construction, secret encryption, the
generated workflow — so they run without network or credentials. anything
touching an api is exercised with `--dry-run` instead.

## license

[mit](LICENSE)
