#!/bin/bash

WIFI=$(find /sys/class/net -maxdepth 1 -name 'wl*' -o -name 'wlan*' | head -1 | xargs basename 2>/dev/null || true)
ETH=$(find /sys/class/net -maxdepth 1 -name 'en*' -o -name 'eth*' | head -1 | xargs basename 2>/dev/null || true)

# --- WiFi AP (only if hardware exists) ---
if [ -n "$WIFI" ] && [ -d "/sys/class/net/$WIFI" ]; then
    rfkill unblock wlan 2>/dev/null || true
    ip link set "$WIFI" down 2>/dev/null || true
    ip addr flush dev "$WIFI" 2>/dev/null || true
    ip addr add 192.168.50.1/24 dev "$WIFI"
    ip link set "$WIFI" up

    cat > /etc/hostapd/hostapd.conf <<HEOF
interface=$WIFI
driver=nl80211
ssid=SetupBox
hw_mode=g
channel=7
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase=setup1234
country_code=IT
ieee80211d=1
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
HEOF

    systemctl start hostapd || true

    cat > /etc/dnsmasq.d/setup.conf <<HhEOF
interface=$WIFI
bind-interfaces
dhcp-range=192.168.50.10,192.168.50.50,255.255.255.0,12h
dhcp-option=3,192.168.50.1
dhcp-option=6,192.168.50.1
# captive portal: EVERY name resolves to the box -> Android/Apple/Windows probes
# get the webapp and the OS opens the "Sign in to network" popup by itself
address=/#/192.168.50.1
no-resolv
address=/setupbox.local/192.168.50.1
HhEOF

    systemctl start dnsmasq || true

    iptables -t nat -I PREROUTING -i "$WIFI" -p tcp --dport 80 -j DNAT --to-destination 192.168.50.1:80 2>/dev/null || true
    iptables -t nat -I PREROUTING -i "$WIFI" -p tcp --dport 443 -j DNAT --to-destination 192.168.50.1:80 2>/dev/null || true
fi

# --- Ethernet ---
if [ -n "$ETH" ]; then
    ip link set "$ETH" up 2>/dev/null || true
fi

# --- mDNS ---
cat > /etc/avahi/services/http.service <<hhhEOF
<?xml version="1.0"?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">SetupBox</name>
  <service>
    <type>_http._tcp</type>
    <port>80</port>
  </service>
</service-group>
hhhEOF

systemctl restart avahi-daemon || true

# --- Start webapp ---
/usr/local/bin/webapp.py &
WEBAPP_PID=$!

# --- Wait for submission ---
while [ ! -f /var/lib/setup-data.json ]; do
    sleep 2
done

kill $WEBAPP_PID 2>/dev/null || true

# --- Create user from submitted data ---
USER=$(python3 -c "import json; print(json.load(open('/var/lib/setup-data.json'))['user'])")
PASS=$(python3 -c "import json; print(json.load(open('/var/lib/setup-data.json'))['password'])")
EMAIL=$(python3 -c "import json; print(json.load(open('/var/lib/setup-data.json'))['email'])")

useradd -m -s /bin/bash "$USER" || true
echo "$USER:$PASS" | chpasswd
usermod -aG sudo "$USER" 2>/dev/null || usermod -aG wheel "$USER" 2>/dev/null || true
usermod -aG docker "$USER" 2>/dev/null || true

echo "$EMAIL" > "/home/$USER/.email"
chown "$USER:$USER" "/home/$USER/.email"

# --- Clean up the setup environment: it must NOT survive later boots ---
systemctl stop hostapd dnsmasq 2>/dev/null || true
rm -f /etc/dnsmasq.d/setup.conf /etc/hostapd/hostapd.conf /etc/avahi/services/http.service
# drop the temporary mDNS name: from now on avahi uses the hostname (egocenter.local)
sed -i 's/^host-name=.*/#&/' /etc/avahi/avahi-daemon.conf
systemctl restart avahi-daemon || true

# --- Disable self and reboot (req.4) ---
touch /var/lib/setup-complete
systemctl disable first-boot-setup.service
systemctl reboot
