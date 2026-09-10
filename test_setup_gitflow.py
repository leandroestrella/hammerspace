"""Test di setup_gitflow.py.

Le funzioni pure (validazione, README, piano) si testano da sole. In piu',
diversamente dagli altri file di test, qui l'orchestrazione gira contro un
GitHub finto in memoria: il caso che conta - il repository vuoto - non si
puo' riprovare dal vivo senza creare un repository apposta.

    pip install -r requirements-dev.txt
    pytest -q
"""

import pytest

import setup_gitflow as sg


# --------------------------------------------------------------------------- #
# Validazione degli input
# --------------------------------------------------------------------------- #

def test_repo_spezzato_in_owner_e_nome():
    assert sg.valida_repo("leandroestrella/psicovandali") == ("leandroestrella", "psicovandali")


@pytest.mark.parametrize("repo", ["", None, "solo-nome", "owner/repo/extra", "owner/repo?a=b"])
def test_repo_rifiutati(repo):
    with pytest.raises(ValueError):
        sg.valida_repo(repo)


@pytest.mark.parametrize(
    "nome", ["master", "main", "develop", "release/2026", "feature/x-y_z", "v1.2", "a" * 100]
)
def test_branch_validi(nome):
    assert sg.valida_branch(nome) == nome


@pytest.mark.parametrize(
    "nome",
    [
        "", None,
        "-x",           # git lo scambierebbe per un'opzione
        "/x", "x/",     # cambierebbero il path dell'API
        ".x", "x.",
        "x.lock",       # riservato da git
        "a..b", "a//b", "a/.b",
        "due parole", 'a"b', "a\\b", "a?b", "a~b", "a^b", "a:b",
        "a" * 101,
    ],
)
def test_branch_rifiutati(nome):
    with pytest.raises(ValueError):
        sg.valida_branch(nome)


def test_produzione_e_develop_devono_differire():
    with pytest.raises(ValueError):
        sg.valida_branches("develop", "develop")


# --------------------------------------------------------------------------- #
# README del commit iniziale
# --------------------------------------------------------------------------- #

def test_readme_nomina_i_due_branch():
    testo = sg.costruisci_readme("lab", "main", "dev")
    assert testo.startswith("# lab\n")
    assert "`main`" in testo and "`dev`" in testo
    assert sg.GITFLOW_URL in testo


def test_readme_stabile():
    assert sg.costruisci_readme("lab", "master", "develop") == sg.costruisci_readme("lab", "master", "develop")


# --------------------------------------------------------------------------- #
# Il piano
# --------------------------------------------------------------------------- #

def test_piano_repo_vuoto():
    assert sg.pianifica(True, False, False, "master", "master", "develop") == ["commit", "develop"]


def test_piano_repo_vuoto_con_default_develop():
    assert sg.pianifica(True, False, False, "master", "master", "develop", default_develop=True) == [
        "commit", "develop", "default",
    ]


def test_piano_gia_configurato_non_fa_niente():
    assert sg.pianifica(False, True, True, "master", "master", "develop") == []


def test_piano_crea_solo_develop():
    assert sg.pianifica(False, True, False, "master", "master", "develop") == ["develop"]


def test_piano_default_gia_develop():
    assert sg.pianifica(False, True, True, "develop", "master", "develop", default_develop=True) == []


def test_piano_senza_branch_di_produzione_si_ferma():
    # Repository con commit ma senza il branch indicato: develop non avrebbe
    # da dove partire, e indovinare il branch giusto non spetta allo script.
    with pytest.raises(RuntimeError, match="--branch"):
        sg.pianifica(False, False, False, "main", "master", "develop")


# --------------------------------------------------------------------------- #
# Traduzione degli errori GitHub
# --------------------------------------------------------------------------- #

class RispostaFinta:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.ok = 200 <= status_code < 300

    def json(self):
        if self._payload is None:
            raise ValueError("nessun json")
        return self._payload


def test_409_riporta_il_messaggio_di_github():
    msg = sg.spiega_errore_github(RispostaFinta(409, {"message": "Git Repository is empty."}), "Cosa")
    assert "Git Repository is empty." in msg


def test_403_nomina_permessi_e_admin():
    msg = sg.spiega_errore_github(RispostaFinta(403, {"message": "Must have admin rights"}), "Cosa")
    assert "Contents: write" in msg and "admin" in msg


def test_404_spiega_che_puo_essere_un_permesso():
    assert "cannot see it" in sg.spiega_errore_github(RispostaFinta(404), "Cosa")


def test_errore_con_json_non_oggetto_non_esplode():
    assert "422" in sg.spiega_errore_github(RispostaFinta(422, ["strano"]), "Cosa")


# --------------------------------------------------------------------------- #
# Orchestrazione contro un GitHub finto
# --------------------------------------------------------------------------- #

class GitHubFinto:
    """Quanto basta di GitHub per configura_gitflow, con le stesse risposte
    osservate su un repository vero: /branches vuoto e 409 dall'API Git
    finche' non esiste un commit."""

    def __init__(self, rami=None, default="master"):
        self.rami = dict(rami or {})   # nome -> sha
        self.default = default
        self.chiamate = []
        self.commit = 0

    def scritture(self):
        return [c for c in self.chiamate if c[0] != "GET"]

    def __call__(self, metodo, url, headers=None, json=None, timeout=None):
        path = url.replace(sg.GITHUB_API, "").replace("/repos/o/r", "", 1)
        self.chiamate.append((metodo, path, json))

        if metodo == "GET" and path == "":
            return RispostaFinta(200, {"default_branch": self.default})
        if metodo == "GET" and path == "/branches?per_page=1":
            return RispostaFinta(200, [{"name": n} for n in self.rami][:1])
        if metodo == "GET" and path.startswith("/branches/"):
            nome = path[len("/branches/"):]
            if nome not in self.rami:
                return RispostaFinta(404, {"message": "Branch not found"})
            return RispostaFinta(200, {"commit": {"sha": self.rami[nome]}})
        if metodo == "PUT" and path == "/contents/README.md":
            assert not self.rami, "il commit iniziale solo su un repository vuoto"
            self.commit += 1
            sha = f"c{self.commit}"
            self.rami[json.get("branch", self.default)] = sha
            return RispostaFinta(201, {"commit": {"sha": sha}})
        if metodo == "POST" and path == "/git/refs":
            if not self.rami:
                return RispostaFinta(409, {"message": "Git Repository is empty."})
            nome = json["ref"][len("refs/heads/"):]
            if nome in self.rami:
                return RispostaFinta(422, {"message": "Reference already exists"})
            self.rami[nome] = json["sha"]
            return RispostaFinta(201, {})
        if metodo == "PATCH" and path == "":
            self.default = json["default_branch"]
            return RispostaFinta(200, {})
        if metodo == "PATCH" and path.startswith("/git/refs/heads/"):
            self.rami[path[len("/git/refs/heads/"):]] = json["sha"]
            return RispostaFinta(200, {})
        raise AssertionError(f"chiamata inattesa: {metodo} {path}")


@pytest.fixture
def github_finto(monkeypatch):
    def installa(**kwargs):
        finto = GitHubFinto(**kwargs)
        monkeypatch.setattr(sg.requests, "request", finto)
        monkeypatch.setattr(sg, "GITHUB_PAT", "token-finto")
        return finto
    return installa


def test_repo_vuoto_inizializzato(github_finto):
    gh = github_finto()
    assert sg.configura_gitflow("o", "r") is True
    assert gh.rami == {"master": "c1", "develop": "c1"}


def test_commit_iniziale_sul_default_non_passa_branch(github_finto):
    # "branch" e' documentato solo come "default: il branch di default": sul
    # caso comune non lo si passa, e il repository vuoto non ha sorprese.
    gh = github_finto()
    sg.configura_gitflow("o", "r")
    put = next(c for c in gh.chiamate if c[0] == "PUT")
    assert "branch" not in put[2]


def test_commit_iniziale_su_un_branch_diverso_dal_default(github_finto):
    gh = github_finto(default="master")
    sg.configura_gitflow("o", "r", branch="main")
    assert gh.rami == {"main": "c1", "develop": "c1"}


def test_secondo_run_non_scrive_niente(github_finto):
    gh = github_finto()
    sg.configura_gitflow("o", "r")
    gh.chiamate.clear()
    assert sg.configura_gitflow("o", "r") is False
    assert gh.scritture() == []


def test_repo_esistente_riceve_solo_develop(github_finto):
    gh = github_finto(rami={"master": "abc"})
    assert sg.configura_gitflow("o", "r") is False
    assert gh.rami == {"master": "abc", "develop": "abc"}
    assert not any(c[0] == "PUT" for c in gh.chiamate)


def test_develop_esistente_non_viene_toccato(github_finto):
    gh = github_finto(rami={"master": "abc", "develop": "lavoro"})
    sg.configura_gitflow("o", "r")
    assert gh.rami["develop"] == "lavoro"
    assert gh.scritture() == []


def test_default_develop(github_finto):
    gh = github_finto()
    sg.configura_gitflow("o", "r", default_develop=True)
    assert gh.default == "develop"


def test_allinea_develop_in_fast_forward(github_finto):
    gh = github_finto(rami={"master": "c2", "develop": "c1"})
    sg.allinea_develop("o", "r", "master")
    assert gh.rami["develop"] == "c2"
    patch = gh.scritture()[-1]
    assert patch[2] == {"sha": "c2", "force": False}


def test_dry_run_non_chiama_la_rete(github_finto, capsys):
    gh = github_finto()
    assert sg.configura_gitflow("o", "r", dry_run=True) is False
    assert gh.chiamate == []
    assert "[dry-run]" in capsys.readouterr().out
