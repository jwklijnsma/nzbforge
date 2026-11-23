#!/usr/bin/env bash
# Install the nzbforge Podman Quadlet units.
#   rootless (default):  ./install.sh
#   rootful:             sudo ./install.sh
set -euo pipefail
cd "$(dirname "$0")"

if [ "$(id -u)" -eq 0 ]; then
  unit_dir=/etc/containers/systemd
  env_dir=/etc/nzbforge
  ctl=(systemctl)
else
  unit_dir=${XDG_CONFIG_HOME:-$HOME/.config}/containers/systemd
  env_dir=${XDG_CONFIG_HOME:-$HOME/.config}/nzbforge
  ctl=(systemctl --user)
fi

command -v podman >/dev/null || { echo "podman is not installed" >&2; exit 1; }
ver=$(podman version --format '{{.Client.Version}}')
if [ "$(printf '%s\n4.4\n' "${ver%%-*}" | sort -V | head -n1)" != 4.4 ]; then
  echo "Podman $ver is too old for Quadlet (need 4.4+)" >&2; exit 1
fi

mkdir -p "$unit_dir" "$env_dir"
for f in nzbforge.network nzbforge-data.volume nzbforge.container; do
  if [ "$(id -u)" -eq 0 ]; then
    sed "s|%h/.config/nzbforge|$env_dir|" "$f" > "$unit_dir/$f"
  else
    install -m 644 "$f" "$unit_dir/$f"
  fi
done

if [ ! -f "$env_dir/nzbforge.env" ]; then
  install -m 600 nzbforge.env.example "$env_dir/nzbforge.env"
  key=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
  sed -i "s|^ADMIN_KEY=.*|ADMIN_KEY=$key|" "$env_dir/nzbforge.env"
  echo "Created $env_dir/nzbforge.env (random ADMIN_KEY). Fill in NNTP_* before starting."
fi

"${ctl[@]}" daemon-reload
echo
echo "Installed to $unit_dir. Next:"
echo "  ${ctl[*]} start nzbforge.service"
[ "$(id -u)" -ne 0 ] && echo "  loginctl enable-linger $USER     # keep running after logout / start at boot"
echo "  ${ctl[*]} enable --now podman-auto-update.timer   # optional: daily image updates"
