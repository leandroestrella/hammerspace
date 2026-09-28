#!/usr/bin/env sh
# shellcheck disable=SC2034
dns_cpanel_local_info='cPanel local zone (no API token)
 Writes the ACME TXT record into the DNS zone of the cPanel account it runs in,
 through the local cpapi2 command, so no credentials are stored anywhere.
 Public DNS only reaches that zone if the registrar delegates the
 _acme-challenge name (an NS record) to the hosting server nameserver.
 See docs/wildcard-certificate.md in hammerspace.
'

# Hook DNS per acme.sh: va copiato in ~/.acme.sh/dnsapi/ e gira sul server,
# come utente dell'account cPanel. cpapi2 da riga di comando non chiede
# token: agisce con i permessi dell'utente che lo lancia. Per questo niente
# credenziali in giro, a differenza del plugin dns_cpanel ufficiale.

# Uso: dns_cpanel_local_add _acme-challenge.example.com "txtvalue"
dns_cpanel_local_add() {
  fulldomain="$1"
  txtvalue="$2"
  if ! _cpl_find_zone "$fulldomain"; then
    _err "No DNS zone in this cPanel account contains $fulldomain"
    return 1
  fi
  _info "Adding TXT for $fulldomain to local zone $_cpl_zone"
  _cpl_resp=$(cpapi2 --output=json ZoneEdit add_zone_record domain="$_cpl_zone" name="$fulldomain." type=TXT txtdata="$txtvalue" ttl=60)
  _debug _cpl_resp "$_cpl_resp"
  # api2 risponde con HTTP 200 anche quando fallisce: l'esito vero sta in
  # data[0].result.status.
  if ! printf '%s' "$_cpl_resp" | perl -MJSON::PP -e 'local $/; my $j = decode_json(<STDIN>); my $d = $j->{cpanelresult}{data}[0] || {}; exit((($d->{result} || {})->{status} // 0) == 1 ? 0 : 1)'; then
    _err "add_zone_record failed: $_cpl_resp"
    return 1
  fi
  return 0
}

# Uso: dns_cpanel_local_rm _acme-challenge.example.com "txtvalue"
dns_cpanel_local_rm() {
  fulldomain="$1"
  txtvalue="$2"
  if ! _cpl_find_zone "$fulldomain"; then
    _err "No DNS zone in this cPanel account contains $fulldomain"
    return 1
  fi
  # Si cancella per numero di riga, e le righe successive scalano a ogni
  # rimozione: quindi dal basso verso l'alto. Si tocca solo il TXT con
  # questo valore esatto, non eventuali altri _acme-challenge (AutoSSL
  # ne lascia di suoi).
  _cpl_lines=$(cpapi2 --output=json ZoneEdit fetchzone_records domain="$_cpl_zone" name="$fulldomain." type=TXT |
    TXT="$txtvalue" perl -MJSON::PP -e 'local $/; my $j = decode_json(<STDIN>); for my $r (@{ $j->{cpanelresult}{data} || [] }) { print "$r->{line}\n" if defined $r->{txtdata} && $r->{txtdata} eq $ENV{TXT} }' |
    sort -rn)
  if [ -z "$_cpl_lines" ]; then
    _info "No TXT record with that value for $fulldomain, nothing to remove"
    return 0
  fi
  for _cpl_line in $_cpl_lines; do
    _info "Removing TXT for $fulldomain (zone line $_cpl_line)"
    cpapi2 --output=json ZoneEdit remove_zone_record domain="$_cpl_zone" line="$_cpl_line" >/dev/null
  done
  return 0
}

# Trova la zona dell'account che contiene il nome: si toglie un'etichetta
# alla volta finche' cPanel non riconosce il resto come zona. Chiedere a
# cPanel invece di tenere "le ultime due etichette" regge anche i .co.uk.
_cpl_find_zone() {
  _cpl_zone=""
  _cpl_rest="${1#*.}"
  while [ -n "$_cpl_rest" ] && [ "${_cpl_rest#*.}" != "$_cpl_rest" ]; do
    if uapi --output=json DNS parse_zone zone="$_cpl_rest" |
      perl -MJSON::PP -e 'local $/; my $j = decode_json(<STDIN>); exit(($j->{result}{status} // 0) == 1 ? 0 : 1)' 2>/dev/null; then
      _cpl_zone="$_cpl_rest"
      return 0
    fi
    _cpl_rest="${_cpl_rest#*.}"
  done
  return 1
}
