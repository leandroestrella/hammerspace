# cpanel api notes

things that cost real time to discover, kept here so nobody has to discover
them twice. everything below was verified against a live server, not read off
a documentation page — **except** the `Ftp` section at the bottom, which is
flagged as unverified until the first real run.

## uapi is missing whole categories of function

cpanel has two api generations. **uapi** (`/execute/<Module>/<function>`) is the
modern one — and it has holes big enough to matter:

| you want to | uapi has it? | what actually works |
| --- | --- | --- |
| create a subdomain | yes | `SubDomain::addsubdomain` |
| **delete** a subdomain | **no** | api2 `SubDomain::delsubdomain` |
| read a file | yes | `Fileman::get_file_content` |
| write a file | yes | `Fileman::save_file_content` |
| list files | yes | `Fileman::list_files` |
| **delete** a file | **no** | api2 `Fileman::fileop` |
| list domains + document roots | yes | `DomainInfo::domains_data` |
| create an ftp account | yes | `Ftp::add_ftp` |
| **delete** an ftp account | **yes** | `Ftp::delete_ftp` — the one delete uapi does expose |

the `SubDomain` module exposes only `addsubdomain` and `changedocroot`. probing
`Fileman` for `mkdir`, `delete_files`, `trash_files`, `unlink`, `remove_files`
and `fileop` returns *"could not find the function"* for every one of them.

so anything destructive goes through the deprecated **api2** endpoint
(`/json-api/cpanel`), which is still enabled on most servers:

```
GET /json-api/cpanel
  ?cpanel_jsonapi_user=<user>
  &cpanel_jsonapi_apiversion=2
  &cpanel_jsonapi_module=Fileman
  &cpanel_jsonapi_func=fileop
  &op=trash            # or unlink
  &sourcefiles=/home/<user>/<dir>
  &doubledecode=0
```

probing for a function name is cheap, by the way: the error message names the
function it couldn't find, so guessing at candidates is a legitimate way to map
what a module actually exposes.

## api2 is slow, and a timeout doesn't mean failure

`delsubdomain` on this server regularly takes longer than 30 seconds and can
exceed 90. crucially **the server often completes the operation anyway** — the
response just never arrives.

both callers use a 90s timeout and translate a timeout into a message telling
you to check cpanel before retrying, rather than letting a traceback through. a
blind retry risks acting twice.

## fileop reports success for paths that don't exist

`op=unlink` on a path that isn't there still returns `result: 1`. a "deleted"
log line is therefore **not** proof anything was deleted.

this is why the delete path reads the real document root from
`DomainInfo::domains_data` first: that path exists only if the subdomain does.

## trash renames on collision

`op=trash` on `~/lab` when a `lab` is already sitting in `~/.trash` puts it there
as `lab.1`. cleanup that assumes the original name silently misses it.

also: `Fileman::empty_trash` exists, but it empties the **whole** trash —
including whatever the user put there themselves. never use it to clean up
after a script.

## document roots are not guessable

cpanel lets any subdomain point anywhere. on this account they sit at
`~/<subdomain>`, one level *above* `public_html` — but an earlier version of
this script created them at `~/public_html/<subdomain>`, and the cpanel ui
lets you type anything at all.

`DomainInfo::domains_data` is the source of truth. per domain it returns:

```
domain          lab.example.com
documentroot    /home/user/lab
homedir         /home/user
ssl_redirect    false
```

read it **before** deleting the subdomain — afterwards the domain is gone from
the listing and the path is unrecoverable.

that `ssl_redirect` field is worth a look if the `.htaccess` approach to forcing
https is ever revisited; it looks like the state behind the domains-ui toggle,
though nothing here has tested writing it.

## get_file_content returns plain text, not base64

it looks like it should be base64. it isn't. an earlier version ran the content
through `base64.b64decode` and crashed on a real `.htaccess` the moment the
content contained a curly quote.

for a file that genuinely doesn't exist you get `status: 0` and an error
reading *"The file ... does not exist for the account."* — which is worth
distinguishing from a read that failed for some other reason, because
`save_file_content` **replaces the entire file**. treating an unreadable file as
an empty one means overwriting whatever was in it.

## forcing https

there's no confirmed public uapi call equivalent to the "force https redirect"
toggle in the domains ui. writing a `mod_rewrite` block into the subdomain's
`.htaccess` achieves the same result and is what this tool does:

```apache
RewriteEngine On
RewriteCond %{HTTPS} off
RewriteRule ^(.*)$ https://%{HTTP_HOST}%{REQUEST_URI} [L,R=301]
```

`SSL::toggle_ssl_redirect_for_domains` shows up in third-party api docs as the
likely real function, but it has never been tested here.

## autossl needs a different token entirely

it's a whm (root/reseller) operation on port **2087**, not a cpanel user
operation on 2083. a cpanel token will not authenticate it, no matter how it's
formatted. see [setup](setup.md).

## the `Ftp` module is the exception to all of the above

verified against the live server on the first real run, same as the rest of
this page.

after `SubDomain` and `Fileman`, the expectation was another hunt through api2.
`Ftp` doesn't need it: `add_ftp`, `delete_ftp` and `list_ftp` all exist in uapi,
delete included.

```
POST /execute/Ftp/add_ftp
  user=<login>          # just the login half — cpanel appends @<domain>
  domain=<fqdn>
  pass=<password>
  homedir=<path>        # relative to the account home
  quota=0               # 0 means unlimited
```

three things worth knowing:

- **`user` is the login half only.** the account that ends up existing is
  `<user>@<domain>`, and that full string is what an ftp client (and the
  `FTP_USERNAME` secret) needs. passing an already-`@`-qualified value creates
  something doubly qualified.
- **`homedir` is relative to the account home**, the same way `addsubdomain`'s
  `dir` is. it has to be the subdomain's document root, or the deploy lands in
  a folder nothing serves.
- **`destroy=1` on `delete_ftp` deletes the account's home directory too** —
  which here is the live site. `setup_autodeploy.py` hardcodes `destroy=0` and
  leaves file deletion to `create_subdomain.py --delete --with-files`, which
  reads the real document root from cpanel before touching anything.

`list_ftp` also returns the account's own login, the anonymous `ftp` account and
the log account. and it does **not** populate `serverlogin` at all on this
server — every row comes back with `serverlogin: null` and the full
`user@domain` string in `user`. reading only the field the docs suggest would
find nothing, ever. the existence check reads both.

## `.htaccess` already has cpanel's php directives in it

the `.htaccess` in a fresh subdomain's document root isn't empty — cpanel puts a
`# BEGIN cPanel-generated php ini directives` block there. appending the
force-https rules rather than replacing the file isn't just politeness toward a
user-edited file; it's required from the very first run.

worth knowing alongside the deploy tool: `FTP-Deploy-Action` leaves that file
alone, so the redirect survives a deploy. verified — `http://` still returns a
301 after publishing.
