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
            "Variabili d'ambiente mancanti: "
            + ", ".join(missing)
            + "\nVedi .env.example per la lista completa."
        )


# --------------------------------------------------------------------------- #
# 1. cPanel: creazione sottodominio (UAPI SubDomain::addsubdomain)
# --------------------------------------------------------------------------- #

def cpanel_headers():
    return {"Authorization": f"cpanel {CPANEL_USER}:{CPANEL_API_TOKEN}"}


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
    log("cPanel", f"Creazione sottodominio {subdomain}.{ROOT_DOMAIN} -> {params['dir']}")
    if dry_run:
        log("cPanel", f"[dry-run] GET {url} params={params}")
        return True

    resp = requests.get(url, headers=cpanel_headers(), params=params, timeout=30)
    data = resp.json()
    if not data.get("status"):
        errors = data.get("errors") or [data.get("error")]
        raise RuntimeError(f"Creazione sottodominio fallita: {errors}")
    log("cPanel", "Sottodominio creato con successo.")
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
    url = f"https://{CPANEL_HOST}:2083/json-api/cpanel"
    params = {
        "cpanel_jsonapi_user": CPANEL_USER,
        "cpanel_jsonapi_apiversion": 2,
        "cpanel_jsonapi_module": "SubDomain",
        "cpanel_jsonapi_func": "delsubdomain",
        "domain": f"{subdomain}.{ROOT_DOMAIN}",
    }
    log("cPanel", f"Rimozione sottodominio {subdomain}.{ROOT_DOMAIN} (via API2, legacy)")
    if dry_run:
        log("cPanel", f"[dry-run] GET {url} params={params}")
        return True

    try:
        # Su questo server l'endpoint API2 risponde lentamente: timeout più
        # ampio. NB: l'azione può comunque essere eseguita lato server anche
        # se la risposta non arriva in tempo - in caso di timeout, controlla
        # sempre in cPanel -> Domains prima di riprovare.
        resp = requests.get(url, headers=cpanel_headers(), params=params, timeout=90)
    except requests.exceptions.Timeout:
        raise RuntimeError(
            "Timeout in attesa della risposta (l'endpoint API2 di questo server è lento). "
            "L'operazione potrebbe comunque essere andata a buon fine: controlla in "
            "cPanel -> Domains prima di riprovare, per evitare richieste duplicate."
        )
    data = resp.json()
    result = (data.get("cpanelresult", {}).get("data") or [{}])[0]
    if result.get("result") not in (1, "1"):
        raise RuntimeError(
            f"Rimozione sottodominio fallita: {result.get('reason') or data}. "
            f"In alternativa rimuovilo manualmente da cPanel -> Domains."
        )
    log("cPanel", "Sottodominio rimosso. (I file nella document root NON vengono cancellati automaticamente.)")
    return True


def get_home_dir():
    """Ricava il path assoluto della home dall'API, invece di assumere
    /home/<utente> (che su alcuni server non e' detto sia corretto)."""
    url = f"https://{CPANEL_HOST}:2083/execute/Fileman/list_files"
    resp = requests.get(
        url, headers=cpanel_headers(), params={"dir": ".", "types": "dir"}, timeout=30
    )
    data = resp.json()
    rows = data.get("data") or []
    if not data.get("status") or not rows:
        raise RuntimeError(f"Impossibile determinare la home dell'account: {data.get('errors')}")
    home = rows[0].get("path")
    if not home:
        raise RuntimeError("La risposta di list_files non contiene il path della home.")
    return home


def delete_docroot(subdomain, purge=False, dry_run=False):
    """Cancella la cartella document root del sottodominio (~/<subdomain>).

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

    # Rete di sicurezza: qui si cancellano file veri. Un nome malformato non
    # deve poter puntare alla home, a public_html o fuori dalla home.
    if not subdomain or "/" in subdomain or subdomain in (".", "..", "public_html"):
        raise RuntimeError(
            f"Nome sottodominio non valido per la cancellazione della cartella: {subdomain!r}"
        )

    op = "unlink" if purge else "trash"
    modo = "cancellazione DEFINITIVA" if purge else "spostamento nel cestino (~/.trash)"

    if dry_run:
        log("Docroot", f"[dry-run] {modo} di ~/{subdomain} (API2 Fileman::fileop op={op})")
        return True

    home = get_home_dir()
    target = f"{home}/{subdomain}"
    log("Docroot", f"{modo} di {target}")

    url = f"https://{CPANEL_HOST}:2083/json-api/cpanel"
    params = {
        "cpanel_jsonapi_user": CPANEL_USER,
        "cpanel_jsonapi_apiversion": 2,
        "cpanel_jsonapi_module": "Fileman",
        "cpanel_jsonapi_func": "fileop",
        "op": op,
        "sourcefiles": target,
        "doubledecode": 0,
    }
    resp = requests.get(url, headers=cpanel_headers(), params=params, timeout=90)
    data = resp.json()
    result = (data.get("cpanelresult", {}).get("data") or [{}])[0]
    if result.get("result") not in (1, "1"):
        raise RuntimeError(
            f"Cancellazione cartella fallita: {result.get('reason') or data}. "
            f"In alternativa rimuovila manualmente da cPanel -> File Manager."
        )
    if purge:
        log("Docroot", "Cartella cancellata definitivamente.")
    else:
        log("Docroot", "Cartella spostata nel cestino (recuperabile da cPanel -> File Manager -> Trash).")
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
            f"[dry-run] Leggerei/scriverei i record DNS di {ROOT_DOMAIN} "
            f"per aggiungere {subdomain} -> {SERVER_IP} (nessuna chiamata eseguita).",
        )
        return True
    # NB: Namecheap non ha un "addHost": setHosts SOSTITUISCE l'intera lista
    # di record del dominio. Quindi leggiamo prima quelli esistenti (getHosts)
    # e li reinviamo tutti insieme al nuovo record.
    log("Namecheap", f"Lettura record DNS esistenti per {ROOT_DOMAIN}")
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
        raise RuntimeError(f"getHosts fallita: {errs}")

    hosts = []
    for h in root.findall(".//nc:host", ns):
        hosts.append(
            {
                "HostName": h.get("Name"),
                "RecordType": h.get("Type"),
                "Address": h.get("Address"),
                "TTL": h.get("TTL"),
                "MXPref": h.get("MXPref", "10"),
            }
        )

    # Rimuove un eventuale record preesistente con lo stesso nome, poi aggiunge quello nuovo
    hosts = [h for h in hosts if h["HostName"] != subdomain]
    hosts.append(
        {
            "HostName": subdomain,
            "RecordType": "A",
            "Address": SERVER_IP,
            "TTL": "60",  # "Automatic" nell'interfaccia corrisponde al TTL minimo
            "MXPref": "10",
        }
    )

    extra = {"SLD": sld, "TLD": tld}
    for i, h in enumerate(hosts, start=1):
        extra[f"HostName{i}"] = h["HostName"]
        extra[f"RecordType{i}"] = h["RecordType"]
        extra[f"Address{i}"] = h["Address"]
        extra[f"TTL{i}"] = h["TTL"]
        if h["RecordType"] == "MX":
            extra[f"MXPref{i}"] = h["MXPref"]

    log("Namecheap", f"Scrittura {len(hosts)} record (incluso il nuovo A per '{subdomain}')")
    if dry_run:
        log("Namecheap", f"[dry-run] setHosts params={extra}")
        return True

    set_resp = requests.get(
        NAMECHEAP_API_BASE,
        params=namecheap_params("namecheap.domains.dns.setHosts", extra),
        timeout=30,
    )
    set_resp.raise_for_status()
    root = ET.fromstring(set_resp.text)
    if root.get("Status") != "OK":
        errs = [e.text for e in root.findall(".//nc:Errors/nc:Error", ns)]
        raise RuntimeError(f"setHosts fallita: {errs}")
    ok = root.find(".//nc:DomainDNSSetHostsResult", ns)
    if ok is None or ok.get("IsSuccess") != "true":
        raise RuntimeError("setHosts non ha confermato il successo.")
    log("Namecheap", f"Record A {subdomain}.{ROOT_DOMAIN} -> {SERVER_IP} creato.")
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
            "WHM_API_TOKEN non impostato: salto questo step (serve un token WHM/root, "
            "diverso dal token utente cPanel - vedi .env.example).",
        )
        return False
    url = f"https://{WHM_HOST}:2087/json-api/start_autossl_check"
    params = {"user": CPANEL_USER, "api.version": 1}
    log("AutoSSL", f"Avvio AutoSSL per l'utente {CPANEL_USER}")
    if dry_run:
        log("AutoSSL", f"[dry-run] GET {url} params={params}")
        return True

    resp = requests.get(url, headers=whm_headers(), params=params, timeout=30)
    data = resp.json()
    if data.get("metadata", {}).get("result") != 1:
        raise RuntimeError(f"start_autossl_check fallita: {data.get('metadata')}")
    log("AutoSSL", "Check AutoSSL avviato. L'emissione del certificato è asincrona: "
                    "verifica lo stato in WHM > SSL/TLS Status dopo qualche minuto.")
    if wait_seconds:
        log("AutoSSL", f"Attendo {wait_seconds}s prima di continuare...")
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
# --- BEGIN force-https (aggiunto automaticamente) ---
RewriteEngine On
RewriteCond %{HTTPS} off
RewriteRule ^(.*)$ https://%{HTTP_HOST}%{REQUEST_URI} [L,R=301]
# --- END force-https ---
"""


def force_https_redirect(subdomain, dry_run=False):
    require(
        {"CPANEL_HOST": CPANEL_HOST, "CPANEL_USER": CPANEL_USER, "CPANEL_API_TOKEN": CPANEL_API_TOKEN},
        ["CPANEL_HOST", "CPANEL_USER", "CPANEL_API_TOKEN"],
    )
    doc_root = subdomain
    log("HTTPS redirect", f"Scrittura regole di redirect in {doc_root}/.htaccess")
    if dry_run:
        log("HTTPS redirect", f"[dry-run] Aggiungerei a {doc_root}/.htaccess:\n{HTACCESS_SNIPPET}")
        return True

    read_url = f"https://{CPANEL_HOST}:2083/execute/Fileman/get_file_content"
    resp = requests.get(
        read_url,
        headers=cpanel_headers(),
        params={"dir": doc_root, "file": ".htaccess"},
        timeout=30,
    )
    existing = ""
    if resp.ok:
        data = resp.json()
        if data.get("status"):
            existing = data["data"].get("content", "")

    if "force-https" in existing:
        log("HTTPS redirect", ".htaccess contiene già il blocco force-https, nessuna modifica.")
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
        raise RuntimeError(f"Scrittura .htaccess fallita: {data.get('errors')}")
    log("HTTPS redirect", "Redirect HTTPS forzato attivato via .htaccess.")
    return True


# --------------------------------------------------------------------------- #
# Orchestrazione
# --------------------------------------------------------------------------- #

def main():
    parser = argparse.ArgumentParser(description="Automatizza la creazione di un sottodominio")
    parser.add_argument("subdomain", help="Nome del sottodominio, es. 'lab'")
    parser.add_argument("--dry-run", action="store_true", help="Mostra le chiamate senza eseguirle")
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Rimuove il sottodominio da cPanel invece di crearlo (i file nella "
             "document root non vengono cancellati, vedi --with-files).",
    )
    parser.add_argument(
        "--with-files",
        action="store_true",
        help="Solo con --delete: cancella anche la cartella document root "
             "(~/<subdomain>), spostandola nel cestino ~/.trash.",
    )
    parser.add_argument(
        "--purge",
        action="store_true",
        help="Solo con --with-files: cancella la cartella in modo DEFINITIVO "
             "invece di spostarla nel cestino.",
    )
    parser.add_argument(
        "--with-dns-api",
        action="store_true",
        help="Chiama comunque l'API Namecheap per creare un record dedicato "
             "(non necessario se hai un wildcard '*' -> SERVER_IP, vedi sotto).",
    )
    parser.add_argument("--skip-autossl", action="store_true")
    parser.add_argument("--skip-https-redirect", action="store_true")
    args = parser.parse_args()

    subdomain = args.subdomain.strip().lower()

    # I flag distruttivi hanno senso solo in combinazione con --delete:
    # meglio fallire subito che ignorarli in silenzio.
    if args.with_files and not args.delete:
        parser.error("--with-files si usa solo insieme a --delete.")
    if args.purge and not args.with_files:
        parser.error("--purge si usa solo insieme a --with-files.")

    if args.delete:
        # Prima il sottodominio, poi i file: delsubdomain e' lo step lento e
        # piu' incline a fallire, e i file sono la parte non recuperabile.
        # Se fallisce, i file sono ancora li'.
        delete_subdomain(subdomain, dry_run=args.dry_run)
        if args.with_files:
            delete_docroot(subdomain, purge=args.purge, dry_run=args.dry_run)
        else:
            log("Docroot", f"Cartella ~/{subdomain} lasciata sul server (usa --with-files per rimuoverla).")
        log("Fine", f"Sottodominio {subdomain}.{ROOT_DOMAIN} rimosso (o simulato con --dry-run).")
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
        log("Namecheap", "Step saltato: coperto dal record wildcard '*' (usa --with-dns-api per forzarlo).")

    if not args.skip_autossl:
        run_autossl(dry_run=args.dry_run)
    else:
        log("AutoSSL", "Step saltato (--skip-autossl).")

    if not args.skip_https_redirect:
        force_https_redirect(subdomain, dry_run=args.dry_run)
    else:
        log("HTTPS redirect", "Step saltato (--skip-https-redirect).")

    log("Fine", f"Sottodominio {subdomain}.{ROOT_DOMAIN} pronto (o simulato con --dry-run).")


if __name__ == "__main__":
    main()
