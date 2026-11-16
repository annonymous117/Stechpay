#!/usr/bin/env bash
# Install root-owned at /usr/local/bin/stechpay-ci-entrypoint.
# Authorize the CI public key with restrict,command="/usr/local/bin/stechpay-ci-entrypoint".
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
if [[ "${SSH_ORIGINAL_COMMAND:-}" == 'stechpay-check' ]]; then
    test -r /etc/stechpay.env
    test -w /var/backups/stechpay
    test -x /srv/stechpay/backend/.venv/bin/python
    git -C /srv/stechpay rev-parse --verify HEAD
    echo 'Restricted deployment access verified'
    exit 0
fi
if [[ ! "${SSH_ORIGINAL_COMMAND:-}" =~ ^stechpay-deploy\ ([0-9a-f]{40})$ ]]; then
    echo 'Only a tested Stechpay deployment revision is permitted' >&2
    exit 64
fi
target_sha=${BASH_REMATCH[1]}
cd /srv/stechpay
git fetch origin main
git merge-base --is-ancestor "$target_sha" origin/main
umask 077
deploy_script=$(mktemp /tmp/stechpay-deploy.XXXXXXXX)
trap 'rm -f "$deploy_script"' EXIT
git show "$target_sha:deployment/deploy.sh" > "$deploy_script"
# Reject the legacy script that lacks backup/configuration gates.
grep -Fq 'STECHPAY_BACKUP_DIR' "$deploy_script"
umask 022
bash "$deploy_script" "$target_sha"
