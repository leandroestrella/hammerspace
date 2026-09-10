# usage

two ways to run any of the scripts: from the **actions tab** on github (nothing
to install), or from a **local terminal** (useful for debugging, and the only
way to see a traceback in full).

- [`create_subdomain.py`](#create-subdomain) — the subdomain itself
- [`setup_autodeploy.py`](#setup-auto-deploy) — push-to-deploy from a git repo
  into that subdomain
- [`setup_gitflow.py`](#setup-gitflow) — `master` + `develop` on a github repo,
  with an initial commit if it's empty

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
| `skip_starter_page` | off | skips writing a placeholder `index.html` that already carries the PostHog snippet; never overwrites an existing index |
| `skip_posthog` | off | writes the starter page *without* the PostHog snippet, so the subdomain isn't tracked. with `skip_starter_page` on there's no page at all, so it changes nothing |

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
| `skip_secrets` | off | leave the repository secrets alone |
| `skip_ftp` | off | leave the ftp account alone; with `skip_secrets` and `force`, this is the repair path |
| `force` | off | overwrite a deploy workflow that's already there and different |
| `skip_posthog_check` | off | skip the PostHog snippet scan below — for a site created with `skip_posthog` |

after the workflow step, the tool scans the target repo's pages for `posthog.init` and warns if it's missing: the first deploy replaces the tracked starter page, so a repo without the snippet silently stops reporting. the canonical snippet is [`assets/posthog-snippet.html`](../assets/posthog-snippet.html).

an **empty** target repo — created on github, never pushed to — is fine: the
preflight spots it and the run initializes it with gitflow before writing
anything else. see [an empty repo](#an-empty-repo).

there's no `show_password` input on purpose: in actions it would print a live
credential into the run log. the generated password goes straight into the
target repo's `FTP_PASSWORD` secret, and if you need it for winscp you can
reset it from cpanel → ftp accounts.

### setup gitflow

| input | default | what it does |
| --- | --- | --- |
| `repo` | — | the repo to prepare, as `owner/name` |
| `branch` | `master` | the production branch. on an empty repo, the initial commit lands here |
| `develop` | `develop` | the integration branch, created from `branch` if it isn't there |
| `default_develop` | off | also make `develop` the repo's default branch, so clones and pull requests start from it. needs admin rights on the repo |
| `dry_run` | off | prints what it would do, touches nothing |

safe on any repo, any number of times: it only ever *adds* what's missing, and
never moves a branch that already exists. see [what a gitflow setup actually
does](#what-a-gitflow-setup-actually-does).

### teardown auto deploy

| input | default | what it does |
| --- | --- | --- |
| `project` | — | the project to unhook |
| `repo` | — | the repo it deploys from |
| `confirm` | — | retype the project name; anything else and the job stops before installing anything |
| `branch` | `master` | the branch to remove the deploy workflow from |
| `dry_run` | off | prints every call it would make, touches nothing |

teardown has no such skip inputs, deliberately: it is all-or-nothing, so a
half-torn-down project can't be produced by a mis-clicked checkbox. the
terminal has the flags if you genuinely need a partial one.

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
python3 create_subdomain.py lab --skip-starter-page     # no index.html, no PostHog snippet
python3 create_subdomain.py lab --skip-posthog          # the index.html, without PostHog

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

# auto deploy: repair just the workflow file of an existing setup
python3 setup_autodeploy.py lab --repo you/lab --skip-ftp --skip-secrets --force

# auto deploy: tear down
python3 setup_autodeploy.py lab --repo you/lab --delete

# gitflow
python3 setup_gitflow.py --repo you/lab                     # master + develop, initial commit if empty
python3 setup_gitflow.py --repo you/lab --branch main       # main + develop
python3 setup_gitflow.py --repo you/lab --default-develop   # + develop as the default branch
```

`--with-files` without `--delete`, or `--purge` without `--with-files`, is
rejected with a clear message rather than silently ignored. same for
`--show-password` with `--delete`, and for `--skip-ftp` without either
`--skip-secrets` or an explicit `--ftp-password`.

### every flag

the examples above cover the common paths; these are all of them.

#### `create_subdomain.py <name>`

| flag | default | what it does |
| --- | --- | --- |
| `--dry-run` | off | prints every call it would make, touches nothing |
| `--delete` | off | removes the subdomain instead of creating it; leaves the files |
| `--with-files` | off | with `--delete`: also move the document root to `~/.trash` |
| `--purge` | off | with `--with-files`: delete permanently instead of trashing |
| `--with-dns-api` | off | create a dedicated namecheap a record — usually unnecessary, see [dns](#dns) |
| `--skip-autossl` | off | skip the autossl trigger (the workflow passes it by default) |
| `--skip-https-redirect` | off | skip writing the `.htaccess` redirect block |
| `--skip-starter-page` | off | skip writing a placeholder `index.html` with the PostHog snippet; never overwrites an existing index anyway |
| `--skip-posthog` | off | write the placeholder `index.html` without the PostHog snippet: the subdomain isn't tracked |

#### `setup_autodeploy.py <project> --repo owner/name`

| flag | default | what it does |
| --- | --- | --- |
| `--repo` | **required** | the repo to deploy *from*, as `owner/name` |
| `--dry-run` | off | prints every call it would make, touches nothing |
| `--branch` | `master` | pushes to this branch trigger a deploy |
| `--ftp-user` | the project name | login half of the ftp account; the full login is `<login>@<project>.<root-domain>` |
| `--ftp-password` | generated | 24 random characters if omitted. passing it on the command line leaves it in your shell history |
| `--show-password` | off | print the generated password. for winscp; never use it in actions, where it lands in the run log |
| `--ftp-server` | `SERVER_IP` | the value written into the `FTP_SERVER` secret, if it differs from the env var |
| `--dir` | the project name | the ftp account's home, relative to the cpanel home. must match the subdomain's document root or the deploy lands where nothing serves it |
| `--server-dir` | `./` | subdirectory of the ftp home to publish into. a trailing `/` is added if you leave it off, because the action treats a bare name as a filename prefix |
| `--skip-secrets` | off | leave the repository secrets alone |
| `--skip-ftp` | off | leave the ftp account alone and skip the "already exists" check — the repair path, see below |
| `--skip-workflow-file` | off | print the workflow instead of committing it. useful when the token has no `workflow` scope |
| `--force` | off | replace a deploy workflow that's already there and different |
| `--skip-posthog-check` | off | don't scan the repo for the PostHog snippet — for sites created with `--skip-posthog` |
| `--delete` | off | tear the whole thing down: workflow, secrets, ftp account. never touches the files |

`--ftp-server` and `--dir` exist for the case where the account doesn't follow
the conventions the rest of the repo assumes — a project whose document root
isn't `~/<name>`, or a host reachable at something other than `SERVER_IP`.
neither is needed for a subdomain that `create_subdomain.py` made.

#### `setup_gitflow.py --repo owner/name`

| flag | default | what it does |
| --- | --- | --- |
| `--repo` | **required** | the repo to prepare, as `owner/name` |
| `--dry-run` | off | prints what it would do, touches nothing |
| `--branch` | `master` | the production branch; on an empty repo the initial commit lands here |
| `--develop` | `develop` | the integration branch to create from `--branch` |
| `--default-develop` | off | also make `--develop` the repo's default branch. needs admin rights on the repo |

## what a subdomain creation actually does

1. **cpanel** — creates the subdomain with its document root at `~/<subdomain>`
   (one level above `public_html`, not inside it)
2. **dns** — skipped by default, see below
3. **autossl** — skipped by default, see [ssl](#ssl)
4. **https redirect** — appends a `mod_rewrite` block to
   `~/<subdomain>/.htaccess`, creating the file if it isn't there
5. **starter page** — writes a minimal, noindex `index.html` carrying the
   PostHog snippet to the document root, so the subdomain is tracked from its
   first minute. never overwrites an existing `index.html` or `index.php`; if
   it can't tell whether one exists, it stops rather than guess.
   `--skip-posthog` writes the same page without the snippet

each step prints what it's doing with a `[step]` prefix, so a failed run tells
you exactly how far it got.

## what an auto-deploy setup actually does

0. **preflight** — everything that can be checked without changing anything:
   the target repo is reachable, the branch exists (or the repo is empty,
   see [below](#an-empty-repo)), no deploy workflow is
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

### an empty repo

a repo created on github and never pushed to has no branches at all, so there's
nothing to commit the workflow to and nothing to deploy. rather than failing
with "branch not found", the preflight tells that case apart from a mistyped
branch, and the run initializes the repo first — exactly what
[`setup_gitflow.py`](#what-a-gitflow-setup-actually-does) does: an initial
commit with a `README.md` on the deploy branch, then `develop` from it.

that's the run's first write, before the ftp account exists: if it fails,
nothing is left behind on the server, and if a later step fails the next run
finds an ordinary repo. once the workflow is committed, `develop` is
fast-forwarded to match, so it doesn't start out one commit behind.

two things worth knowing:

- the `README.md` is a real file in the repo, so the first deploy publishes it
  to the document root along with everything else. the starter page stays until
  the repo brings its own `index.html`
- deploying an empty repo *from* `develop` stops at the preflight: there'd be no
  production branch to start it from. run **setup gitflow** first, then this

### repairing an existing setup

the preflight refuses to run against a project whose ftp account already
exists, which is right for a fresh setup but unhelpful when you only want to
update the workflow file. `--skip-ftp` leaves the account alone and skips that
check, so:

```bash
python3 setup_autodeploy.py lab --repo you/lab --skip-ftp --skip-secrets --force
```

rewrites the workflow and nothing else. it needs `--skip-secrets` (or an
explicit `--ftp-password`), because without creating the account there's no
password to put in the secret — writing one anyway would leave `FTP_PASSWORD`
holding a value that opens nothing.

the same three are available as `skip_ftp` / `skip_secrets` / `force` inputs on
the **setup auto deploy** workflow, so the repair is a click too.

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

## what a gitflow setup actually does

[gitflow](https://www.atlassian.com/git/tutorials/comparing-workflows/gitflow-workflow)
keeps two long-lived branches instead of one: `master` only ever receives
releases, `develop` is where features come together.

1. **initial commit** — only if the repo has no commits: a `README.md`
   describing the two branches, on the production branch. github's git api
   (refs, trees, commits) answers 409 on an empty repo, so this goes through
   the contents api, the one way in — see [github api
   notes](github-api.md#an-empty-repo-is-a-different-api)
2. **`develop`** — created from the production branch, unless it's already
   there. an existing `develop` is never moved
3. **default branch** — only with `--default-develop`

what it can't do is the local half: the `feature/`, `release/` and `hotfix/`
prefixes of the `git flow` command live in each clone's `.git/config`, not on
github. after cloning, `git flow init -d` finds both branches and fills in the
defaults.

a repo that has commits but no production branch by that name stops the run
rather than guessing — pass `--branch` with the right one.

## dry runs

every network-touching step supports `--dry-run`, and genuinely makes **no**
calls when it's on — not even read-only ones. use it freely; it's the cheapest
way to check you typed the right name.

```
[cPanel] [dry-run] GET https://host:2083/execute/SubDomain/addsubdomain params={'domain': 'lab', ...}
[Namecheap] Step skipped: covered by the wildcard '*' record
[AutoSSL] Step skipped (--skip-autossl).
[HTTPS redirect] [dry-run] Would append to lab/.htaccess: ...
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
