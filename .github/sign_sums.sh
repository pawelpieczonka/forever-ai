#!/usr/bin/env bash
# Signs SHA256SUMS.txt with the release key (the release job of .github/workflows/release.yml):
#   RELEASE_SIGNING_KEY="$(cat release-key.pem)" .github/sign_sums.sh dist/SHA256SUMS.txt companion/foreverai/updates.py
# writes dist/SHA256SUMS.txt.sig, the Ed25519 signature (64 bytes) that the apps check before they take an update
# (companion/foreverai/updates.py, check_signature). The key is the PEM file of `openssl genpkey -algorithm ed25519`.
# Its public half must be one of RELEASE_KEYS in the given updates.py: the apps built from it take only releases signed
# with those, so the release stops here when the key in the secret is another one.
set -euo pipefail
sums="$1" source="$2"
if [[ -z "${RELEASE_SIGNING_KEY:-}" ]]; then
  echo "::error::RELEASE_SIGNING_KEY is empty: add the release key as a secret (CLAUDE.md, Self-update)"
  exit 1
fi
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
(umask 077 && printf '%s\n' "$RELEASE_SIGNING_KEY" > "$tmp/key.pem")
openssl pkey -in "$tmp/key.pem" -pubout -outform DER -out "$tmp/public.der"
# an Ed25519 public key in DER: 12 bytes that say so, then the key itself
if [[ "$(od -An -tx1 -N12 "$tmp/public.der" | tr -d ' \n')" != 302a300506032b6570032100 ]] ||
   [[ "$(wc -c < "$tmp/public.der")" -ne 44 ]]; then
  echo "::error::RELEASE_SIGNING_KEY is not an Ed25519 key"
  exit 1
fi
public="$(tail -c 32 "$tmp/public.der" | base64 -w0)"
if ! grep -qF -e "\"$public\"" -e "'$public'" "$source"; then
  echo "::error::the release key's public half $public is not in RELEASE_KEYS of $source"
  exit 1
fi
openssl pkeyutl -sign -inkey "$tmp/key.pem" -rawin -in "$sums" -out "$sums.sig"
openssl pkeyutl -verify -pubin -keyform DER -inkey "$tmp/public.der" -rawin -in "$sums" -sigfile "$sums.sig" > /dev/null
echo "Signed $sums with the release key $public"
