# troubleshooting

## "variabili d'ambiente mancanti: ..."

the message names exactly which ones. locally, you probably didn't load the
file:

```bash
set -a; source .env; set +a
```

in actions, the secret isn't set, or is set under a different name — the names
are case-sensitive and must match [setup](setup.md) exactly.

## the run timed out while deleting

api2 is slow on this server and the operation **may well have gone through
anyway**. check cpanel → domains before retrying: a blind retry can act twice.

## "the domain ... does not belong to ..."

the subdomain isn't on the account — already deleted, a typo, or the wrong
`ROOT_DOMAIN`. deleting a folder through cpanel's file manager can also remove
the subdomain entry with it, so it may already be gone.

check what's actually there:

```bash
curl -s "https://$CPANEL_HOST:2083/execute/DomainInfo/list_domains" \
  -H "Authorization: cpanel $CPANEL_USER:$CPANEL_API_TOKEN" | python3 -m json.tool
```

## https shows a certificate warning

expected until autossl runs. without a certificate of its own the subdomain is
served the server's default one — which on a shared ip belongs to some other
site entirely, so you may even get redirected somewhere unrelated. plain
`http://` still redirects correctly; it's only the certificate that's missing.

see [ssl](usage.md#ssl) for enabling autossl.

## the subdomain doesn't resolve

the wildcard `*` record should make this immediate. check it still exists in
namecheap, and confirm what dns is actually returning:

```bash
dig +short lab.example.com
```

if that's empty, it's dns, not the script — the subdomain can exist in cpanel
and still not resolve.

## the delete workflow failed before doing anything

the `confirm` field has to match the subdomain name exactly. that's the guard
working: it runs before checkout, so nothing is installed and nothing is
touched.

## the folder is still there after deleting

`--delete` on its own leaves files alone by design. use `--with-files`, or
delete the folder from the file manager.

if you *did* pass `--with-files` and it's still there, check `~/.trash` — and
remember it may have been renamed to `<name>.1` if something with that name was
already in the trash.

## a run "succeeded" but nothing was deleted

`fileop` returns success for paths that don't exist, so a success line isn't
proof by itself. verify directly:

```bash
curl -s -G "https://$CPANEL_HOST:2083/execute/Fileman/list_files" \
  -H "Authorization: cpanel $CPANEL_USER:$CPANEL_API_TOKEN" \
  -d "dir=." -d "types=dir" | python3 -m json.tool
```

## the .htaccess step refused to run

it aborts rather than overwrite when it can't read the existing file — because
saving replaces the file wholesale, and an unreadable file is not the same as
an empty one. check the document root exists and the token can read it, then
re-run.

## namecheap

`--with-dns-api` is untested against a live api and the account doesn't meet
namecheap's api requirements. if you hit an ip error, the machine's public ip
has to be whitelisted in namecheap → api access — which is awkward from actions,
where the runner's ip changes every run.

## seeing what a run actually did

```bash
gh run list --workflow=create-subdomain.yml --limit 5
gh run view <run-id> --log
```

or just add `dry_run` and read the plan before committing to it.
