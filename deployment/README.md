# OCI deployment files

Copy `stechpay.service` to `/etc/systemd/system/stechpay.service` and
`nginx/stechpay.conf` to `/etc/nginx/sites-available/stechpay` on the Ubuntu
server. Replace `example.com` and `www.example.com` in the Nginx configuration
before enabling it.

Production secrets belong in `/etc/stechpay.env`, owned by `root:www-data` with
mode `640`. Do not copy an environment file into this repository.

`deploy.sh` updates an already configured server. Run it with Bash from
`/srv/stechpay`; it deliberately performs no database backup, so take a backup
before deploying schema changes.
