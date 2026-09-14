"""Test delle funzioni pure di create_subdomain.py.

Nessuna rete: si testano solo le parti che trasformano dati (validazione,
costruzione parametri, parsing risposte). Sono esattamente i punti in cui
si sono annidati i bug veri di questo script - una docroot presunta, un
campo dimenticato nel round-trip DNS, una risposta interpretata male.

    pip install -r requirements-dev.txt
    pytest -q
"""

import pytest

import create_subdomain as cs


# --------------------------------------------------------------------------- #
# Validazione del nome
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("nome", ["lab", "a", "test-2", "x1-y2-z3", "a" * 63])
def test_nomi_validi(nome):
    assert cs.valida_subdomain(nome) == nome


@pytest.mark.parametrize(
    "nome",
    [
        "",              # vuoto
        "-lab",          # trattino iniziale
        "lab-",          # trattino finale
        "la b",          # spazio
        "lab/../etc",    # separatore di path
        "lab.sub",       # punto
        'x") ; id ; ("', # il payload di shell injection del workflow
        "a" * 64,        # troppo lungo
        None,
    ],
)
def test_nomi_rifiutati(nome):
    with pytest.raises(ValueError):
        cs.valida_subdomain(nome)


# --------------------------------------------------------------------------- #
# Guardia sulla document root (qui si cancellano file veri)
# --------------------------------------------------------------------------- #

HOME = "/home/utente"


def test_docroot_normale_accettata():
    assert cs.assert_docroot_sicura(f"{HOME}/lab", HOME) == f"{HOME}/lab"


def test_docroot_con_slash_finale_normalizzata():
    assert cs.assert_docroot_sicura(f"{HOME}/lab/", HOME) == f"{HOME}/lab"


def test_docroot_annidata_accettata():
    # Sottodomini creati dalle versioni precedenti dello script.
    assert cs.assert_docroot_sicura(f"{HOME}/public_html/lab", HOME) == f"{HOME}/public_html/lab"


@pytest.mark.parametrize(
    "docroot",
    [
        HOME,                      # la home stessa
        f"{HOME}/",                # idem, con slash
        f"{HOME}/public_html",     # docroot del dominio principale
        "/home/altro/lab",         # fuori dalla home
        "/etc",                    # completamente fuori
        f"{HOME}/../altro/lab",    # traversal
        "",                        # vuoto
    ],
)
def test_docroot_pericolose_rifiutate(docroot):
    with pytest.raises(RuntimeError):
        cs.assert_docroot_sicura(docroot, HOME)


def test_home_mancante_rifiutata():
    with pytest.raises(RuntimeError):
        cs.assert_docroot_sicura(f"{HOME}/lab", "")


# --------------------------------------------------------------------------- #
# Lettura della docroot reale da domains_data
# --------------------------------------------------------------------------- #

RIGHE = [
    {"domain": "esempio.com", "documentroot": "/home/u/public_html", "homedir": "/home/u"},
    {"domain": "lab.esempio.com", "documentroot": "/home/u/lab", "homedir": "/home/u"},
    {"domain": "old.esempio.com", "documentroot": "/home/u/public_html/old", "homedir": "/home/u"},
]


def test_estrae_docroot_del_dominio_giusto():
    assert cs.estrai_docroot(RIGHE, "lab.esempio.com") == ("/home/u/lab", "/home/u")


def test_estrae_docroot_annidata():
    # Il caso che il path presunto ~/<subdomain> sbagliava.
    assert cs.estrai_docroot(RIGHE, "old.esempio.com") == ("/home/u/public_html/old", "/home/u")


def test_dominio_non_trovato():
    with pytest.raises(RuntimeError, match="not found"):
        cs.estrai_docroot(RIGHE, "inesistente.esempio.com")


def test_campi_mancanti():
    righe = [{"domain": "lab.esempio.com", "documentroot": None, "homedir": "/home/u"}]
    with pytest.raises(RuntimeError):
        cs.estrai_docroot(righe, "lab.esempio.com")


def test_righe_vuote():
    with pytest.raises(RuntimeError):
        cs.estrai_docroot([], "lab.esempio.com")


# --------------------------------------------------------------------------- #
# Round-trip dei record DNS (setHosts riscrive TUTTO)
# --------------------------------------------------------------------------- #

def _host(nome, tipo="A", **kw):
    base = {
        "HostName": nome, "RecordType": tipo, "Address": "1.2.3.4", "TTL": "60",
        "MXPref": "10", "Flags": None, "Tag": None, "IsDDNSEnabled": False,
    }
    base.update(kw)
    return base


def test_email_type_conservato():
    # Se EmailType non viene rimandato indietro, Namecheap puo' resettare
    # l'instradamento email del dominio.
    extra = cs.costruisci_set_hosts([_host("www")], "esempio", "com", email_type="MX")
    assert extra["EmailType"] == "MX"


def test_email_type_assente_non_inventato():
    extra = cs.costruisci_set_hosts([_host("www")], "esempio", "com", email_type=None)
    assert "EmailType" not in extra


def test_tutti_i_record_rimandati_indietro():
    hosts = [_host("www"), _host("blog"), _host("@")]
    extra = cs.costruisci_set_hosts(hosts, "esempio", "com")
    assert [extra[f"HostName{i}"] for i in (1, 2, 3)] == ["www", "blog", "@"]


def test_mxpref_solo_sugli_mx():
    hosts = [_host("www"), _host("@", tipo="MX", MXPref="20")]
    extra = cs.costruisci_set_hosts(hosts, "esempio", "com")
    assert "MXPref1" not in extra
    assert extra["MXPref2"] == "20"


def test_caa_conserva_flags_e_tag():
    hosts = [_host("@", tipo="CAA", Flags="0", Tag="issue")]
    extra = cs.costruisci_set_hosts(hosts, "esempio", "com")
    assert extra["Flags1"] == "0"
    assert extra["Tag1"] == "issue"


# --------------------------------------------------------------------------- #
# Interpretazione delle risposte cPanel
# --------------------------------------------------------------------------- #

def test_file_inesistente_riconosciuto():
    risposta = {"status": 0, "errors": ['The file "/home/u/lab/.htaccess" does not exist for the account.']}
    assert cs.file_inesistente(risposta) is True


def test_errore_di_lettura_non_scambiato_per_file_mancante():
    # Questo e' il caso in cui NON si deve sovrascrivere il .htaccess.
    risposta = {"status": 0, "errors": ["Permission denied"]}
    assert cs.file_inesistente(risposta) is False


def test_risposta_senza_errori():
    assert cs.file_inesistente({"status": 0}) is False


def test_api2_successo():
    data = {"cpanelresult": {"data": [{"result": 1}]}}
    assert cs.parse_api2_result(data, "cPanel")["result"] == 1


def test_api2_successo_come_stringa():
    data = {"cpanelresult": {"data": [{"result": "1"}]}}
    assert cs.parse_api2_result(data, "cPanel")["result"] == "1"


@pytest.mark.parametrize(
    "data",
    [
        {"cpanelresult": {"data": [{"result": 0, "reason": "non tuo"}]}},
        {"cpanelresult": {"data": []}},
        {"cpanelresult": {"error": "boom"}},
        {},
    ],
)
def test_api2_fallimento(data):
    with pytest.raises(RuntimeError):
        cs.parse_api2_result(data, "cPanel -> Domains")


# --------------------------------------------------------------------------- #
# Pagina iniziale con PostHog (LNDR-129)
# --------------------------------------------------------------------------- #

def test_snippet_canonico_e_cookieless():
    s = cs.carica_snippet_posthog()
    assert "posthog.init(" in s
    assert "cookieless_mode: 'always'" in s
    assert "person_profiles: 'never'" in s
    assert "api_host: 'https://relay.leandroestrella.com'" in s
    assert "ui_host: 'https://eu.posthog.com'" in s


def test_snippet_non_identifica_mai():
    s = cs.carica_snippet_posthog()
    # Lo stub elenca "identify" e "alias" come nomi di metodo: vietate sono le chiamate.
    assert "posthog.identify(" not in s
    assert "posthog.alias(" not in s


def test_snippet_tagga_il_progetto():
    # Tutti i siti condividono un progetto PostHog: il tag li separa nei report.
    s = cs.carica_snippet_posthog(project="lab")
    assert "posthog.register({ project: 'lab' })" in s
    assert cs.POSTHOG_PROJECT_PLACEHOLDER not in s


def test_snippet_senza_progetto_lascia_il_segnaposto():
    s = cs.carica_snippet_posthog()
    assert f"posthog.register({{ project: '{cs.POSTHOG_PROJECT_PLACEHOLDER}' }})" in s


def test_snippet_progetto_non_valido_rifiutato():
    with pytest.raises(ValueError):
        cs.carica_snippet_posthog(project="x' }); alert(1); ({ y: '")


def test_snippet_incompleto_rifiutato(tmp_path):
    finto = tmp_path / "snippet.html"
    finto.write_text("<script>posthog.init('phc_x', {})</script>")
    with pytest.raises(RuntimeError):
        cs.carica_snippet_posthog(finto)


def test_starter_page_contiene_snippet_e_titolo():
    pagina = cs.costruisci_starter_page("lab.example.com", cs.carica_snippet_posthog())
    assert pagina.startswith("<!DOCTYPE html>")
    assert "<title>lab.example.com</title>" in pagina
    assert pagina.index("posthog.init(") < pagina.index("</head>")
    assert '<meta name="robots" content="noindex">' in pagina


def test_starter_page_senza_posthog():
    # --skip-posthog: stessa pagina, nessuna traccia dello snippet.
    pagina = cs.costruisci_starter_page("lab.example.com")
    assert "posthog" not in pagina.lower()
    assert "<title>lab.example.com</title>" in pagina
    assert '<meta name="robots" content="noindex">' in pagina
    assert "</head>" in pagina


def test_starter_page_escapa_il_nome():
    pagina = cs.costruisci_starter_page("<b>x</b>", "<script>posthog.init(</script>")
    assert "<b>x</b>" not in pagina
    assert "&lt;b&gt;x&lt;/b&gt;" in pagina


# --------------------------------------------------------------------------- #
# Authorized URLs di PostHog (LNDR-147)
# --------------------------------------------------------------------------- #

LAB = "https://lab.leandroestrella.com"
ESISTENTI = ["https://leandroestrella.com", "https://www.leandroestrella.com"]


def test_app_url_aggiunto_in_fondo_senza_toccare_gli_altri():
    assert cs.aggiungi_app_url(ESISTENTI, LAB) == ESISTENTI + [LAB]
    assert ESISTENTI == ["https://leandroestrella.com", "https://www.leandroestrella.com"]


def test_app_url_gia_presente_non_duplicato():
    assert cs.aggiungi_app_url(ESISTENTI + [LAB], LAB) == ESISTENTI + [LAB]


@pytest.mark.parametrize("variante", [LAB + "/", LAB.upper(), f"  {LAB}  "])
def test_app_url_varianti_riconosciute_come_duplicati(variante):
    # La voce esistente resta com'era: non si riscrive quello che non e' nostro.
    assert cs.aggiungi_app_url([variante], LAB) == [variante]


def test_app_urls_duplicati_esistenti_ripuliti():
    assert cs.aggiungi_app_url([LAB, ESISTENTI[0], LAB + "/"], LAB) == [LAB, ESISTENTI[0]]


@pytest.mark.parametrize("vuoti", [None, []])
def test_app_urls_vuoti(vuoti):
    assert cs.aggiungi_app_url(vuoti, LAB) == [LAB]
    assert cs.rimuovi_app_url(vuoti, LAB) == []


def test_app_url_rimosso_in_tutte_le_varianti():
    assert cs.rimuovi_app_url([ESISTENTI[0], LAB, LAB + "/", ESISTENTI[1]], LAB) == ESISTENTI


def test_app_url_assente_rimozione_non_cambia_niente():
    assert cs.rimuovi_app_url(ESISTENTI, LAB) == ESISTENTI


def test_rimozione_non_tocca_altri_sottodomini():
    # "lab" non deve portarsi via "lab2" o "www.lab".
    altri = ["https://lab2.leandroestrella.com", "https://www.lab.leandroestrella.com"]
    assert cs.rimuovi_app_url(altri + [LAB], LAB) == altri


class _Risposta:
    def __init__(self, status_code, data=None):
        self.status_code = status_code
        self.ok = status_code < 400
        self._data = data or {}
        self.text = str(self._data)

    def json(self):
        return self._data


@pytest.fixture
def posthog_finto(monkeypatch):
    """Sostituisce requests con un finto progetto PostHog in memoria."""
    stato = {"app_urls": list(ESISTENTI), "patch": [], "get": 0, "get_status": 200}
    monkeypatch.setattr(cs, "POSTHOG_PERSONAL_API_KEY", "phx_finta")
    monkeypatch.setattr(cs, "ROOT_DOMAIN", "leandroestrella.com")

    def get(url, headers, timeout):
        assert url == "https://eu.posthog.com/api/environments/139609/"
        assert headers == {"Authorization": "Bearer phx_finta"}
        stato["get"] += 1
        return _Risposta(stato["get_status"], {"app_urls": list(stato["app_urls"])})

    def patch(url, headers, json, timeout):
        stato["patch"].append(json)
        stato["app_urls"] = json["app_urls"]
        return _Risposta(200, {"app_urls": list(stato["app_urls"])})

    monkeypatch.setattr(cs.requests, "get", get)
    monkeypatch.setattr(cs.requests, "patch", patch)
    return stato


def test_posthog_aggiunge_il_sottodominio(posthog_finto):
    assert cs.aggiorna_app_urls_posthog("lab") is True
    assert posthog_finto["patch"] == [{"app_urls": ESISTENTI + [LAB]}]


def test_posthog_gia_presente_niente_patch(posthog_finto):
    posthog_finto["app_urls"].append(LAB)
    assert cs.aggiorna_app_urls_posthog("lab") is True
    assert posthog_finto["patch"] == []


def test_posthog_rimuove_il_sottodominio(posthog_finto):
    posthog_finto["app_urls"].append(LAB)
    assert cs.aggiorna_app_urls_posthog("lab", rimuovi=True) is True
    assert posthog_finto["patch"] == [{"app_urls": ESISTENTI}]


def test_posthog_senza_chiave_saltato(posthog_finto, monkeypatch, capsys):
    monkeypatch.setattr(cs, "POSTHOG_PERSONAL_API_KEY", None)
    assert cs.aggiorna_app_urls_posthog("lab") is False
    assert posthog_finto["get"] == 0
    assert "POSTHOG_PERSONAL_API_KEY is not set" in capsys.readouterr().out


def test_posthog_dry_run_nessuna_chiamata(posthog_finto):
    assert cs.aggiorna_app_urls_posthog("lab", dry_run=True) is True
    assert posthog_finto["get"] == 0
    assert posthog_finto["patch"] == []


def test_posthog_chiave_senza_scope_e_un_warning(posthog_finto, capsys):
    # Il sottodominio esiste gia': un errore qui non deve far fallire il run.
    posthog_finto["get_status"] = 403
    assert cs.aggiorna_app_urls_posthog("lab") is False
    assert posthog_finto["patch"] == []
    out = capsys.readouterr().out
    assert "WARNING" in out and "project:write" in out
