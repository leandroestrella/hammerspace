# setup

what you need before the first run, in both places the script can run from.

## credentials

| variable | required | where it comes from |
| --- | --- | --- |
| `CPANEL_HOST` | yes | the cpanel hostname, e.g. `server.example.com` — no `https://`, no port |
| `CPANEL_USER` | yes | your cpanel account username |
| `CPANEL_API_TOKEN` | yes | cpanel → security → **manage api tokens** → create |
| `ROOT_DOMAIN` | yes | the domain the subdomains hang off, e.g. `example.com` |
| `SERVER_IP` | yes | the ip subdomains point at (only actually used by the dns step) |
| `WHM_HOST` | no | defaults to `CPANEL_HOST` |
| `WHM_USER` | no | the reseller's whm username — **not** `root` unless you really are root |
| `WHM_API_TOKEN` | no | whm → development → manage api tokens. without it, autossl is skipped |
| `NAMECHEAP_API_USER` | no | only for `--with-dns-api` |
| `NAMECHEAP_API_KEY` | no | namecheap → profile → tools → api access |
| `NAMECHEAP_USERNAME` | no | defaults to `NAMECHEAP_API_USER` |
| `NAMECHEAP_CLIENT_IP` | no | must be whitelisted in namecheap → api access |

a missing variable fails immediately, naming exactly which ones are absent —
nothing half-runs.

### the two token levels are not interchangeable

the **cpanel** token acts as one account and can do everything here except ssl.
the **whm** token acts at server level and is the only thing that can trigger
autossl. a cpanel token in `WHM_API_TOKEN` will not work.

## local

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
chmod 600 .env
```

`chmod 600` matters: the file holds a live api token, and the default `644`
leaves it readable by every account on the machine.

on macos the venv isn't optional — homebrew python refuses `pip install`
system-wide with `externally-managed-environment` (pep 668).

load it before running:

```bash
set -a; source .env; set +a
```

`.env` is gitignored and has never been committed.

## github actions

**settings → secrets and variables → actions → new repository secret**, one at
a time:

```
CPANEL_HOST
CPANEL_USER
CPANEL_API_TOKEN
ROOT_DOMAIN
SERVER_IP
```

that's enough for creating and deleting. add the `WHM_*` secrets when you want
autossl, and the `NAMECHEAP_*` ones only if you'll use `--with-dns-api`.

github masks secrets in logs automatically — you'll see `***` where a value
would be.

### who can run the workflows

triggering a workflow requires **write** access to the repository. on a public
repo, visitors can read the code and the run history but the *run workflow*
button doesn't appear for them, and forks never receive the secrets.

worth being deliberate about, though: write access here is effectively access
to the hosting account, since anyone who can run the workflow can create and
delete subdomains — and anyone with write access can read repository secrets by
editing a workflow to echo them. if you ever add a collaborator, consider
`read`/`triage` instead, or move the secrets into an environment with required
reviewers so each run waits for approval.

## development

```bash
pip install -r requirements-dev.txt
pytest -q
```

the tests cover the pure functions only — name validation, the document-root
safety guard, response parsing, dns parameter construction — so they need
neither network nor credentials. anything that talks to an api is checked with
`--dry-run` instead.
