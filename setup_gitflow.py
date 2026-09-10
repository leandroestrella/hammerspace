#!/usr/bin/env python3
"""
setup_gitflow.py

Prepara un repository GitHub per il Gitflow
(https://www.atlassian.com/git/tutorials/comparing-workflows/gitflow-workflow):
due branch di lunga vita invece di uno solo - quello di produzione (master),
che riceve solo le release, e develop, dove si integrano le feature.

  1. se il repository e' vuoto (nessun commit) crea il commit iniziale, con un
     README.md che descrive i branch, sul branch di produzione
  2. crea develop a partire dal branch di produzione, se non c'e' gia'
  3. facoltativo (--default-develop): rende develop il branch di default, cosi'
     clone e pull request partono da li'

Uso:
    python3 setup_gitflow.py --repo utente/lab
    python3 setup_gitflow.py --repo utente/lab --dry-run
    python3 setup_gitflow.py --repo utente/lab --branch main
    python3 setup_gitflow.py --repo utente/lab --default-develop

E' idempotente: su un repository gia' configurato non fa niente, e non
riscrive mai un branch che esiste gia'. setup_autodeploy.py lo richiama da
solo quando il repository di destinazione e' vuoto.

NB: i prefissi feature/ release/ hotfix/ del comando `git flow` sono
configurazione del clone locale (.git/config), non del repository su GitHub:
nessuna API li imposta. Dopo il clone basta `git flow init -d`, che trova i
due branch gia' al loro posto.

Le credenziali si passano tramite variabili d'ambiente (vedi .env.example
nella stessa cartella). Non salvare mai le API key nel codice o in git.
"""

import argparse
import base64
import os
import re
import sys
import time

import requests

# --------------------------------------------------------------------------- #
# Configurazione da variabili d'ambiente
# --------------------------------------------------------------------------- #

# Stesso token di setup_autodeploy.py, e per lo stesso motivo non si chiama
# GITHUB_TOKEN: dentro Actions quel nome e' il token di hammerspace, che sul
# repository di destinazione non puo' fare niente.
GITHUB_PAT = os.environ.get("GITHUB_PAT")

GITHUB_API = os.environ.get("GITHUB_API", "https://api.github.com")

GITFLOW_URL = "https://www.atlassian.com/git/tutorials/comparing-workflows/gitflow-workflow"
DEVELOP = "develop"


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
# Repository e branch finiscono dentro path dell'API (branches/<nome>,
# git/refs/heads/<nome>) e dentro il README generato.
# --------------------------------------------------------------------------- #

# Come in setup_autodeploy.py: owner e nome repo secondo le regole GitHub.
REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")

# Un sottoinsieme prudente di git check-ref-format: le barre sono ammesse
# (release/2026), il resto dei casi limite e' in valida_branch.
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")


def valida_repo(repo):
    """Spezza "owner/nome" nelle due parti, rifiutando tutto il resto."""
    if not REPO_RE.match(repo or ""):
        raise ValueError(
            f"Invalid repository: {repo!r}. Expected format: 'owner/name' "
            "(e.g. 'leandroestrella/hammerspace')."
        )
    owner, _, nome = repo.partition("/")
    return owner, nome


def valida_branch(nome):
    """Rifiuta i nomi che git non accetterebbe, o che cambierebbero l'URL."""
    if (
        not BRANCH_RE.match(nome or "")
        or nome.startswith(("/", "-", "."))
        or nome.endswith(("/", ".", ".lock"))
        or any(pezzo in nome for pezzo in ("..", "//", "/."))
    ):
        raise ValueError(
            f"Invalid branch name: {nome!r}. Letters, digits, '.', '_', '-' and '/' "
            "only, max 100 characters, no '..', and not starting or ending with '/' or '.'."
        )
    return nome


def valida_branches(branch, develop):
    valida_branch(branch)
    valida_branch(develop)
    if branch == develop:
        raise ValueError(
            f"The production branch and the develop branch must differ (both are {branch!r})."
        )


# --------------------------------------------------------------------------- #
# GitHub
# --------------------------------------------------------------------------- #

def github_headers():
    return {
        "Authorization": f"Bearer {GITHUB_PAT}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def spiega_errore_github(resp, cosa):
    """Traduce i codici di GitHub in qualcosa di azionabile.

    Il 404 vale anche per un repository che il token non vede (GitHub non
    rivela i repository privati), e il 409 e' la risposta dell'API Git su un
    repository senza commit.
    """
    dettaglio = ""
    try:
        corpo = resp.json()
        if isinstance(corpo, dict):
            dettaglio = corpo.get("message", "")
    except ValueError:
        dettaglio = resp.text[:200]

    if resp.status_code == 401:
        return (
            f"{cosa} failed: the GitHub token is invalid or expired (401). "
            "Regenerate the PAT and update GITHUB_PAT."
        )
    if resp.status_code == 404:
        return (
            f"{cosa} failed: not found (404). GitHub also answers 404 when the repository "
            "exists but the token cannot see it: check the name, and that the PAT covers "
            "this repository."
        )
    if resp.status_code == 403:
        return (
            f"{cosa} failed: permission denied (403). {dettaglio} Commits and branches "
            "need 'Contents: write' (fine-grained PAT) or 'repo' (classic); changing the "
            "default branch also needs admin rights on the repository."
        )
    if resp.status_code == 409:
        return f"{cosa} failed: conflict (409). {dettaglio}"
    if resp.status_code == 422:
        return f"{cosa} failed: rejected by GitHub (422). {dettaglio}"
    return f"{cosa} failed: HTTP {resp.status_code}. {resp.text[:300]}"


def github(metodo, url, cosa, ok=(), json=None):
    """Una chiamata all'API GitHub.

    Gli status elencati in `ok` tornano come risposta invece di diventare un
    errore: e' cosi' che un 404 diventa "il branch non c'e'".
    """
    resp = requests.request(metodo, url, headers=github_headers(), json=json, timeout=30)
    if resp.ok or resp.status_code in ok:
        return resp
    raise RuntimeError(spiega_errore_github(resp, cosa))


def leggi_repo(owner, repo):
    return github("GET", f"{GITHUB_API}/repos/{owner}/{repo}", "Reading the repository").json()


def repo_vuoto(owner, repo):
    """True se il repository non ha ancora nessun commit.

    Verificato su un repository appena creato: /branches risponde 200 con una
    lista vuota, mentre /commits, /git/refs e /git/trees rispondono 409 "Git
    Repository is empty.". Valgono entrambi i segnali, per non dipendere da
    quale dei due GitHub usera' domani.
    """
    resp = github(
        "GET", f"{GITHUB_API}/repos/{owner}/{repo}/branches?per_page=1",
        "Listing the branches", ok=(409,),
    )
    return resp.status_code == 409 or resp.json() == []


def sha_branch(owner, repo, branch):
    """SHA dell'ultimo commit del branch, o None se il branch non esiste."""
    resp = github(
        "GET", f"{GITHUB_API}/repos/{owner}/{repo}/branches/{branch}",
        f"Reading branch {branch}", ok=(404,),
    )
    if resp.status_code == 404:
        return None
    return resp.json()["commit"]["sha"]


# --------------------------------------------------------------------------- #
# Il commit iniziale
# --------------------------------------------------------------------------- #

def costruisci_readme(nome, branch, develop):
    """README del commit iniziale. Funzione pura: testata senza rete.

    Descrive i branch invece di essere un titolo e basta: chi clona il
    repository deve sapere da dove partire senza aprire questo script.
    """
    return (
        f"# {nome}\n"
        "\n"
        f"Branches follow [Gitflow]({GITFLOW_URL}):\n"
        "\n"
        f"- `{branch}` holds released code. It receives merges from `release/*` and\n"
        "  `hotfix/*` branches, each one tagged with a version number.\n"
        f"- `{develop}` is where work comes together. Start `feature/*` branches from it\n"
        "  and merge them back into it.\n"
        "\n"
        "After cloning, `git flow init -d` picks both branches up.\n"
    )


def commit_iniziale(owner, repo, branch, develop, default_attuale):
    """Crea il primo commit del repository (il README) e restituisce il suo SHA.

    Su un repository vuoto l'API Git (refs, tree, commit) risponde 409: l'unica
    porta d'ingresso e' l'API Contents, che crea commit e branch in un colpo.
    Il parametro `branch` e' documentato solo come "default: il branch di
    default", quindi lo si passa solo quando serve davvero un nome diverso.
    """
    payload = {
        "message": "Initial commit",
        "content": base64.b64encode(
            costruisci_readme(repo, branch, develop).encode("utf-8")
        ).decode("ascii"),
    }
    if branch != default_attuale:
        payload["branch"] = branch
    resp = github(
        "PUT", f"{GITHUB_API}/repos/{owner}/{repo}/contents/README.md",
        "Creating the initial commit", json=payload,
    )
    return resp.json()["commit"]["sha"]


def crea_branch(owner, repo, nome, sha, tentativi=1):
    """Crea refs/heads/<nome> sul commit indicato.

    GitHub documenta il 409 anche per un repository "non ancora disponibile":
    subito dopo il commit iniziale si concede qualche secondo, invece di
    fallire su un repository che un attimo dopo risponderebbe.
    """
    for tentativo in range(1, tentativi + 1):
        resp = github(
            "POST", f"{GITHUB_API}/repos/{owner}/{repo}/git/refs",
            f"Creating branch {nome}", ok=(409,),
            json={"ref": f"refs/heads/{nome}", "sha": sha},
        )
        if resp.status_code != 409:
            return
        if tentativo < tentativi:
            time.sleep(2)
    raise RuntimeError(spiega_errore_github(resp, f"Creating branch {nome}"))


def imposta_default(owner, repo, nome):
    github(
        "PATCH", f"{GITHUB_API}/repos/{owner}/{repo}",
        "Changing the default branch", json={"default_branch": nome},
    )


def allinea_develop(owner, repo, branch, develop=DEVELOP):
    """Porta develop in pari con il branch di produzione, solo in fast-forward.

    Serve a setup_autodeploy.py: develop nasce dal commit iniziale, e il
    workflow di deploy arriva sul branch di produzione un commit dopo. Con
    force=False GitHub rifiuta (422) tutto cio' che non e' un fast-forward,
    quindi un develop con del lavoro sopra non viene mai riscritto.
    """
    sha = sha_branch(owner, repo, branch)
    if sha is None:
        raise RuntimeError(f"Branch {branch!r} not found.")
    github(
        "PATCH", f"{GITHUB_API}/repos/{owner}/{repo}/git/refs/heads/{develop}",
        f"Fast-forwarding {develop}", json={"sha": sha, "force": False},
    )
    log("Gitflow", f"{develop} fast-forwarded to {branch}.")


# --------------------------------------------------------------------------- #
# Orchestrazione
# --------------------------------------------------------------------------- #

def pianifica(vuoto, branch_esiste, develop_esiste, default_attuale,
              branch, develop, default_develop=False):
    """Decide cosa fare a partire da cosa c'e' gia'. Funzione pura.

    E' il punto che rende lo script idempotente: ogni azione compare solo se
    manca il suo risultato, quindi un secondo run non fa niente.
    """
    if not vuoto and not branch_esiste:
        raise RuntimeError(
            f"The repository already has commits but no {branch!r} branch to start "
            f"{develop!r} from (the default branch is {default_attuale!r}). "
            "Pass --branch with the production branch."
        )
    azioni = []
    if vuoto:
        azioni.append("commit")
    if vuoto or not develop_esiste:
        azioni.append("develop")
    if default_develop and default_attuale != develop:
        azioni.append("default")
    return azioni


def configura_gitflow(owner, repo, branch="master", develop=DEVELOP,
                      default_develop=False, dry_run=False):
    """Porta il repository al Gitflow. True se ha creato il commit iniziale."""
    valida_branches(branch, develop)
    require({"GITHUB_PAT": GITHUB_PAT}, ["GITHUB_PAT"])
    if dry_run:
        piano = (
            f"[dry-run] Would check {owner}/{repo}: if it is empty, commit a README.md "
            f"to {branch}; then create {develop} from {branch} unless it exists"
        )
        if default_develop:
            piano += f"; then make {develop} the default branch"
        log("Gitflow", piano + ".")
        return False

    default_attuale = leggi_repo(owner, repo).get("default_branch")
    vuoto = repo_vuoto(owner, repo)
    sha = None if vuoto else sha_branch(owner, repo, branch)
    develop_esiste = not vuoto and sha_branch(owner, repo, develop) is not None
    azioni = pianifica(
        vuoto, sha is not None, develop_esiste, default_attuale,
        branch, develop, default_develop,
    )
    if not azioni:
        log("Gitflow", f"{branch} and {develop} are already there, nothing to do.")
        return False

    if "commit" in azioni:
        log("Gitflow", f"{owner}/{repo} has no commits: creating the initial commit (README.md) on {branch}")
        sha = commit_iniziale(owner, repo, branch, develop, default_attuale)
        # Se GitHub avesse ignorato `branch` il commit starebbe sul branch di
        # default, e il resto (develop, il workflow di deploy) cercherebbe un
        # branch che non c'e'.
        if branch != default_attuale and sha_branch(owner, repo, branch) is None:
            log("Gitflow", f"The commit landed on {default_attuale!r}: creating {branch} from it.")
            crea_branch(owner, repo, branch, sha, tentativi=3)
        log("Gitflow", f"Initial commit {sha[:7]} created on {branch}.")

    if "develop" in azioni:
        log("Gitflow", f"Creating {develop} from {branch}")
        crea_branch(owner, repo, develop, sha, tentativi=3 if vuoto else 1)
        log("Gitflow", f"{develop} created.")
    else:
        log("Gitflow", f"{develop} already exists: left untouched.")

    if "default" in azioni:
        log("Gitflow", f"Making {develop} the default branch (was {default_attuale!r})")
        imposta_default(owner, repo, develop)
        log("Gitflow", "Default branch changed.")

    return "commit" in azioni


def main():
    parser = argparse.ArgumentParser(
        description="Prepare a GitHub repository for Gitflow: an initial commit if it "
                    "is empty, and a develop branch next to the production one"
    )
    parser.add_argument("--repo", required=True, help="GitHub repository, as 'owner/name'.")
    parser.add_argument("--dry-run", action="store_true", help="Print every call it would make, and make none")
    parser.add_argument(
        "--branch",
        default="master",
        help="Production branch, the one that only receives releases (default: master). "
             "On an empty repository the initial commit goes here.",
    )
    parser.add_argument(
        "--develop",
        default=DEVELOP,
        help=f"Integration branch to create from the production branch (default: {DEVELOP}).",
    )
    parser.add_argument(
        "--default-develop",
        action="store_true",
        help="Also make the develop branch the repository's default, so clones and pull "
             "requests start from it. Needs admin rights on the repository.",
    )
    args = parser.parse_args()

    try:
        owner, repo = valida_repo(args.repo)
        valida_branches(args.branch, args.develop)
    except ValueError as e:
        parser.error(str(e))

    configura_gitflow(
        owner, repo, branch=args.branch, develop=args.develop,
        default_develop=args.default_develop, dry_run=args.dry_run,
    )
    log("Done", f"Gitflow on {owner}/{repo} is set up (or simulated, with --dry-run).")


if __name__ == "__main__":
    main()
