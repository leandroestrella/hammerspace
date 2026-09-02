# usage

two ways to run either script: from the **actions tab** on github (nothing
to install), or from a **local terminal** (useful for debugging, and the only
way to see a traceback in full).

- [`create_subdomain.py`](#create-subdomain) — the subdomain itself
- [`setup_autodeploy.py`](#setup-auto-deploy) — push-to-deploy from a git repo
  into that subdomain

## from github actions

**actions** tab → pick a workflow → **run workflow**.

### create subdomain

| input | default | what it does |
| --- | --- | --- |
| `subdomain` | — | the name only, without the root domain: `lab`, not `lab.example.com` |
| `dry_run` | off | prints every call it would make, touches nothing |
| `with_dns_api` | off | also creates a dedicated namecheap a record — usually unnecessary, see [dns](#dns) |
| `skip_autossl` | **on** | leaves ssl alone; on by default because autossl needs a whm token nobody has set yet |
| `skip_https_redirect` | off | skips writing the `.htaccess` redirect block |

### delete subdomain

| input | default | what it does |
| --- | --- | --- |
| `subdomain` | — | the name to remove |
| `confirm` | — | retype the same name; anything else and the job stops before installing anything |
| `with_files` | off | also delete the document root folder |
| `purge` | off | with `with_files`: delete permanently instead of moving to the trash |
| `dry_run` | off | prints every call it would make, touches nothing |

### setup auto deploy

| input | default | what it does |
| --- | --- | --- |
| `project` | — | the subdomain name, same one you passed to `create_subdomain.py` |
| `repo` | — | the repo to deploy *from*, as `owner/name` |
| `branch` | `master` | pushes to this branch trigger a deploy |
| `dry_run` | off | prints every call it would make, touches nothing |
| `skip_workflow_file` | off | creates the ftp account and the secrets, prints the workflow instead of committing it |
| `force` | off | overwrite a deploy workflow that's already there and different |

there's no `show_password` input on purpose: in actions it would print a live
credential into the run log. the generated password goes straight into the
target repo's `FTP_PASSWORD` secret, and if you need it for winscp you can
reset it from cpanel → ftp accounts.

### teardown auto deploy

| input | default | what it does |
| --- | --- | --- |
| `project` | — | the project to unhook |
| `repo` | — | the repo it deploys from |
| `confirm` | — | retype the project name; anything else and the job stops before installing anything |
| `branch` | `master` | the branch to remove the deploy workflow from |
| `dry_run` | off | prints every call it would make, touches nothing |

teardown removes the workflow, the three secrets and the ftp account. it never
touches the deployed files — `destroy=0` on the cpanel call, deliberately, since
the ftp account's home *is* the live site. deleting that is
`create_subdomain.py --delete --with-files`, which at least reads the real
document root first.

creating and deleting are deliberately separate workflows in both pairs, so a
destructive run is never one mis-clicked checkbox away from a routine one. the
delete workflows also receive only the credentials they need — the subdomain one
never sees the dns or whm secrets, and the teardown one never sees `SERVER_IP`.

## from a terminal

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env    # fill in your credentials
chmod 600 .env          # it holds live api tokens
set -a; source .env; set +a
```

then:

```bash
# subdomain: create
python3 create_subdomain.py lab
python3 create_subdomain.py lab --dry-run
python3 create_subdomain.py lab --with-dns-api          # only if you need a dedicated record
python3 create_subdomain.py lab --skip-https-redirect

# subdomain: delete
python3 create_subdomain.py lab --delete                       # subdomain only
python3 create_subdomain.py lab --delete --with-files          # + folder to ~/.trash
python3 create_subdomain.py lab --delete --with-files --purge  # + folder gone for good

# auto deploy: set up
python3 setup_autodeploy.py lab --repo you/lab
python3 setup_autodeploy.py lab --repo you/lab --dry-run
python3 setup_autodeploy.py lab --repo you/lab --branch web
python3 setup_autodeploy.py lab --repo you/lab --show-password    # for winscp
python3 setup_autodeploy.py lab --repo you/lab --skip-workflow-file

# auto deploy: tear down
python3 setup_autodeploy.py lab --repo you/lab --delete
```

`--with-files` without `--delete`, or `--purge` without `--with-files`, is
rejected with a clear message rather than silently ignored. same for
`--show-password` with `--delete`.

## what a subdomain creation actually does

1. **cpanel** — creates the subdomain with its document root at `~/<subdomain>`
   (one level above `public_html`, not inside it)
2. **dns** — skipped by default, see below
3. **autossl** — skipped by default, see [ssl](#ssl)
4. **https redirect** — appends a `mod_rewrite` block to
   `~/<subdomain>/.htaccess`, creating the file if it isn't there

each step prints what it's doing with a `[step]` prefix, so a failed run tells
you exactly how far it got.

## what an auto-deploy setup actually does

0. **preflight** — everything that can be checked without changing anything:
   the target repo is reachable, the branch exists, no deploy workflow is
   already sitting there, no ftp account by that name exists. all of it happens
   *before* the first write, because a half-finished run leaves an orphan ftp
   account on the server that you then have to find and remove by hand
1. **cpanel** — creates the ftp account `<project>@<project>.<root-domain>`
   with its home at `~/<project>`, unlimited quota. that home is the same
   folder `create_subdomain.py` set as the subdomain's document root, so the
   deploy lands where the site is actually served from
2. **github secrets** — writes `FTP_SERVER`, `FTP_USERNAME` and `FTP_PASSWORD`
   to the *target* repo, encrypted with that repo's public key
3. **github workflow** — commits `.github/workflows/deploy-to-cpanel.yml`

step 3 is last on purpose. the workflow triggers on pushes to the branch, and
the commit that adds it **is** such a push — so the moment the file lands, a
real deploy runs. the ftp account and the secrets have to exist by then.

that also means `setup_autodeploy.py` is not a quiet operation at the end: it
finishes by publishing your repo. `--dry-run` first if you're not sure, or
`--skip-workflow-file` to stop one step short and commit the file yourself.

### the generated workflow isn't the one in the doc

the confluence page pins `actions/checkout@v2.1.0` and
`SamKirkland/FTP-Deploy-Action@3.1.1`, both from 2020. what gets committed uses
the current versions, with two differences that matter:

- **`protocol: ftps`** — v3 spoke plain ftp, so the account password crossed
  the network readable on every deploy. v4 supports explicit ftps, which is the
  same encryption the doc has you configure in winscp
- **no `fetch-depth: 2`** — v3 needed git history because it diffed with
  `git diff`. v4 keeps a `.ftp-deploy-sync-state.json` on the server instead, so
  a shallow checkout is correct and slightly faster

if a repo was set up by hand from the doc, it already has a
`deployTocPanel.yml`. the script checks for that name and **refuses to run**
rather than adding a second workflow — two of them means two deploys per push,
racing each other over the same ftp directory.

## dry runs

every network-touching step supports `--dry-run`, and genuinely makes **no**
calls when it's on — not even read-only ones. use it freely; it's the cheapest
way to check you typed the right name.

```
[cPanel] [dry-run] GET https://host:2083/execute/SubDomain/addsubdomain params={'domain': 'lab', ...}
[Namecheap] Step saltato: coperto dal record wildcard '*'
[AutoSSL] Step saltato (--skip-autossl).
[HTTPS redirect] [dry-run] Aggiungerei a lab/.htaccess: ...
```

one consequence worth knowing: in a dry run the delete path can't show you the
real document root, because reading it would be a network call. it says so
instead of guessing.

## dns

a wildcard `*` → server-ip record was created once, by hand, in namecheap. that
means **every** subdomain resolves the moment it exists — no per-subdomain dns
work, which is why the step is skipped by default.

`--with-dns-api` exists for the rare case you need a dedicated record (a
different ip for one subdomain, or no wildcard at all). two warnings:

- namecheap's api is gated behind account requirements (a $50 balance, 20+
  domains, or $50 spent in the last two years) that this account doesn't meet,
  so this path is **untested against a live api**
- `setHosts` rewrites *every* dns record for the domain at once. the script
  reads them all back and resends them, preserving `EmailType` and caa
  `Flags`/`Tag`, and it **refuses to run** if any record has dynamic dns
  enabled, because the api has no way to preserve that flag

if you ever do use it, check the domain's records before and after.

## ssl

autossl needs a **whm** token — root or reseller level — which is a different
thing from the cpanel user token everything else uses. until one exists,
`skip_autossl` stays on by default and new subdomains are served the server's
default certificate, so `https://<subdomain>` will show a certificate warning
while plain `http://` correctly redirects.

to enable it later: get a token from whm → development → manage api tokens, then
set `WHM_HOST`, `WHM_USER` (the reseller's own username, **not** `root`) and
`WHM_API_TOKEN`, and turn `skip_autossl` off.

## deleting: trash vs purge

`--with-files` moves the folder to `~/.trash`, where cpanel's file manager can
restore it. `--purge` deletes it outright.

two behaviours of cpanel's file api worth knowing:

- trashing `~/lab` when a `lab` is already in the trash lands it as `lab.1`
- the delete call reports **success even for paths that don't exist**, so a
  "deleted" line is not by itself proof anything was there

the script mitigates the second by reading the real document root from cpanel
before deleting, and refusing any path outside the home directory, the home
itself, or `public_html`.
