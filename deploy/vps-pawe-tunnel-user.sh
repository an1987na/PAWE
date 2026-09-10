#!/bin/sh
set -eu

user=pawe-tunnel
keydir=/etc/ssh/authorized_keys
keyfile="$keydir/$user"
dropin=/etc/ssh/sshd_config.d/pawe-tunnel.conf

if ! id "$user" >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin "$user"
fi
passwd -l "$user" >/dev/null 2>&1 || true
install -d -o root -g root -m 0755 "$keydir"
cat > "$keyfile" <<'KEY'
restrict,port-forwarding,permitlisten="127.0.0.1:18443" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDXK0+AMCU7TYEXvV+pXqH9W5xrrMfB3GJO6dFvQhuKl pawe-tunnel
KEY
chown "$user:$user" "$keyfile"
chmod 0600 "$keyfile"
cat > "$dropin" <<'SSHD'
Match User pawe-tunnel
    AuthorizedKeysFile /etc/ssh/authorized_keys/%u
    AuthenticationMethods publickey
    PubkeyAuthentication yes
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    PermitTTY no
    AllowAgentForwarding no
    X11Forwarding no
    AllowTcpForwarding remote
    GatewayPorts no
    PermitListen 127.0.0.1:18443
    MaxSessions 0
    PermitTunnel no
Match all
SSHD
chown root:root "$dropin"
chmod 0644 "$dropin"
sshd -t
systemctl reload ssh
printf '%s\n' 'pawe tunnel ssh configuration loaded'
sshd -T -C user=pawe-tunnel,host=localhost,addr=127.0.0.1 2>/dev/null | awk 'tolower($1) ~ /^(authorizedkeysfile|authenticationmethods|pubkeyauthentication|passwordauthentication|kbdinteractiveauthentication|permittty|allowagentforwarding|x11forwarding|allowtcpforwarding|gatewayports|permitlisten|maxsessions|permittunnel)$/ {print}'
