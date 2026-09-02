# github api notes

what `setup_autodeploy.py` has to get right when it writes to a repository
that isn't this one.

## secrets can't be written in plain text

there is no "set this secret to this string" call. github publishes a public
key per repository and accepts only values sealed against it:

```
GET  /repos/{owner}/{repo}/actions/secrets/public-key   -> {key, key_id}
PUT  /repos/{owner}/{repo}/actions/secrets/{name}       <- {encrypted_value, key_id}
```

`encrypted_value` is a libsodium **sealed box** (anonymous, no sender key),
base64-encoded — which is why `pynacl` is a dependency for what otherwise looks
like a pure-http script.

the failure mode is quiet: a value encrypted wrong is still valid base64, so
github accepts it with a `201`. nothing goes wrong until the deploy workflow
runs and the ftp server rejects the password, several minutes and one repo
away from the actual mistake. the tests round-trip a real keypair for exactly
this reason.

`201` means created, `204` means an existing secret was overwritten. both are
success, and the script says which happened — overwriting a secret somebody set
by hand is worth seeing in the log.

## the `GITHUB_` prefix is reserved for secret names

you cannot create a repository secret called `GITHUB_PAT`. github rejects the
whole `GITHUB_` prefix. the workflows here store it as `DEPLOY_GITHUB_PAT` and
map it onto the env var the script reads.

the env var itself is deliberately *not* called `GITHUB_TOKEN`: that name is
always populated inside actions, with a token scoped to the repo running the
job. reusing it would mean silently picking up a token that cannot write to the
target repo, and getting a 404 that looks like a typo.

## 404 doesn't mean "doesn't exist"

github returns `404` for a private repository the token can't see, rather than
`403`, so it doesn't leak the existence of private repos. so a 404 here means
*either* a wrong name *or* a token without access, and the message says so —
guessing wrong costs a round of re-checking the spelling of something spelled
correctly.

## writing under `.github/workflows/` needs its own scope

`PUT /repos/{owner}/{repo}/contents/.github/workflows/<file>` is refused with a
`403` unless the token carries `workflow` (classic pat) or `Workflows: write`
(fine-grained) — even when it can already write every other path in the repo.
the message mentions "workflow", which is what the script matches on to explain
the fix rather than printing a bare 403.

## committing the workflow triggers a deploy

the file says "run on push to `master`". the commit that creates it is a push to
`master`. so the setup run's last action publishes the repo — intentional as a
smoke test, but it means the operation isn't dry at the end.

it's also the reason the ftp account and the secrets are created *first*: by the
time the workflow exists, everything it needs already does.

## `permissions: {}` breaks checkout on a private repo

hammerspace's own workflows declare `permissions: {}` — they talk to cpanel and
github with their own credentials and want nothing from the automatic
`GITHUB_TOKEN`. copying that line into the *generated* deploy workflow was
wrong: `actions/checkout` reads the repository through that token, and on a
private repo with no permissions it fails as:

```
remote: Repository not found.
fatal: repository 'https://github.com/owner/repo/' not found
```

which reads like a typo or a deleted repo, not a permissions problem. it also
would not have shown up on a public repo, where the anonymous clone succeeds.

the generated workflow declares `contents: read` and nothing else. found on the
first real deploy.

## writing a file replaces it

the contents api takes the existing blob `sha` and overwrites. same shape as
`Fileman::save_file_content` on the cpanel side, and the same handling: read
first, no-op if identical, refuse if it differs. a deploy workflow already in
the repo may well have been edited by hand.

the one case a read can't catch: the doc's manual procedure names the file
`deployTocPanel.yml`, this one writes `deploy-to-cpanel.yml`. different paths,
so nothing is overwritten and *both* run — two deploys per push, racing over the
same ftp directory. the preflight looks for the old name specifically and stops.
