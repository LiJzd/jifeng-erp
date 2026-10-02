#!/bin/sh
set -eu
cd /home/frappe/frappe-bench
if [ -L sites/assets ]; then
    [ "$(readlink sites/assets)" = "../assets" ] || { echo "Unexpected assets target" >&2; exit 1; }
elif [ -e sites/assets ]; then
    echo "Unexpected assets directory; inspect before replacing" >&2
    exit 1
else
    ln -s ../assets sites/assets 2>/dev/null || [ "$(readlink sites/assets)" = "../assets" ]
fi
exec "$@"
