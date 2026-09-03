"""Test delle funzioni pure di setup_autodeploy.py.

Nessuna rete: si testano solo le parti che trasformano dati (validazione,
generazione del workflow, cifratura dei secret, lettura delle risposte).
Sono i punti in cui un errore non si vede subito - un secret cifrato male
viene accettato da GitHub e fallisce solo dopo, dentro il deploy.

    pip install -r requirements-dev.txt
    pytest -q
"""

import base64

import pytest
import yaml
from nacl import encoding, public

import setup_autodeploy as sa


# --------------------------------------------------------------------------- #
# Validazione degli input
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("nome", ["lab", "a", "test-2", "x1-y2-z3", "a" * 63])
def test_progetti_validi(nome):
    assert sa.valida_progetto(nome) == nome


@pytest.mark.parametrize(
    "nome",
    ["", "-lab", "lab-", "la b", "lab/../etc", "lab.sub", 'x") ; id ; ("', "a" * 64, None],
)
def test_progetti_rifiutati(nome):
    with pytest.raises(ValueError):
        sa.valida_progetto(nome)


@pytest.mark.parametrize("user", ["lab", "lab-deploy", "lab_deploy", "lab.deploy", "a" * 32])
def test_login_ftp_validi(user):
    assert sa.valida_ftp_user(user) == user


@pytest.mark.parametrize(
    "user",
    [
        "",
        "lab@dominio.com",   # la "@" la aggiunge cPanel, non l'utente
        "../lab",            # separatore di path
        "lab/deploy",
        "-lab",
        "lab-",
        "a" * 33,            # oltre il limite cPanel
        None,
    ],
)
def test_login_ftp_rifiutati(user):
    with pytest.raises(ValueError):
        sa.valida_ftp_user(user)


def test_repo_spezzato_in_owner_e_nome():
    assert sa.valida_repo("leandroestrella/hammerspace") == ("leandroestrella", "hammerspace")


def test_repo_con_punti_e_trattini():
    assert sa.valida_repo("owner/my.repo-name_2") == ("owner", "my.repo-name_2")


@pytest.mark.parametrize(
    "repo",
    [
        "",
        "hammerspace",                 # manca l'owner
        "owner/repo/extra",            # una barra di troppo: cambia endpoint
        "owner//repo",
        "../../owner/repo",
        "owner/repo?a=b",              # query string dentro il path dell'API
        "owner/repo#frag",
        "own er/repo",
        None,
    ],
)
def test_repo_rifiutati(repo):
    with pytest.raises(ValueError):
        sa.valida_repo(repo)


def test_login_completo():
    assert sa.ftp_login_completo("lab", "lab.esempio.com") == "lab@lab.esempio.com"


# --------------------------------------------------------------------------- #
# Password generata
# --------------------------------------------------------------------------- #

def test_password_lunghezza_di_default():
    assert len(sa.genera_password()) == 24


def test_password_solo_caratteri_ammessi():
    assert set(sa.genera_password(200)) <= set(sa.ALFABETO_PASSWORD)


def test_password_senza_caratteri_ambigui():
    # 0/O e 1/l/I si confondono quando la password viene ricopiata a mano
    # dentro WinSCP.
    assert not (set(sa.genera_password(500)) & set("0O1lI"))


def test_password_non_deterministica():
    assert sa.genera_password() != sa.genera_password()


def test_password_troppo_corta_rifiutata():
    with pytest.raises(ValueError):
        sa.genera_password(8)


# --------------------------------------------------------------------------- #
# Cifratura dei secret (sealed box libsodium)
#
# Un valore cifrato male non fallisce qui: GitHub lo accetta e il deploy
# fallisce dopo, con un errore di credenziali che non punta a questa riga.
# --------------------------------------------------------------------------- #

def test_secret_cifrato_e_ridecifrabile():
    chiave_privata = public.PrivateKey.generate()
    pub_b64 = chiave_privata.public_key.encode(encoding.Base64Encoder).decode()

    cifrato = sa.cifra_secret(pub_b64, "password-segreta")

    aperto = public.SealedBox(chiave_privata).decrypt(base64.b64decode(cifrato))
    assert aperto.decode() == "password-segreta"


def test_secret_cifrato_e_base64_valido():
    chiave_privata = public.PrivateKey.generate()
    pub_b64 = chiave_privata.public_key.encode(encoding.Base64Encoder).decode()
    # GitHub rifiuta il payload se non e' base64: un errore qui sarebbe un 422.
    base64.b64decode(sa.cifra_secret(pub_b64, "x"), validate=True)


def test_secret_cifrato_non_contiene_il_valore_in_chiaro():
    chiave_privata = public.PrivateKey.generate()
    pub_b64 = chiave_privata.public_key.encode(encoding.Base64Encoder).decode()
    cifrato = sa.cifra_secret(pub_b64, "password-segreta")
    assert "password-segreta" not in cifrato


def test_secret_con_caratteri_non_ascii():
    chiave_privata = public.PrivateKey.generate()
    pub_b64 = chiave_privata.public_key.encode(encoding.Base64Encoder).decode()
    valore = "pàsswörd–con–trattini–lunghi"
    cifrato = sa.cifra_secret(pub_b64, valore)
    aperto = public.SealedBox(chiave_privata).decrypt(base64.b64decode(cifrato))
    assert aperto.decode() == valore


# --------------------------------------------------------------------------- #
# Il workflow generato
#
# E' un file che finisce committato su un repository altrui: deve essere YAML
# valido e deve riferirsi al branch giusto.
# --------------------------------------------------------------------------- #

def test_workflow_e_yaml_valido():
    doc = yaml.safe_load(sa.costruisci_workflow("master"))
    assert doc["jobs"]["deploy"]["runs-on"] == "ubuntu-latest"


def test_workflow_si_attiva_sul_branch_richiesto():
    # yaml.safe_load traduce la chiave "on" in True (YAML 1.1: on/off sono
    # booleani), quindi il trigger si legge da li'.
    doc = yaml.safe_load(sa.costruisci_workflow("web"))
    assert doc[True]["push"]["branches"] == ["web"]


def test_workflow_usa_i_tre_secret():
    testo = sa.costruisci_workflow("master")
    for nome in sa.SECRET_NAMES:
        assert f"secrets.{nome}" in testo


def test_workflow_usa_ftps_non_ftp_in_chiaro():
    # La versione del documento Confluence (FTP-Deploy-Action 3.1.1) mandava
    # la password in chiaro a ogni deploy.
    passo = yaml.safe_load(sa.costruisci_workflow("master"))["jobs"]["deploy"]["steps"][1]
    assert passo["with"]["protocol"] == "ftps"


def test_workflow_puo_fare_checkout_di_un_repo_privato():
    # Con "permissions: {}" il checkout di un repository privato fallisce con
    # un fuorviante "Repository not found": e' successo al primo deploy vero.
    assert yaml.safe_load(sa.costruisci_workflow("master"))["permissions"] == {
        "contents": "read"
    }


def test_server_dir_riceve_la_barra_finale():
    # Senza barra l'action tratta il valore come prefisso di nome file.
    doc = yaml.safe_load(sa.costruisci_workflow("master", "public"))
    assert doc["jobs"]["deploy"]["steps"][1]["with"]["server-dir"] == "public/"


def test_server_dir_di_default():
    doc = yaml.safe_load(sa.costruisci_workflow("master"))
    assert doc["jobs"]["deploy"]["steps"][1]["with"]["server-dir"] == "./"


def test_branch_con_barra_accettato():
    doc = yaml.safe_load(sa.costruisci_workflow("release/2026"))
    assert doc[True]["push"]["branches"] == ["release/2026"]


@pytest.mark.parametrize(
    "branch",
    [
        "",
        None,
        "due parole",
        'ma"ster',        # chiuderebbe la stringa YAML
        "master\nfoo:",   # inietterebbe una chiave nel file
        "back\\slash",
    ],
)
def test_branch_non_validi_rifiutati(branch):
    with pytest.raises(ValueError):
        sa.costruisci_workflow(branch)


def test_server_dir_non_valida_rifiutata():
    with pytest.raises(ValueError):
        sa.costruisci_workflow("master", 'pu"blic')


def test_workflow_generato_e_stabile():
    # Il confronto con quello gia' presente sul repository e' testuale: se la
    # generazione non fosse deterministica, ogni run produrrebbe un commit.
    assert sa.costruisci_workflow("master") == sa.costruisci_workflow("master")


# --------------------------------------------------------------------------- #
# Lettura delle risposte cPanel (UAPI)
# --------------------------------------------------------------------------- #

def test_uapi_successo():
    assert sa.parse_uapi_result({"status": 1, "data": {"ok": True}}, "Cosa") == {"ok": True}


@pytest.mark.parametrize(
    "data",
    [
        {"status": 0, "errors": ["The username already exists."]},
        {"status": 0, "error": "boom"},
        {"status": 0},
        {},
    ],
)
def test_uapi_fallimento(data):
    with pytest.raises(RuntimeError):
        sa.parse_uapi_result(data, "Creazione account FTP")


def test_uapi_errore_riporta_il_motivo():
    with pytest.raises(RuntimeError, match="already exists"):
        sa.parse_uapi_result({"status": 0, "errors": ["The username already exists."]}, "Something")


# --------------------------------------------------------------------------- #
# Ricerca dell'account FTP fra quelli esistenti
#
# list_ftp elenca anche gli account di sistema, e non mette il login completo
# sempre nello stesso campo.
# --------------------------------------------------------------------------- #

RIGHE_FTP = [
    {"user": "utente", "serverlogin": "utente"},                       # account principale
    {"user": "ftp", "serverlogin": "ftp@esempio.com"},                 # ftp anonimo
    {"user": "lab@lab.esempio.com", "serverlogin": "lab@lab.esempio.com"},
    {"user": "solo_user@altro.esempio.com"},                           # senza serverlogin
]


def test_account_trovato():
    assert sa.cerca_account_ftp(RIGHE_FTP, "lab@lab.esempio.com") is True


def test_account_trovato_solo_nel_campo_user():
    assert sa.cerca_account_ftp(RIGHE_FTP, "solo_user@altro.esempio.com") is True


def test_account_non_trovato():
    assert sa.cerca_account_ftp(RIGHE_FTP, "nuovo@lab.esempio.com") is False


def test_login_parziale_non_conta_come_trovato():
    # "lab" da solo non e' l'account "lab@lab.esempio.com".
    assert sa.cerca_account_ftp(RIGHE_FTP, "lab") is False


def test_lista_vuota_o_sporca():
    assert sa.cerca_account_ftp([], "lab@lab.esempio.com") is False
    assert sa.cerca_account_ftp(None, "lab@lab.esempio.com") is False
    assert sa.cerca_account_ftp(["stringa", None], "lab@lab.esempio.com") is False


# --------------------------------------------------------------------------- #
# Traduzione degli errori GitHub
#
# Il 404 e' il caso che fa perdere tempo: GitHub lo restituisce anche quando
# il repository esiste ma il token non lo vede.
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


def test_404_spiega_che_puo_essere_un_permesso():
    msg = sa.spiega_errore_github(RispostaFinta(404), "Lettura del repository")
    assert "cannot see it" in msg


def test_403_su_workflow_indica_lo_scope_mancante():
    resp = RispostaFinta(403, {"message": "refusing to allow a PAT to create or update workflow"})
    msg = sa.spiega_errore_github(resp, "Scrittura del workflow")
    assert "workflow" in msg and "--skip-workflow-file" in msg


def test_403_generico_non_parla_di_scope_workflow():
    resp = RispostaFinta(403, {"message": "Resource not accessible"})
    msg = sa.spiega_errore_github(resp, "Scrittura del secret")
    assert "--skip-workflow-file" not in msg


def test_401_dice_di_rigenerare_il_token():
    msg = sa.spiega_errore_github(RispostaFinta(401), "Lettura del repository")
    assert "GITHUB_PAT" in msg


def test_403_senza_json_non_esplode():
    resp = RispostaFinta(403, None, text="<html>rate limited</html>")
    assert "403" in sa.spiega_errore_github(resp, "Cosa")


def test_status_inatteso_riporta_il_codice():
    assert "500" in sa.spiega_errore_github(RispostaFinta(500, text="boom"), "Cosa")
