#!/bin/sh
# Certificato wildcard Let's Encrypt per *.<dominio> su un account cPanel,
# validato via DNS sulla zona locale dell'account (hook dns_cpanel_local).
# Gira sul server, come utente dell'account. Idempotente.
#
#   sh setup_wildcard_certificate.sh example.com staging
#   sh setup_wildcard_certificate.sh example.com production
#
# Documentazione completa: docs/wildcard-certificate.md
set -eu

if [ $# -ne 2 ] || { [ "$2" != staging ] && [ "$2" != production ]; }; then
  echo "usage: sh $0 <domain> staging|production" >&2
  exit 2
fi
DOMAIN="$1"
MODE="$2"
CERT="*.$DOMAIN"

# acme.sh bloccato su una release precisa, verificata per commit: e' codice
# che gira con i permessi dell'account e con accesso alla sua zona DNS.
TAG=3.1.6
COMMIT=807da6498377ee5e0cf43a78091f46f12dc59a89

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/acme.sh-src"
ACME_HOME="$HOME/.acme.sh"
ACME="$ACME_HOME/acme.sh"

# Senza SSH lo si lancia da un cron temporaneo che scatta ogni minuto: il
# marcatore evita di ripetere una modalita' gia' conclusa, il lock evita due
# esecuzioni sovrapposte. Per rifare una modalita' basta cancellare il suo
# marcatore .done-<modalita'>.
[ -e "$HERE/.done-$MODE" ] && exit 0
mkdir "$HERE/.lock" 2>/dev/null || exit 0
trap 'rmdir "$HERE/.lock"' EXIT

echo "== $(date) $MODE for $CERT"

if [ ! -d "$SRC/.git" ]; then
  git clone --quiet --branch "$TAG" --depth 1 https://github.com/acmesh-official/acme.sh.git "$SRC"
fi
if [ "$(git -C "$SRC" rev-parse HEAD)" != "$COMMIT" ]; then
  echo "acme.sh checkout is not the pinned commit $COMMIT, stopping" >&2
  exit 1
fi

if [ ! -x "$ACME" ]; then
  (cd "$SRC" && ./acme.sh --install --home "$ACME_HOME" --nocron --noprofile)
fi
cp "$HERE/dns_cpanel_local.sh" "$ACME_HOME/dnsapi/dns_cpanel_local.sh"

if [ "$MODE" = staging ]; then
  # Prova generale contro la CA di staging, in una cartella a parte: non
  # tocca la configurazione del certificato vero, e non consuma i limiti
  # della CA di produzione.
  "$ACME" --issue --staging --cert-home "$HERE/staging-certs" \
    -d "$CERT" --dns dns_cpanel_local --dnssleep 30 --keylength 2048
else
  "$ACME" --issue --server letsencrypt \
    -d "$CERT" --dns dns_cpanel_local --dnssleep 30 --keylength 2048 || [ $? -eq 2 ]

  # L'hook cpanel_uapi di default e' in "auto mode": installa il
  # certificato su OGNI vhost che il wildcard copre, cioe' su tutti i
  # sottodomini, sostituendo i loro certificati AutoSSL. La modalita'
  # classica lo installa solo sul vhost *.<dominio>. acme.sh salva questa
  # scelta nella configurazione del certificato, quindi vale anche per ogni
  # rinnovo.
  DEPLOY_CPANEL_AUTO_ENABLED=false "$ACME" --deploy -d "$CERT" --deploy-hook cpanel_uapi

  # AutoSSL considera suo un certificato Let's Encrypt e proverebbe a
  # rinnovarlo (fallendo): meglio escludere il nome.
  uapi SSL add_autossl_excluded_domains domains="$CERT" >/dev/null

  echo
  echo "Now add the daily renewal in cPanel -> Cron Jobs (no percent signs in cron commands):"
  echo "  17 4 * * *  $ACME --cron --home $ACME_HOME > $HERE/renew-last-run.log 2>&1"
fi

touch "$HERE/.done-$MODE"
echo "== $(date) done $MODE"
