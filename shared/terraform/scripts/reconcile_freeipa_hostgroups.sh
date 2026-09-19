#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

: "${IPA_CLIENT_HOSTNAME:?}"
: "${IPA_CLIENT_SERVERS:?}"
: "${IPA_CLIENT_HOSTGROUPS:?}"
: "${IPA_ENROLLER_PASSWORD:?}"
: "${IPA_CA_CERT:?}"

tmpdir=$(mktemp -d -t ipa-hostgroups.XXXXXX)
trap 'kdestroy -A >/dev/null 2>&1 || true; rm -rf "$tmpdir"' EXIT

unset KRB5_CONFIG
export KRB5CCNAME="FILE:${tmpdir}/krb5cc"
printf '%s\n' "$IPA_ENROLLER_PASSWORD" |
  kinit -c "$KRB5CCNAME" "${IPA_ENROLLER_PRINCIPAL:-ipa-enroller}"
ipa_enroller_user=${IPA_ENROLLER_PRINCIPAL:-ipa-enroller}
ipa_enroller_user=${ipa_enroller_user%%@*}

old_ifs=$IFS
IFS=,
for hostgroup in $IPA_CLIENT_HOSTGROUPS; do
  payload=$(printf '{"method":"hostgroup_add_member","params":[["%s"],{"host":["%s"]}],"id":0}' \
    "$hostgroup" "$IPA_CLIENT_HOSTNAME")
  added=false

  for api_server in $IPA_CLIENT_SERVERS; do
    cookie_jar="${tmpdir}/ipa-session-${api_server}.cookie"
    if curl --silent --show-error --noproxy '*' --http1.1 \
      --cacert "$IPA_CA_CERT" \
      -H "Referer: https://${api_server}/ipa" \
      -H 'Content-Type: application/x-www-form-urlencoded' \
      -H 'Accept: text/plain' \
      --cookie-jar "$cookie_jar" \
      --data-urlencode "user=${ipa_enroller_user}" \
      --data-urlencode "password=${IPA_ENROLLER_PASSWORD}" \
      --request POST "https://${api_server}/ipa/session/login_password" >/dev/null \
      && response=$(curl --silent --show-error --noproxy '*' --http1.1 \
        --cacert "$IPA_CA_CERT" \
        -H 'Content-Type: application/json' \
        -H 'Accept: application/json' \
        -H "Referer: https://${api_server}/ipa" \
        --cookie "$cookie_jar" \
        --data "$payload" "https://${api_server}/ipa/session/json"); then
      if printf '%s' "$response" | grep -Eq '"error"[[:space:]]*:[[:space:]]*null'; then
        added=true
        break
      fi
    fi
  done

  if [ "$added" != true ]; then
    echo "ERROR: cannot add ${IPA_CLIENT_HOSTNAME} to FreeIPA hostgroup ${hostgroup}" >&2
    exit 1
  fi
done
IFS=$old_ifs
