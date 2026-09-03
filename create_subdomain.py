#!/usr/bin/env python3
"""
create_subdomain.py

Automatizza la procedura descritta nella pagina Confluence "Subdomain":

  1. cPanel  -> Domains -> Subdomains: crea il sottodominio
  2. Namecheap -> Advanced DNS: aggiunge un A Record che punta a SERVER_IP
     (SALTATO DI DEFAULT: con un record wildcard "*" -> SERVER_IP creato una
     tantum su Namecheap, ogni sottodominio risolve già da solo. Passa
     --with-dns-api solo se vuoi comunque creare un record dedicato via API -
     serve un account Namecheap che soddisfi i requisiti per l'API: saldo
     >= $50, 20+ domini, o $50 di acquisti negli ultimi 2 anni)
  3. cPanel  -> SSL/TLS Status: avvia AutoSSL (richiede un token WHM, non solo cPanel utente)
  4. cPanel  -> Domains: forza il redirect HTTPS (via .htaccess, vedi nota in fondo al file)

Uso:
    python3 create_subdomain.py lab
    python3 create_subdomain.py lab --dry-run
    python3 create_subdomain.py lab --skip-autossl --skip-https-redirect
    python3 create_subdomain.py lab --with-dns-api   # solo se serve un record dedicato

Rimozione:
    python3 create_subdomain.py lab --delete                        # solo il sottodominio
    python3 create_subdomain.py lab --delete --with-files           # + cartella nel cestino
    python3 create_subdomain.py lab --delete --with-files --purge   # + cartella cancellata

Le credenziali si passano tramite variabili d'ambiente (vedi .env.example
nella stessa cartella). Non salvare mai le API key nel codice o in git.
"""

import argparse
import os
import re
import sys
import time
import xml.etree.ElementTree as ET

import requests

# --------------------------------------------------------------------------- #
# Configurazione da variabili d'ambiente
# --------------------------------------------------------------------------- #

CPANEL_HOST = os.environ.get("CPANEL_HOST")            # es. server.leandroestrella.com
CPANEL_USER = os.environ.get("CPANEL_USER")             # utente cPanel
CPANEL_API_TOKEN = os.environ.get("CPANEL_API_TOKEN")   # Security -> Manage API Tokens
ROOT_DOMAIN = os.environ.get("ROOT_DOMAIN")   # es. tuodominio.com
SERVER_IP = os.environ.get("SERVER_IP")        # IP a cui deve puntare il sottodominio

# Facoltativo: serve solo per lo step AutoSSL, che è un'operazione a livello
# WHM (server), non disponibile con il solo token utente cPanel.
WHM_HOST = os.environ.get("WHM_HOST", CPANEL_HOST)
WHM_USER = os.environ.get("WHM_USER", "root")
WHM_API_TOKEN = os.environ.get("WHM_API_TOKEN")

NAMECHEAP_API_USER = os.environ.get("NAMECHEAP_API_USER")
NAMECHEAP_API_KEY = os.environ.get("NAMECHEAP_API_KEY")
NAMECHEAP_USERNAME = os.environ.get("NAMECHEAP_USERNAME", NAMECHEAP_API_USER)
NAMECHEAP_CLIENT_IP = os.environ.get("NAMECHEAP_CLIENT_IP")  # IP whitelisted in Namecheap
NAMECHEAP_API_BASE = os.environ.get(
    "NAMECHEAP_API_BASE", "https://api.namecheap.com/xml.response"
)


def log(step, msg):
    print(f"[{step}] {msg}")


def require(vars_dict, names):
    missing = [n for n in names if not vars_dict.get(n)]
    if missing:
        sys.exit(
            "Missing environment variables: "
            + ", ".join(missing)
            + "\nSee .env.example for the full list."
        )


# --------------------------------------------------------------------------- #
# 1. cPanel: creazione sottodominio (UAPI SubDomain::addsubdomain)
# --------------------------------------------------------------------------- #

def cpanel_headers():
    return {"Authorization": f"cpanel {CPANEL_USER}:{CPANEL_API_TOKEN}"}


# Nome di sottodominio valido: lettere, cifre e trattini, niente trattino
# iniziale/finale (RFC 1123), max 63 caratteri per label.
SUBDOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def valida_subdomain(subdomain):
    """Rifiuta i nomi non validi PRIMA di usarli in chiamate o path.

    Non e' solo cosmetica: il nome finisce in un path di cancellazione file
    e negli argomenti passati dal workflow, quindi un valore inatteso non
    deve nemmeno arrivare a quel punto.
    """
    if not SUBDOMAIN_RE.match(subdomain or ""):
        raise ValueError(
            f"Invalid subdomain name: {subdomain!r}. "
            "Only lowercase letters, digits and hyphens (not leading or trailing), max 63 characters."
        )
    return subdomain


def api2_call(module, func, extra_params, dove_controllare, timeout=90):
    """Chiama la vecchia API2 (/json-api/cpanel).

    Serve per le operazioni che la UAPI non espone affatto: la cancellazione
    di un sottodominio e quella dei file. Su questo server l'endpoint e'
    lento e puo' sforare il timeout ANCHE SE l'operazione va comunque a
    buon fine lato server: per questo il timeout viene tradotto in un
    messaggio che dice esplicitamente di controllare prima di riprovare,
    invece di lasciar passare un traceback.
    """
    url = f"https://{CPANEL_HOST}:2083/json-api/cpanel"
    params = {
        "cpanel_jsonapi_user": CPANEL_USER,
        "cpanel_jsonapi_apiversion": 2,
        "cpanel_jsonapi_module": module,
        "cpanel_jsonapi_func": func,
    }
    params.update(extra_params)

    try:
        resp = requests.get(url, headers=cpanel_headers(), params=params, timeout=timeout)
    except requests.exceptions.Timeout:
        raise RuntimeError(
            f"Timed out waiting for a response (this server's API2 endpoint is slow). "
            f"The operation may well have completed anyway: check {dove_controllare} "
            f"before retrying, so you don't act twice."
        )
    return parse_api2_result(resp.json(), dove_controllare)


def parse_api2_result(data, dove_controllare):
    """Estrae l'esito da una risposta API2, alzando un errore parlante."""
    result = (data.get("cpanelresult", {}).get("data") or [{}])[0]
    if result.get("result") not in (1, "1"):
        motivo = result.get("reason") or data.get("cpanelresult", {}).get("error") or data
        raise RuntimeError(
            f"Operation failed: {motivo}. Otherwise do it by hand from {dove_controllare}."
        )
    return result


def create_subdomain(subdomain, dry_run=False):
    require(
        {
            "CPANEL_HOST": CPANEL_HOST,
            "CPANEL_USER": CPANEL_USER,
            "CPANEL_API_TOKEN": CPANEL_API_TOKEN,
            "ROOT_DOMAIN": ROOT_DOMAIN,
        },
        ["CPANEL_HOST", "CPANEL_USER", "CPANEL_API_TOKEN", "ROOT_DOMAIN"],
    )
    url = f"https://{CPANEL_HOST}:2083/execute/SubDomain/addsubdomain"
    params = {
        "domain": subdomain,
        "rootdomain": ROOT_DOMAIN,
        # Document root un livello sopra public_html (~/<subdomain>, non
        # ~/public_html/<subdomain>) — confermato dal setup reale dell'account.
        "dir": subdomain,
    }
    log("cPanel", f"Creating subdomain {subdomain}.{ROOT_DOMAIN} -> {params['dir']}")
    if dry_run:
        log("cPanel", f"[dry-run] GET {url} params={params}")
        return True

    resp = requests.get(url, headers=cpanel_headers(), params=params, timeout=30)
    data = resp.json()
    if not data.get("status"):
        errors = data.get("errors") or [data.get("error")]
        raise RuntimeError(f"Subdomain creation failed: {errors}")
    log("cPanel", "Subdomain created.")
    return True


def delete_subdomain(subdomain, dry_run=False):
    require(
        {
            "CPANEL_HOST": CPANEL_HOST,
            "CPANEL_USER": CPANEL_USER,
            "CPANEL_API_TOKEN": CPANEL_API_TOKEN,
            "ROOT_DOMAIN": ROOT_DOMAIN,
        },
        ["CPANEL_HOST", "CPANEL_USER", "CPANEL_API_TOKEN", "ROOT_DOMAIN"],
    )
    # NB: la UAPI (endpoint /execute/) NON espone una funzione di cancellazione
    # nel modulo SubDomain (solo addsubdomain e changedocroot). L'unica via
    # rimasta è la vecchia API2 (deprecata ma ancora attiva sulla maggior
    # parte dei server) tramite l'endpoint /json-api/cpanel.
    params = {"domain": f"{subdomain}.{ROOT_DOMAIN}"}
    log("cPanel", f"Removing subdomain {subdomain}.{ROOT_DOMAIN} (via API2, legacy)")
    if dry_run:
        log("cPanel", f"[dry-run] API2 SubDomain::delsubdomain params={params}")
        return True

    api2_call("SubDomain", "delsubdomain", params, "cPanel -> Domains")
    log("cPanel", "Subdomain removed. (Files in the document root are NOT deleted automatically.)")
    return True


def get_docroot(subdomain, dry_run=False):
    """Legge (document root, home) REALI del sottodominio da DomainInfo::domains_data.

    Non si assume ~/<subdomain>: cPanel permette qualsiasi document root, e
    i sottodomini creati da versioni precedenti di questo script stanno
    sotto public_html/<nome>. Cancellare una cartella basandosi su un path
    presunto puo' colpire una directory scorrelata e lasciare i file veri
    dove sono.

    NB: va chiamata PRIMA di delete_subdomain - dopo la rimozione il
    sottodominio non e' piu' elencato e la docroot non e' piu' recuperabile.
    """
    if dry_run:
        return None, None

    url = f"https://{CPANEL_HOST}:2083/execute/DomainInfo/domains_data"
    resp = requests.get(
        url, headers=cpanel_headers(), params={"format": "list"}, timeout=30
    )
    data = resp.json()
    if not data.get("status"):
        raise RuntimeError(f"Could not read the account's domains: {data.get('errors')}")

    fqdn = f"{subdomain}.{ROOT_DOMAIN}"
    return estrai_docroot(data.get("data"), fqdn)


def estrai_docroot(righe, fqdn):
    """Cerca (documentroot, homedir) del dominio richiesto in domains_data."""
    for row in righe or []:
        if not isinstance(row, dict) or row.get("domain") != fqdn:
            continue
        docroot = row.get("documentroot")
        home = row.get("homedir")
        if not docroot or not home:
            raise RuntimeError(f"cPanel reports no document root and home for {fqdn}.")
        return docroot, home
    raise RuntimeError(
        f"Subdomain {fqdn} not found among the account's domains: "
        f"cannot determine its document root."
    )


def assert_docroot_sicura(docroot, home):
    """Impedisce che una docroot inattesa faccia cancellare la cosa sbagliata.

    Qui si cancellano file veri: la docroot deve stare DENTRO la home e non
    puo' essere la home stessa ne' public_html (che e' la document root del
    dominio principale - cancellarla porterebbe giu' il sito primario).
    """
    percorso = (docroot or "").rstrip("/")
    home = (home or "").rstrip("/")
    if not percorso or not home:
        raise RuntimeError("Document root or home could not be determined: not proceeding.")
    if not percorso.startswith(home + "/"):
        raise RuntimeError(
            f"Document root {percorso!r} is outside the home {home!r}: not proceeding."
        )
    relativo = percorso[len(home) + 1:]
    if not relativo or relativo == "public_html" or ".." in relativo.split("/"):
        raise RuntimeError(
            f"Document root is not safe to delete: {percorso!r}."
        )
    return percorso


def delete_docroot(subdomain, docroot=None, home=None, purge=False, dry_run=False):
    """Cancella la cartella document root del sottodominio.

    Come per delsubdomain, la UAPI non espone NESSUNA funzione di
    cancellazione file (verificato: mkdir/delete_files/trash_files/unlink
    non esistono nel modulo Fileman). L'unica via e' di nuovo la vecchia
    API2 tramite /json-api/cpanel, con Fileman::fileop:
      - op=trash  -> sposta in ~/.trash, recuperabile da cPanel (default)
      - op=unlink -> cancellazione definitiva (--purge)
    """
    require(
        {"CPANEL_HOST": CPANEL_HOST, "CPANEL_USER": CPANEL_USER, "CPANEL_API_TOKEN": CPANEL_API_TOKEN},
        ["CPANEL_HOST", "CPANEL_USER", "CPANEL_API_TOKEN"],
    )

    op = "unlink" if purge else "trash"
    modo = "PERMANENT deletion" if purge else "move to the trash (~/.trash)"

    if dry_run:
        log(
            "Docroot",
            f"[dry-run] {modo} of the document root of {subdomain}.{ROOT_DOMAIN} "
            f"(API2 Fileman::fileop op={op}; the real path is read from cPanel at run time)",
        )
        return True

    if not docroot or not home:
        raise RuntimeError("Document root or home not provided: not deleting anything.")

    target = assert_docroot_sicura(docroot, home)
    log("Docroot", f"{modo} of {target}")

    api2_call(
        "Fileman",
        "fileop",
        {"op": op, "sourcefiles": target, "doubledecode": 0},
        "cPanel -> File Manager",
    )
    if purge:
        log("Docroot", "Folder deleted permanently.")
    else:
        log("Docroot", "Folder moved to the trash (restore it from cPanel -> File Manager -> Trash).")
    return True


# --------------------------------------------------------------------------- #
# 2. Namecheap: aggiunta record A (domains.dns.getHosts + domains.dns.setHosts)
# --------------------------------------------------------------------------- #

def namecheap_params(command, extra=None):
    p = {
        "ApiUser": NAMECHEAP_API_USER,
        "ApiKey": NAMECHEAP_API_KEY,
        "UserName": NAMECHEAP_USERNAME,
        "ClientIp": NAMECHEAP_CLIENT_IP,
        "Command": command,
    }
    if extra:
        p.update(extra)
    return p


def costruisci_set_hosts(hosts, sld, tld, email_type=None):
    """Costruisce i parametri di setHosts a partire dai record da riscrivere.

    Estratta come funzione pura perche' e' il punto in cui un campo dimenticato
    si traduce in record DNS persi: e' testabile senza toccare la rete.
    """
    extra = {"SLD": sld, "TLD": tld}
    if email_type:
        extra["EmailType"] = email_type
    for i, h in enumerate(hosts, start=1):
        extra[f"HostName{i}"] = h["HostName"]
        extra[f"RecordType{i}"] = h["RecordType"]
        extra[f"Address{i}"] = h["Address"]
        extra[f"TTL{i}"] = h["TTL"]
        if h["RecordType"] == "MX":
            extra[f"MXPref{i}"] = h.get("MXPref", "10")
        if h["RecordType"] == "CAA":
            # Senza Flags/Tag un record CAA rimandato indietro perde significato.
            if h.get("Flags") is not None:
                extra[f"Flags{i}"] = h["Flags"]
            if h.get("Tag") is not None:
                extra[f"Tag{i}"] = h["Tag"]
    return extra


def add_dns_record(subdomain, dry_run=False):
    require(
        {
            "NAMECHEAP_API_USER": NAMECHEAP_API_USER,
            "NAMECHEAP_API_KEY": NAMECHEAP_API_KEY,
            "NAMECHEAP_CLIENT_IP": NAMECHEAP_CLIENT_IP,
            "ROOT_DOMAIN": ROOT_DOMAIN,
            "SERVER_IP": SERVER_IP,
        },
        ["NAMECHEAP_API_USER", "NAMECHEAP_API_KEY", "NAMECHEAP_CLIENT_IP", "ROOT_DOMAIN", "SERVER_IP"],
    )
    sld, _, tld = ROOT_DOMAIN.partition(".")
    if dry_run:
        log(
            "Namecheap",
            f"[dry-run] Would read and rewrite the DNS records of {ROOT_DOMAIN} "
            f"to add {subdomain} -> {SERVER_IP} (no call made).",
        )
        return True
    # NB: Namecheap non ha un "addHost": setHosts SOSTITUISCE l'intera lista
    # di record del dominio. Quindi leggiamo prima quelli esistenti (getHosts)
    # e li reinviamo tutti insieme al nuovo record.
    log("Namecheap", f"Reading the existing DNS records for {ROOT_DOMAIN}")
    get_resp = requests.get(
        NAMECHEAP_API_BASE,
        params=namecheap_params("namecheap.domains.dns.getHosts", {"SLD": sld, "TLD": tld}),
        timeout=30,
    )
    get_resp.raise_for_status()
    ns = {"nc": "http://api.namecheap.com/xml.response"}
    root = ET.fromstring(get_resp.text)
    if root.get("Status") != "OK":
        errs = [e.text for e in root.findall(".//nc:Errors/nc:Error", ns)]
        raise RuntimeError(f"getHosts failed: {errs}")

    # setHosts riscrive TUTTI i record del dominio: quello che non viene
    # rimandato indietro sparisce. Quindi il round-trip deve conservare anche
    # i campi che non ci interessano direttamente (Flags/Tag dei CAA), non
    # solo nome/tipo/indirizzo.
    hosts = []
    for h in root.findall(".//nc:host", ns):
        hosts.append(
            {
                "HostName": h.get("Name"),
                "RecordType": h.get("Type"),
                "Address": h.get("Address"),
                "TTL": h.get("TTL"),
                "MXPref": h.get("MXPref", "10"),
                "Flags": h.get("Flags"),
                "Tag": h.get("Tag"),
                "IsDDNSEnabled": (h.get("IsDDNSEnabled") or "").lower() == "true",
            }
        )

    # L'API setHosts non ha un parametro per il flag Dynamic DNS: se un record
    # ce l'ha attivo, il round-trip lo perde. Meglio fermarsi e lasciare che
    # sia una scelta esplicita, invece di degradare un record in silenzio.
    ddns = [h["HostName"] for h in hosts if h["IsDDNSEnabled"] and h["HostName"] != subdomain]
    if ddns:
        raise RuntimeError(
            f"Records {ddns} have Dynamic DNS enabled and setHosts has no way to preserve "
            f"it: rewriting them would downgrade them to static records. Add the record "
            f"by hand from the Namecheap panel, or remove the DDNS flag if you no "
            f"longer need it."
        )

    # EmailType NON e' opzionale in pratica: se non viene rimandato indietro,
    # Namecheap puo' resettare l'instradamento email del dominio (MX inclusi).
    email_type = None
    hosts_result = root.find(".//nc:DomainDNSGetHostsResult", ns)
    if hosts_result is not None:
        email_type = hosts_result.get("EmailType")

    # Rimuove un eventuale record preesistente con lo stesso nome, poi aggiunge quello nuovo
    hosts = [h for h in hosts if h["HostName"] != subdomain]
    hosts.append(
        {
            "HostName": subdomain,
            "RecordType": "A",
            "Address": SERVER_IP,
            "TTL": "60",  # "Automatic" nell'interfaccia corrisponde al TTL minimo
            "MXPref": "10",
            "Flags": None,
            "Tag": None,
            "IsDDNSEnabled": False,
        }
    )

    extra = costruisci_set_hosts(hosts, sld, tld, email_type)

    log("Namecheap", f"Writing {len(hosts)} records (including the new A record for '{subdomain}')")
    set_resp = requests.get(
        NAMECHEAP_API_BASE,
        params=namecheap_params("namecheap.domains.dns.setHosts", extra),
        timeout=30,
    )
    set_resp.raise_for_status()
    root = ET.fromstring(set_resp.text)
    if root.get("Status") != "OK":
        errs = [e.text for e in root.findall(".//nc:Errors/nc:Error", ns)]
        raise RuntimeError(f"setHosts failed: {errs}")
    ok = root.find(".//nc:DomainDNSSetHostsResult", ns)
    if ok is None or ok.get("IsSuccess") != "true":
        raise RuntimeError("setHosts did not confirm success.")
    log("Namecheap", f"A record {subdomain}.{ROOT_DOMAIN} -> {SERVER_IP} created.")
    return True


# --------------------------------------------------------------------------- #
# 3. cPanel/WHM: AutoSSL (WHM API1 start_autossl_check)
# --------------------------------------------------------------------------- #

def whm_headers():
    return {"Authorization": f"whm {WHM_USER}:{WHM_API_TOKEN}"}


def run_autossl(dry_run=False, wait_seconds=0):
    if not WHM_API_TOKEN:
        log(
            "AutoSSL",
            "WHM_API_TOKEN is not set: skipping this step (it needs a WHM/root token, "
            "which is not the same thing as the cPanel user token - see .env.example).",
        )
        return False
    url = f"https://{WHM_HOST}:2087/json-api/start_autossl_check"
    params = {"user": CPANEL_USER, "api.version": 1}
    log("AutoSSL", f"Starting AutoSSL for user {CPANEL_USER}")
    if dry_run:
        log("AutoSSL", f"[dry-run] GET {url} params={params}")
        return True

    resp = requests.get(url, headers=whm_headers(), params=params, timeout=30)
    data = resp.json()
    if data.get("metadata", {}).get("result") != 1:
        raise RuntimeError(f"start_autossl_check failed: {data.get('metadata')}")
    log("AutoSSL", "AutoSSL check started. Certificate issuance is asynchronous: "
                    "check WHM > SSL/TLS Status in a few minutes.")
    if wait_seconds:
        log("AutoSSL", f"Waiting {wait_seconds}s before continuing...")
        time.sleep(wait_seconds)
    return True


# --------------------------------------------------------------------------- #
# 4. Force HTTPS Redirect (via .htaccess, UAPI Fileman)
#
# NOTA: non esiste una singola chiamata UAPI pubblica e documentata che
# corrisponda 1:1 al toggle "Force HTTPS Redirect" mostrato nell'interfaccia
# Domains di cPanel (il comportamento interno dipende dalla versione/tema).
# L'equivalente affidabile e documentato è scrivere le regole di redirect
# in un .htaccess nella document root del sottodominio - è il meccanismo che
# quel toggle applica sotto il cofano nelle versioni cPanel più comuni.
# Verifica comunque il risultato: Domains -> il sottodominio dovrebbe
# mostrare il redirect attivo.
# --------------------------------------------------------------------------- #

HTACCESS_SNIPPET = """
# --- BEGIN force-https (added automatically) ---
RewriteEngine On
RewriteCond %{HTTPS} off
RewriteRule ^(.*)$ https://%{HTTP_HOST}%{REQUEST_URI} [L,R=301]
# --- END force-https ---
"""


def file_inesistente(data):
    """True se l'errore di get_file_content dice che il file non c'e'.

    Verificato sul server: per un file mancante l'API risponde status 0 con
    un errore tipo "The file ... does not exist for the account.". Va
    distinto da un errore di lettura vero, perche' nel primo caso si puo'
    creare il file da zero, nel secondo no.
    """
    errori = data.get("errors") or []
    return any("does not exist" in str(e) for e in errori)


def force_https_redirect(subdomain, dry_run=False):
    require(
        {"CPANEL_HOST": CPANEL_HOST, "CPANEL_USER": CPANEL_USER, "CPANEL_API_TOKEN": CPANEL_API_TOKEN},
        ["CPANEL_HOST", "CPANEL_USER", "CPANEL_API_TOKEN"],
    )
    # Qui il path non e' un'assunzione: questa funzione viene eseguita solo
    # subito dopo create_subdomain, che ha appena impostato la document root
    # a <subdomain>. Su un sottodominio preesistente andrebbe invece letta
    # con get_docroot (come fa il percorso di cancellazione).
    doc_root = subdomain
    log("HTTPS redirect", f"Writing redirect rules to {doc_root}/.htaccess")
    if dry_run:
        log("HTTPS redirect", f"[dry-run] Would append to {doc_root}/.htaccess:\n{HTACCESS_SNIPPET}")
        return True

    read_url = f"https://{CPANEL_HOST}:2083/execute/Fileman/get_file_content"
    resp = requests.get(
        read_url,
        headers=cpanel_headers(),
        params={"dir": doc_root, "file": ".htaccess"},
        timeout=30,
    )
    # save_file_content SOSTITUISCE l'intero file: se la lettura fallisce e si
    # procede lo stesso, un .htaccess gia' popolato (regole WordPress, redirect
    # custom...) verrebbe rimpiazzato dal solo blocco force-https. Quindi si
    # distingue "il file non esiste" (normale su un sottodominio nuovo, si
    # parte da vuoto) da "non sono riuscito a leggerlo" (ci si ferma).
    existing = ""
    data = resp.json() if resp.ok else {}
    if data.get("status"):
        existing = (data.get("data") or {}).get("content", "")
    elif not file_inesistente(data):
        raise RuntimeError(
            f"Could not read {doc_root}/.htaccess: {data.get('errors') or resp.status_code}. "
            "Not proceeding: that would overwrite an existing file without knowing what is in it."
        )

    if "force-https" in existing:
        log("HTTPS redirect", ".htaccess already contains the force-https block, nothing to do.")
        return True

    new_content = existing + HTACCESS_SNIPPET

    save_url = f"https://{CPANEL_HOST}:2083/execute/Fileman/save_file_content"
    resp = requests.post(
        save_url,
        headers=cpanel_headers(),
        data={"dir": doc_root, "file": ".htaccess", "content": new_content},
        timeout=30,
    )
    data = resp.json()
    if not data.get("status"):
        raise RuntimeError(f"Writing .htaccess failed: {data.get('errors')}")
    log("HTTPS redirect", "Forced HTTPS redirect enabled via .htaccess.")
    return True


# --------------------------------------------------------------------------- #
# Orchestrazione
# --------------------------------------------------------------------------- #

def main():
    parser = argparse.ArgumentParser(description="Automate creating a subdomain on cPanel")
    parser.add_argument("subdomain", help="Subdomain name, e.g. 'lab'")
    parser.add_argument("--dry-run", action="store_true", help="Print every call it would make, and make none")
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Remove the subdomain from cPanel instead of creating it. Files in "
             "the document root are left alone; see --with-files.",
    )
    parser.add_argument(
        "--with-files",
        action="store_true",
        help="With --delete only: also delete the document root folder "
             "(~/<subdomain>), moving it to the ~/.trash bin.",
    )
    parser.add_argument(
        "--purge",
        action="store_true",
        help="With --with-files only: delete the folder PERMANENTLY instead of "
             "moving it to the trash.",
    )
    parser.add_argument(
        "--with-dns-api",
        action="store_true",
        help="Call the Namecheap API to create a dedicated A record anyway "
             "(unnecessary if you have a wildcard '*' -> SERVER_IP, see below).",
    )
    parser.add_argument(
        "--skip-autossl", action="store_true",
        help="Skip the AutoSSL trigger (it needs a WHM token).",
    )
    parser.add_argument(
        "--skip-https-redirect", action="store_true",
        help="Skip writing the force-https block into .htaccess.",
    )
    args = parser.parse_args()

    subdomain = args.subdomain.strip().lower()
    try:
        valida_subdomain(subdomain)
    except ValueError as e:
        parser.error(str(e))

    # I flag distruttivi hanno senso solo in combinazione con --delete:
    # meglio fallire subito che ignorarli in silenzio.
    if args.with_files and not args.delete:
        parser.error("--with-files is only used together with --delete.")
    if args.purge and not args.with_files:
        parser.error("--purge is only used together with --with-files.")

    if args.delete:
        # La docroot va letta PRIMA di rimuovere il sottodominio: dopo, cPanel
        # non lo elenca piu' e il path non e' piu' recuperabile. Se la lettura
        # fallisce si esce qui, senza aver toccato nulla.
        docroot, home = (None, None)
        if args.with_files:
            docroot, home = get_docroot(subdomain, dry_run=args.dry_run)

        # Prima il sottodominio, poi i file: delsubdomain e' lo step lento e
        # piu' incline a fallire, e i file sono la parte non recuperabile.
        # Se fallisce, i file sono ancora li'.
        delete_subdomain(subdomain, dry_run=args.dry_run)
        if args.with_files:
            delete_docroot(
                subdomain, docroot=docroot, home=home,
                purge=args.purge, dry_run=args.dry_run,
            )
        else:
            log("Docroot", f"Folder ~/{subdomain} left on the server (use --with-files to remove it).")
        log("Done", f"Subdomain {subdomain}.{ROOT_DOMAIN} removed (or simulated, with --dry-run).")
        return

    create_subdomain(subdomain, dry_run=args.dry_run)

    # NB: con un record wildcard "*" -> SERVER_IP già presente su Namecheap
    # (creato manualmente una volta), OGNI sottodominio risolve già da solo:
    # lo step DNS non serve più e viene saltato di default. Passa
    # --with-dns-api solo se vuoi comunque creare un record A dedicato
    # (es. il wildcard non c'è, o serve un IP diverso per questo sottodominio).
    if args.with_dns_api:
        add_dns_record(subdomain, dry_run=args.dry_run)
    else:
        log("Namecheap", "Step skipped: covered by the wildcard '*' record (use --with-dns-api to force it).")

    if not args.skip_autossl:
        run_autossl(dry_run=args.dry_run)
    else:
        log("AutoSSL", "Step skipped (--skip-autossl).")

    if not args.skip_https_redirect:
        force_https_redirect(subdomain, dry_run=args.dry_run)
    else:
        log("HTTPS redirect", "Step skipped (--skip-https-redirect).")

    log("Done", f"Subdomain {subdomain}.{ROOT_DOMAIN} ready (or simulated, with --dry-run).")


if __name__ == "__main__":
    main()
