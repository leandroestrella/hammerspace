# cpanel api notes

things that cost real time to discover, kept here so nobody has to discover
them twice. everything below was verified against a live server, not read off
a documentation page.

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
