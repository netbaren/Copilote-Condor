# diag_udp.py - Liste toutes les valeurs envoyées par Condor sur l'UDP pendant 15 s
# (à lancer pendant un vol, copilote_condor.py FERMÉ : un seul programme par port)
import socket, time
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("127.0.0.1", 55278))
sock.settimeout(1)
vues, fin, paquets = {}, time.time() + 15, 0
print("Écoute pendant 15 secondes...")
while time.time() < fin:
    try:
        data, _ = sock.recvfrom(8192)
    except socket.timeout:
        continue
    paquets += 1
    for ligne in data.decode("ascii", errors="ignore").splitlines():
        if "=" in ligne:
            cle, val = ligne.split("=", 1)
            vues[cle.strip()] = val.strip()
print(f"{paquets} paquets reçus, {len(vues)} valeurs différentes :")
for cle in sorted(vues):
    print(f"   {cle:22s} {vues[cle]}")
