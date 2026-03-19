from wireguard_tools import WireguardKey

private = "COq0+9HvcWI5MAoaGQG4VU+j91OnPnHledKP3H2n0nI="
print( str(WireguardKey(private).public_key()))