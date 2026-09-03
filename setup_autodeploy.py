#!/usr/bin/env python3
"""
setup_autodeploy.py

Automatizza la procedura descritta nella pagina Confluence "Auto Deploy from
Git": collegare un repository GitHub a una cartella su cPanel, in modo che ogni
push sul branch scelto pubblichi il sito via FTP.

  1. cPanel -> Files -> FTP Accounts: crea l'account FTP con home nella
     document root del progetto (UAPI Ftp::add_ftp, quota illimitata)
  2. GitHub -> Settings -> Secrets and variables -> Actions: crea i tre
     secret FTP_SERVER, FTP_USERNAME, FTP_PASSWORD sul repository DI DESTINAZIONE
  3. GitHub -> il repository di destinazione: committa il workflow di deploy
     in .github/workflows/deploy-to-cpanel.yml

Uso:
    python3 setup_autodeploy.py lab --repo utente/lab
    python3 setup_autodeploy.py lab --repo utente/lab --dry-run
    python3 setup_autodeploy.py lab --repo utente/lab --branch web
    python3 setup_autodeploy.py lab --repo utente/lab --show-password

Rimozione:
    python3 setup_autodeploy.py lab --repo utente/lab --delete

NB: lo step 3 e' l'ULTIMO di proposito. Il workflow si attiva sul push del
branch scelto, e il commit che lo aggiunge E' un push su quel branch: appena il
file arriva parte un deploy vero. Per questo l'account FTP e i secret devono
gia' esistere quando succede.

Le credenziali si passano tramite variabili d'ambiente (vedi .env.example
nella stessa cartella). Non salvare mai le API key nel codice o in git.
"""

import argparse
import base64
import os
import re
import secrets
import string
import sys

import requests

# --------------------------------------------------------------------------- #
# Configurazione da variabili d'ambiente
# --------------------------------------------------------------------------- #

CPANEL_HOST = os.environ.get("CPANEL_HOST")            # es. server.leandroestrella.com
CPANEL_USER = os.environ.get("CPANEL_USER")             # utente cPanel
CPANEL_API_TOKEN = os.environ.get("CPANEL_API_TOKEN")   # Security -> Manage API Tokens
ROOT_DOMAIN = os.environ.get("ROOT_DOMAIN")   # es. tuodominio.com
SERVER_IP = os.environ.get("SERVER_IP")        # finisce nel secret FTP_SERVER

# Token GitHub con cui scrivere sul repository DI DESTINAZIONE.
#
# NB: si chiama GITHUB_PAT e non GITHUB_TOKEN di proposito. Dentro Actions
# GITHUB_TOKEN esiste sempre ed e' il token del repository che sta girando
# (hammerspace), che non ha alcun permesso sul repository di destinazione:
# riusare quel nome significherebbe raccogliere in silenzio il token sbagliato
# e fallire con un 404 incomprensibile.
GITHUB_PAT = os.environ.get("GITHUB_PAT")

GITHUB_API = os.environ.get("GITHUB_API", "https://api.github.com")

# Nome del workflow scritto sul repository di destinazione. Il documento
# Confluence lo chiama "deployTocPanel.yml"; qui si usa il kebab-case come
# tutti gli altri workflow, ma il nome vecchio viene comunque cercato prima
# di scrivere (vedi controlla_workflow_legacy).
WORKFLOW_PATH = ".github/workflows/deploy-to-cpanel.yml"
WORKFLOW_PATH_LEGACY = ".github/workflows/deployTocPanel.yml"

SECRET_NAMES = ("FTP_SERVER", "FTP_USERNAME", "FTP_PASSWORD")


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
# Validazione degli input
#
# Gli stessi valori finiscono in un path FTP, in una URL dell'API GitHub e nel
# nome di un account sul server: vanno respinti PRIMA, non gestiti dopo.
# --------------------------------------------------------------------------- #

# Come per create_subdomain.py: label DNS valida (RFC 1123).
PROGETTO_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

# Login FTP cPanel: lettere, cifre, punto, trattino e underscore. Niente "@"
# (lo aggiunge cPanel col dominio) e niente separatori di path.
FTP_USER_RE = re.compile(r"^[a-z0-9]([a-z0-9._-]{0,30}[a-z0-9])?$")

# owner e nome repo secondo le regole GitHub.
REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")


def valida_progetto(nome):
    """Rifiuta i nomi di progetto non validi prima di usarli in path e URL."""
    if not PROGETTO_RE.match(nome or ""):
        raise ValueError(
            f"Invalid project name: {nome!r}. "
            "Only lowercase letters, digits and hyphens (not leading or trailing), max 63 characters."
        )
    return nome


def valida_ftp_user(user):
    """Rifiuta i login FTP non validi.

    Il login diventa la parte prima della "@" di un account reale sul server:
    un valore con "/" o ".." dentro non deve nemmeno arrivare all'API.
    """
    if not FTP_USER_RE.match(user or ""):
        raise ValueError(
            f"Invalid FTP login: {user!r}. "
            "Only lowercase letters, digits, dot, hyphen and underscore "
            "(not leading or trailing), max 32 characters."
        )
    return user


def valida_repo(repo):
    """Spezza "owner/nome" nelle due parti, rifiutando tutto il resto.

    Le due parti finiscono dentro l'URL dell'API GitHub: una barra in piu' e
    la chiamata punta a un endpoint diverso da quello previsto.
    """
    if not REPO_RE.match(repo or ""):
        raise ValueError(
            f"Invalid repository: {repo!r}. Expected format: 'owner/name' "
            "(e.g. 'leandroestrella/hammerspace')."
        )
    owner, _, nome = repo.partition("/")
    return owner, nome


# Niente caratteri ambigui (0/O, 1/l/I) ne' simboli che alcune shell o client
# FTP interpretano: la password puo' finire incollata a mano in WinSCP.
ALFABETO_PASSWORD = (
    "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lI")
    + "!#%*+-=?_"
)


def genera_password(lunghezza=24):
    """Password casuale per l'account FTP.

    Generata con `secrets`, non `random`: e' una credenziale vera, che finisce
    in un secret di GitHub e nell'account FTP del server.
    """
    if lunghezza < 12:
        raise ValueError("Password length too low: 12 characters minimum.")
    return "".join(secrets.choice(ALFABETO_PASSWORD) for _ in range(lunghezza))


# --------------------------------------------------------------------------- #
# 1. cPanel: account FTP (UAPI Ftp::add_ftp / Ftp::delete_ftp)
#
# NB: a differenza di SubDomain e Fileman (vedi docs/cpanel-api.md), qui la
# UAPI espone ANCHE la cancellazione: Ftp::delete_ftp esiste e funziona, non
# serve passare dalla vecchia API2.
# --------------------------------------------------------------------------- #

def cpanel_headers():
    return {"Authorization": f"cpanel {CPANEL_USER}:{CPANEL_API_TOKEN}"}


def parse_uapi_result(data, cosa):
    """Estrae l'esito da una risposta UAPI, alzando un errore parlante."""
    if not data.get("status"):
        errori = data.get("errors") or ([data["error"]] if data.get("error") else ["no detail given"])
        raise RuntimeError(f"{cosa} failed: {errori}")
    return data.get("data")


def ftp_login_completo(user, dominio):
    """Login FTP come lo vuole il client: utente@dominio."""
    return f"{user}@{dominio}"


def crea_account_ftp(user, dominio, password, homedir, dry_run=False):
    """Crea l'account FTP con home nella document root del progetto.

    quota=0 significa illimitata (il documento Confluence chiede "Unlimited").
    homedir e' relativa alla home dell'account cPanel, come il `dir` che
    create_subdomain.py passa a addsubdomain: le due cose devono coincidere,
    altrimenti il deploy scrive in una cartella che il sottodominio non serve.
    """
    require(
        {
            "CPANEL_HOST": CPANEL_HOST,
            "CPANEL_USER": CPANEL_USER,
            "CPANEL_API_TOKEN": CPANEL_API_TOKEN,
        },
        ["CPANEL_HOST", "CPANEL_USER", "CPANEL_API_TOKEN"],
    )
    url = f"https://{CPANEL_HOST}:2083/execute/Ftp/add_ftp"
    params = {
        "user": user,
        "domain": dominio,
        "pass": password,
        "homedir": homedir,
        "quota": 0,  # illimitata
    }
    log("cPanel", f"Creating FTP account {ftp_login_completo(user, dominio)} -> ~/{homedir}")
    if dry_run:
        # La password non viene mai stampata, nemmeno in dry-run.
        censurati = dict(params, **{"pass": "***"})
        log("cPanel", f"[dry-run] POST {url} params={censurati}")
        return True

    resp = requests.post(url, headers=cpanel_headers(), data=params, timeout=30)
    parse_uapi_result(resp.json(), "Creating the FTP account")
    log("cPanel", "FTP account created (unlimited quota).")
    return True


def elimina_account_ftp(user, dominio, dry_run=False):
    """Rimuove l'account FTP, LASCIANDO i file dove sono.

    destroy=1 cancellerebbe anche la home dell'account - che qui e' la
    document root del sottodominio, cioe' il sito. La cancellazione dei file
    e' competenza di create_subdomain.py --delete --with-files, che almeno
    legge la document root vera da cPanel prima di toccarla.
    """
    require(
        {
            "CPANEL_HOST": CPANEL_HOST,
            "CPANEL_USER": CPANEL_USER,
            "CPANEL_API_TOKEN": CPANEL_API_TOKEN,
        },
        ["CPANEL_HOST", "CPANEL_USER", "CPANEL_API_TOKEN"],
    )
    url = f"https://{CPANEL_HOST}:2083/execute/Ftp/delete_ftp"
    params = {"user": user, "domain": dominio, "destroy": 0}
    log("cPanel", f"Removing FTP account {ftp_login_completo(user, dominio)}")
    if dry_run:
        log("cPanel", f"[dry-run] POST {url} params={params}")
        return True

    resp = requests.post(url, headers=cpanel_headers(), data=params, timeout=30)
    parse_uapi_result(resp.json(), "Removing the FTP account")
    log("cPanel", "FTP account removed. (Files are NOT deleted: destroy=0.)")
    return True


def account_ftp_esiste(user, dominio, dry_run=False):
    """True se l'account FTP c'e' gia'.

    Serve a fallire con un messaggio comprensibile invece del "user already
    exists" dell'API, e a non ricreare qualcosa che c'e' gia'.
    """
    if dry_run:
        return False
    url = f"https://{CPANEL_HOST}:2083/execute/Ftp/list_ftp"
    resp = requests.get(url, headers=cpanel_headers(), timeout=30)
    righe = parse_uapi_result(resp.json(), "Listing the FTP accounts")
    return cerca_account_ftp(righe, ftp_login_completo(user, dominio))


def cerca_account_ftp(righe, login):
    """Cerca un login tra gli account restituiti da list_ftp.

    list_ftp elenca anche gli account di sistema (ftp anonimo, l'account
    principale, quello di log) e non e' garantito che riporti il login
    completo nello stesso campo: si guardano sia `user` che `serverlogin`.
    """
    for row in righe or []:
        if not isinstance(row, dict):
            continue
        if login in (row.get("serverlogin"), row.get("user")):
            return True
    return False


# --------------------------------------------------------------------------- #
# 2. GitHub: secret del repository di destinazione
#
# I secret non si scrivono in chiaro: GitHub pubblica una chiave pubblica per
# repository e accetta solo valori cifrati con quella (libsodium sealed box).
# --------------------------------------------------------------------------- #

def github_headers():
    return {
        "Authorization": f"Bearer {GITHUB_PAT}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def spiega_errore_github(resp, cosa):
    """Traduce i codici di GitHub in qualcosa di azionabile.

    In particolare il 404: GitHub risponde 404 anche quando il repository
    esiste ma il token non lo puo' vedere, per non rivelare repository
    privati. Detto cosi' e' indistinguibile da un errore di battitura.
    """
    if resp.status_code in (401,):
        return (
            f"{cosa} failed: the GitHub token is invalid or expired (401). "
            "Regenerate the PAT and update GITHUB_PAT."
        )
    if resp.status_code == 404:
        return (
            f"{cosa} failed: repository not found (404). GitHub also answers 404 when "
            "the repository exists but the token cannot see it: check the name, and "
            "that the PAT covers this repository."
        )
    if resp.status_code == 403:
        dettaglio = ""
        try:
            dettaglio = (resp.json() or {}).get("message", "")
        except ValueError:
            dettaglio = resp.text[:200]
        if "workflow" in dettaglio.lower():
            return (
                f"{cosa} failed: the token cannot write files under .github/workflows/ "
                "(403). That needs the 'workflow' scope on a classic PAT, or "
                "'Workflows: write' on a fine-grained one. Otherwise use "
                "--skip-workflow-file and commit the file by hand."
            )
        return f"{cosa} failed: permission denied (403). {dettaglio}"
    return f"{cosa} failed: HTTP {resp.status_code}. {resp.text[:300]}"


def github_get(url, cosa, ok_404=False):
    """GET sull'API GitHub. Con ok_404=True, un 404 diventa None invece di un errore."""
    resp = requests.get(url, headers=github_headers(), timeout=30)
    if resp.status_code == 404 and ok_404:
        return None
    if not resp.ok:
        raise RuntimeError(spiega_errore_github(resp, cosa))
    return resp.json()


def cifra_secret(public_key_b64, valore):
    """Cifra un valore con la chiave pubblica del repository (sealed box).

    Estratta come funzione pura perche' e' il punto in cui un errore non si
    vede: un valore cifrato male viene accettato da GitHub e fallisce solo
    dopo, dentro il workflow di deploy, come credenziale sbagliata.
    """
    # Import locale: pynacl serve solo qui, e cosi' un --dry-run o un
    # --help continuano a funzionare anche senza la dipendenza installata.
    from nacl import encoding, public

    chiave = public.PublicKey(public_key_b64.encode("utf-8"), encoding.Base64Encoder())
    cifrato = public.SealedBox(chiave).encrypt(valore.encode("utf-8"))
    return base64.b64encode(cifrato).decode("utf-8")


def scrivi_secret(owner, repo, nome, valore, public_key, key_id, dry_run=False):
    url = f"{GITHUB_API}/repos/{owner}/{repo}/actions/secrets/{nome}"
    if dry_run:
        log("GitHub", f"[dry-run] PUT {url} (encrypted value, not shown)")
        return True

    payload = {
        "encrypted_value": cifra_secret(public_key, valore),
        "key_id": key_id,
    }
    resp = requests.put(url, headers=github_headers(), json=payload, timeout=30)
    if not resp.ok:
        raise RuntimeError(spiega_errore_github(resp, f"Writing secret {nome}"))
    # 201 = creato, 204 = aggiornato. Entrambi vanno bene.
    log("GitHub", f"Secret {nome} {'created' if resp.status_code == 201 else 'updated'}.")
    return True


def scrivi_secrets(owner, repo, valori, dry_run=False):
    """Scrive i tre secret FTP_* sul repository di destinazione."""
    require({"GITHUB_PAT": GITHUB_PAT}, ["GITHUB_PAT"])
    log("GitHub", f"Writing secrets {', '.join(SECRET_NAMES)} on {owner}/{repo}")
    if dry_run:
        for nome in SECRET_NAMES:
            scrivi_secret(owner, repo, nome, None, None, None, dry_run=True)
        return True

    chiave = github_get(
        f"{GITHUB_API}/repos/{owner}/{repo}/actions/secrets/public-key",
        "Reading the repository public key",
    )
    for nome in SECRET_NAMES:
        scrivi_secret(
            owner, repo, nome, valori[nome],
            chiave["key"], chiave["key_id"], dry_run=False,
        )
    return True


def elimina_secrets(owner, repo, dry_run=False):
    require({"GITHUB_PAT": GITHUB_PAT}, ["GITHUB_PAT"])
    log("GitHub", f"Removing secrets {', '.join(SECRET_NAMES)} from {owner}/{repo}")
    for nome in SECRET_NAMES:
        url = f"{GITHUB_API}/repos/{owner}/{repo}/actions/secrets/{nome}"
        if dry_run:
            log("GitHub", f"[dry-run] DELETE {url}")
            continue
        resp = requests.delete(url, headers=github_headers(), timeout=30)
        if resp.status_code == 404:
            # Gia' assente: non e' un errore, e dirlo evita di far ripartire
            # una rimozione a mano che non serve.
            log("GitHub", f"Secret {nome} is not there, nothing to remove.")
            continue
        if not resp.ok:
            raise RuntimeError(spiega_errore_github(resp, f"Removing secret {nome}"))
        log("GitHub", f"Secret {nome} removed.")
    return True


# --------------------------------------------------------------------------- #
# 3. GitHub: il workflow di deploy sul repository di destinazione
#
# Il documento Confluence riporta actions/checkout@v2.1.0 e
# SamKirkland/FTP-Deploy-Action@3.1.1, entrambe del 2020. Qui si usano le
# versioni correnti, con due differenze che contano:
#
#   - protocol: ftps  -> la v3 parlava FTP in chiaro, quindi la password
#     dell'account passava sulla rete leggibile a ogni deploy. La v4 supporta
#     FTPS esplicito, che e' la stessa cosa che il documento fa configurare in
#     WinSCP ("TLS/SSL Explicit encryption").
#   - niente fetch-depth: 2 -> serviva alla v3, che calcolava le differenze
#     con git diff. La v4 tiene un file di stato sul server
#     (.ftp-deploy-sync-state.json) e non ha bisogno della storia git.
#
# I permessi sono l'opposto di quelli dei workflow di questo repository:
# la' "permissions: {}" va bene, qui il checkout deve poter leggere un
# repository privato. Verificato: con {} il primo deploy fallisce.
# --------------------------------------------------------------------------- #

WORKFLOW_TEMPLATE = """\
# Generated by hammerspace/setup_autodeploy.py
# https://github.com/leandroestrella/hammerspace
#
# Every push to "{branch}" publishes this repository over FTPS to the account
# created in cPanel. The credentials live in this repository's secrets.

name: Deploy to cPanel

on:
  push:
    branches:
      - "{branch}"
  workflow_dispatch:

# The minimum needed: actions/checkout on a PRIVATE repository requires
# contents: read. With "permissions: {{}}" the GITHUB_TOKEN has no permissions
# at all and the checkout fails with a misleading "Repository not found".
permissions:
  contents: read

# Two deploys running at once over the same FTP directory overwrite each
# other and leave the state file inconsistent, so they queue instead.
concurrency:
  group: "deploy-to-cpanel-{branch}"
  cancel-in-progress: false

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Deploy via FTPS
        uses: SamKirkland/FTP-Deploy-Action@v4.3.5
        with:
          server: ${{{{ secrets.FTP_SERVER }}}}
          username: ${{{{ secrets.FTP_USERNAME }}}}
          password: ${{{{ secrets.FTP_PASSWORD }}}}
          protocol: ftps
          server-dir: "{server_dir}"
"""


def costruisci_workflow(branch, server_dir="./"):
    """Genera il workflow di deploy per il branch richiesto.

    Funzione pura: e' il file che finisce committato su un repository altrui,
    e un branch sbagliato qui significa un deploy che non parte mai (o che
    parte da dove non dovrebbe).
    """
    # Il valore finisce dentro una stringa YAML fra virgolette: un apice
    # doppio o un a capo la chiuderebbero, cambiando il file generato.
    if not branch or any(c.isspace() or c in '"\\' for c in branch):
        raise ValueError(
            f"Invalid branch name: {branch!r}. No spaces, quotes or backslashes."
        )
    server_dir = server_dir or "./"
    if any(c.isspace() or c in '"\\' for c in server_dir):
        raise ValueError(f"Invalid server-dir: {server_dir!r}.")
    if not server_dir.endswith("/"):
        # Senza la barra finale l'action tratta il valore come un prefisso di
        # nome file invece che come cartella.
        server_dir = server_dir + "/"
    return WORKFLOW_TEMPLATE.format(branch=branch, server_dir=server_dir)


def leggi_file_repo(owner, repo, path, branch):
    """Legge un file dal repository di destinazione: (sha, contenuto) o None."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}?ref={branch}"
    data = github_get(url, f"Reading {path}", ok_404=True)
    if data is None:
        return None
    contenuto = base64.b64decode(data.get("content", "")).decode("utf-8", "replace")
    return data.get("sha"), contenuto


def controlla_workflow_legacy(owner, repo, branch, force=False, dry_run=False):
    """Si ferma se sul repository c'e' gia' il workflow col nome del documento.

    Il documento Confluence chiama il file "deployTocPanel.yml": un repository
    configurato a mano prima di questo script ce l'ha con quel nome. Scriverne
    un secondo con nome diverso non sostituisce il primo - li fa girare
    entrambi, cioe' due deploy per ogni push, che si sovrascrivono a vicenda.
    Meglio fermarsi qui, prima che esista qualsiasi cosa.
    """
    if dry_run:
        return False
    esistente = leggi_file_repo(owner, repo, WORKFLOW_PATH_LEGACY, branch)
    if not esistente:
        return False
    messaggio = (
        f"{WORKFLOW_PATH_LEGACY} already exists on {owner}/{repo}: that is the name "
        f"the manual procedure uses. Adding {WORKFLOW_PATH} as well would fire TWO "
        f"deploys on every push, overwriting each other. Remove the old file, or use "
        f"--skip-workflow-file so a second one isn't added."
    )
    if not force:
        raise RuntimeError(messaggio)
    log("GitHub", f"WARNING (--force): {messaggio}")
    return True


def scrivi_workflow(owner, repo, branch, contenuto, path=WORKFLOW_PATH,
                    force=False, dry_run=False):
    """Committa il workflow di deploy sul repository di destinazione.

    Come per il .htaccess in create_subdomain.py, la scrittura SOSTITUISCE il
    file: si legge prima. Identico -> niente da fare; diverso -> ci si ferma,
    perche' un workflow gia' li' puo' essere stato modificato a mano.
    """
    require({"GITHUB_PAT": GITHUB_PAT}, ["GITHUB_PAT"])
    log("GitHub", f"Writing {path} on {owner}/{repo} (branch {branch})")
    if dry_run:
        log("GitHub", f"[dry-run] Would commit {path}:\n{contenuto}")
        return True

    esistente = leggi_file_repo(owner, repo, path, branch)
    sha = None
    if esistente:
        sha, vecchio = esistente
        if vecchio == contenuto:
            log("GitHub", f"{path} is already identical, no commit.")
            return True
        if not force:
            raise RuntimeError(
                f"{path} already exists on {owner}/{repo} with different content. "
                "Not overwriting it: it may have been edited by hand. Check the file "
                "and pass --force if you do want to replace it."
            )
        log("GitHub", f"{path} exists and differs: replacing it (--force).")

    payload = {
        "message": (
            "Add cPanel FTP deploy workflow" if sha is None
            else "Update cPanel FTP deploy workflow"
        ),
        "content": base64.b64encode(contenuto.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}"
    resp = requests.put(url, headers=github_headers(), json=payload, timeout=30)
    if not resp.ok:
        raise RuntimeError(spiega_errore_github(resp, f"Writing {path}"))
    log("GitHub", f"{path} committed. That push starts the first deploy right away.")
    return True


def elimina_workflow(owner, repo, branch, path=WORKFLOW_PATH, dry_run=False):
    require({"GITHUB_PAT": GITHUB_PAT}, ["GITHUB_PAT"])
    log("GitHub", f"Removing {path} from {owner}/{repo} (branch {branch})")
    if dry_run:
        log("GitHub", f"[dry-run] DELETE {path}")
        return True

    esistente = leggi_file_repo(owner, repo, path, branch)
    if not esistente:
        log("GitHub", f"{path} is not there, nothing to remove.")
        return False
    sha, _ = esistente

    url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}"
    payload = {"message": "Remove cPanel FTP deploy workflow", "sha": sha, "branch": branch}
    resp = requests.delete(url, headers=github_headers(), json=payload, timeout=30)
    if not resp.ok:
        raise RuntimeError(spiega_errore_github(resp, f"Removing {path}"))
    log("GitHub", f"{path} removed.")
    return True


# --------------------------------------------------------------------------- #
# Preflight
#
# Tutti i controlli che si possono fare senza modificare niente, fatti PRIMA
# di creare l'account FTP: un fallimento a meta' procedura lascia un account
# FTP orfano sul server, che poi va tolto a mano.
# --------------------------------------------------------------------------- #

def preflight(owner, repo, branch, ftp_user, dominio, skip_workflow,
              skip_ftp=False, force=False, dry_run=False):
    if dry_run:
        log("Preflight", "[dry-run] Skipping the preliminary checks (they are network calls).")
        return

    log("Preflight", f"Checking access to {owner}/{repo}")
    require({"GITHUB_PAT": GITHUB_PAT}, ["GITHUB_PAT"])
    info = github_get(f"{GITHUB_API}/repos/{owner}/{repo}", "Reading the repository")

    # Un branch inesistente farebbe fallire la scrittura del workflow DOPO che
    # account FTP e secret esistono gia'.
    rami = github_get(
        f"{GITHUB_API}/repos/{owner}/{repo}/branches/{branch}",
        f"Reading branch {branch}",
        ok_404=True,
    )
    if rami is None:
        raise RuntimeError(
            f"Branch {branch!r} does not exist on {owner}/{repo} "
            f"(the default branch is {info.get('default_branch')!r}). "
            "Pass --branch with the right one."
        )

    if not skip_workflow:
        controlla_workflow_legacy(owner, repo, branch, force=force)

    if not skip_ftp and account_ftp_esiste(ftp_user, dominio):
        raise RuntimeError(
            f"The FTP account {ftp_login_completo(ftp_user, dominio)} already exists. "
            "Use --ftp-user to pick another one, or remove the existing account with "
            "--delete (the password is not recoverable: if you need it, reset it from "
            "cPanel -> FTP Accounts). To update only the workflow of a deploy that is "
            "already set up: --skip-ftp --skip-secrets --force."
        )
    log("Preflight", "Checks passed.")


# --------------------------------------------------------------------------- #
# Orchestrazione
# --------------------------------------------------------------------------- #

def main():
    parser = argparse.ArgumentParser(
        description="Automate push-to-deploy from GitHub to cPanel over FTP"
    )
    parser.add_argument("progetto", help="Project/subdomain name, e.g. 'lab'")
    parser.add_argument(
        "--repo",
        required=True,
        help="Target GitHub repository, as 'owner/name'.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print every call it would make, and make none")
    parser.add_argument(
        "--branch",
        default="master",
        help="Branch whose pushes trigger a deploy (default: master).",
    )
    parser.add_argument(
        "--ftp-user",
        help="FTP login to create (default: the project name). The full login "
             "will be <login>@<project>.<ROOT_DOMAIN>.",
    )
    parser.add_argument(
        "--ftp-password",
        help="FTP account password (default: 24 random characters). Passing it on "
             "the command line leaves it in your shell history.",
    )
    parser.add_argument(
        "--show-password",
        action="store_true",
        help="Print the generated password. Useful locally for setting up WinSCP; "
             "avoid it in Actions, where it would land in the run log.",
    )
    parser.add_argument(
        "--ftp-server",
        help="Value written into the FTP_SERVER secret (default: SERVER_IP).",
    )
    parser.add_argument(
        "--dir",
        dest="homedir",
        help="FTP account home, relative to the cPanel home (default: the project "
             "name, matching the subdomain's document root).",
    )
    parser.add_argument(
        "--server-dir",
        default="./",
        help="Directory the deploy publishes into, relative to the FTP home (default: ./).",
    )
    parser.add_argument(
        "--skip-secrets", action="store_true", help="Leave the repository secrets alone."
    )
    parser.add_argument(
        "--skip-ftp",
        action="store_true",
        help="Leave the FTP account alone, assuming it already exists. This is the "
             "repair path: --skip-ftp --skip-secrets --force rewrites only the "
             "workflow, without tearing everything down and starting over.",
    )
    parser.add_argument(
        "--skip-workflow-file",
        action="store_true",
        help="Don't commit the deploy workflow, just print it. Useful when the "
             "token has no 'workflow' scope.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the deploy workflow if a different one is already there.",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Tear the auto deploy down: removes the workflow, the secrets and the "
             "FTP account. Files on the server are NOT touched.",
    )
    args = parser.parse_args()

    progetto = args.progetto.strip().lower()
    try:
        valida_progetto(progetto)
        owner, repo = valida_repo(args.repo)
        ftp_user = valida_ftp_user((args.ftp_user or progetto).strip().lower())
    except ValueError as e:
        parser.error(str(e))

    if args.force and args.delete:
        parser.error("--force only affects writing the workflow, not --delete.")
    if args.show_password and args.delete:
        parser.error("--show-password makes no sense with --delete.")
    # Senza creare l'account non si conosce la password, e i secret sarebbero
    # scritti con un valore che non apre niente: meglio dirlo subito.
    if args.skip_ftp and not (args.delete or args.skip_secrets or args.ftp_password):
        parser.error(
            "--skip-ftp does not know the existing account's password: add "
            "--skip-secrets, or pass --ftp-password."
        )

    require({"ROOT_DOMAIN": ROOT_DOMAIN}, ["ROOT_DOMAIN"])
    dominio = f"{progetto}.{ROOT_DOMAIN}"
    homedir = args.homedir or progetto

    if args.delete:
        # Senza questo controllo un repository sbagliato non darebbe errore:
        # ogni ricerca risponderebbe 404, cioe' "niente da rimuovere", e il
        # run sembrerebbe riuscito pur non avendo smontato niente.
        if not (args.skip_workflow_file and args.skip_secrets) and not args.dry_run:
            require({"GITHUB_PAT": GITHUB_PAT}, ["GITHUB_PAT"])
            github_get(f"{GITHUB_API}/repos/{owner}/{repo}", "Reading the repository")

        # Ordine inverso rispetto alla creazione: prima si spegne il workflow,
        # poi si tolgono le credenziali che usa. Al contrario, un deploy
        # partito nel frattempo fallirebbe a meta'.
        if not args.skip_workflow_file:
            elimina_workflow(owner, repo, args.branch, dry_run=args.dry_run)
        if not args.skip_secrets:
            elimina_secrets(owner, repo, dry_run=args.dry_run)
        if args.skip_ftp:
            log("cPanel", "Step skipped (--skip-ftp): the FTP account stays where it is.")
        else:
            elimina_account_ftp(ftp_user, dominio, dry_run=args.dry_run)
        log("Done", f"Auto deploy for {owner}/{repo} torn down (or simulated, with --dry-run).")
        return

    preflight(
        owner, repo, args.branch, ftp_user, dominio,
        skip_workflow=args.skip_workflow_file, skip_ftp=args.skip_ftp,
        force=args.force, dry_run=args.dry_run,
    )

    password = args.ftp_password or genera_password()
    ftp_server = args.ftp_server or SERVER_IP
    require({"SERVER_IP (o --ftp-server)": ftp_server}, ["SERVER_IP (o --ftp-server)"])

    if args.skip_ftp:
        log("cPanel", f"Step skipped (--skip-ftp): using the existing account {ftp_login_completo(ftp_user, dominio)}.")
    else:
        crea_account_ftp(ftp_user, dominio, password, homedir, dry_run=args.dry_run)

    if not args.skip_secrets:
        scrivi_secrets(
            owner, repo,
            {
                "FTP_SERVER": ftp_server,
                "FTP_USERNAME": ftp_login_completo(ftp_user, dominio),
                "FTP_PASSWORD": password,
            },
            dry_run=args.dry_run,
        )
    else:
        log("GitHub", "Step skipped (--skip-secrets): the secrets have to be created by hand.")

    workflow = costruisci_workflow(args.branch, args.server_dir)
    if args.skip_workflow_file:
        log("GitHub", f"Step skipped (--skip-workflow-file). Content of {WORKFLOW_PATH}:")
        print(workflow)
    else:
        scrivi_workflow(
            owner, repo, args.branch, workflow,
            force=args.force, dry_run=args.dry_run,
        )

    if args.skip_ftp:
        pass  # nessuna password generata: non c'e' niente da dire
    elif args.show_password and not args.dry_run:
        log("cPanel", f"FTP password for {ftp_login_completo(ftp_user, dominio)}: {password}")
    elif not args.ftp_password and not args.dry_run:
        log(
            "cPanel",
            "The generated password is not printed: it is already in the FTP_PASSWORD "
            "secret. If you need it for WinSCP, re-run with --show-password or reset it "
            "from cPanel -> FTP Accounts.",
        )

    log("Done", f"Auto deploy for {owner}/{repo} is live (or simulated, with --dry-run).")


if __name__ == "__main__":
    main()
