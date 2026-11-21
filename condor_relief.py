# condor_relief.py - Relief d'un paysage Condor (.trn) et zone atteignable en plané
#
# Prérequis : pip install numpy pyproj
#
# Format du fichier .trn (vérifié sur AA3 avec Mont-Blanc, Cervin, Eiger, Léman...) :
#   en-tête 36 octets : largeur (int), hauteur (int), 3 flottants (pas de la grille :
#   90, -90, 90), Est et Nord UTM de l'origine (float), zone UTM (int), hémisphère ('N')
#   puis largeur x hauteur altitudes en mètres (entiers 16 bits), rangées par colonnes :
#       indice = i * hauteur + j,   i = X / pas (X Condor, vers l'ouest),
#                                   j = Y / pas (Y Condor, vers le nord)
#   L'origine (X = 0, Y = 0) est le coin sud-est : Est = origine_E - X, Nord = origine_N + Y.
#
# Utilisable seul pour contrôle :  python condor_relief.py "chemin\AA3.trn" lat lon

import math
import struct
import sys

import numpy as np
from pyproj import Transformer


class Relief:
    def __init__(self, chemin_trn):
        with open(chemin_trn, "rb") as f:
            entete = f.read(36)
        self.largeur, self.hauteur = struct.unpack("<ii", entete[0:8])
        self.pas = abs(struct.unpack("<f", entete[8:12])[0])            # 90 m pour AA3
        self.origine_e, self.origine_n = struct.unpack("<ff", entete[20:28])
        self.zone = struct.unpack("<i", entete[28:32])[0]
        # lecture "à la demande" : le fichier (131 Mo pour AA3) n'est pas chargé en mémoire
        self.alt = np.memmap(chemin_trn, dtype="<i2", mode="r", offset=36,
                             shape=(self.largeur, self.hauteur))
        epsg = f"EPSG:326{self.zone:02d}"
        self.vers_utm = Transformer.from_crs("EPSG:4326", epsg, always_xy=True)
        self.vers_gps = Transformer.from_crs(epsg, "EPSG:4326", always_xy=True)
        self.meridien = 6 * self.zone - 183                             # méridien central

    # --- conversions -----------------------------------------------------------
    def latlon_vers_xy(self, lat, lon):
        e, n = self.vers_utm.transform(lon, lat)
        return self.origine_e - e, n - self.origine_n

    def xy_vers_latlon(self, x, y):
        lon, lat = self.vers_gps.transform(self.origine_e - np.asarray(x),
                                           self.origine_n + np.asarray(y))
        return lat, lon

    def convergence(self, lat, lon):
        """Angle (degrés) entre le nord géographique et le nord de la grille UTM."""
        return math.degrees(math.atan(math.tan(math.radians(lon - self.meridien))
                                      * math.sin(math.radians(lat))))

    # --- altitude du sol -----------------------------------------------------------
    def altitude_xy(self, x, y):
        """Altitude du sol (m) en coordonnées Condor ; marche aussi sur des tableaux."""
        i = np.clip(np.rint(np.asarray(x) / self.pas).astype(int), 0, self.largeur - 1)
        j = np.clip(np.rint(np.asarray(y) / self.pas).astype(int), 0, self.hauteur - 1)
        return self.alt[i, j]

    def altitude(self, lat, lon):
        return float(self.altitude_xy(*self.latlon_vers_xy(lat, lon)))


def zone_atteignable(relief, lat, lon, alt, axe_deg, vent_n=0.0, vent_e=0.0,
                     finesse=45.0, vitesse_kmh=100.0, marge=300.0,
                     ouverture_deg=90.0, pas_angle_deg=3.0, distance_max_km=120.0):
    """Zone atteignable en plané, en éventail ("phares") autour de la direction axe_deg.

    Pour chaque direction de l'éventail, on avance pas à pas (un pas de grille) :
      - la vitesse sol dans cette direction tient compte du vent (vent_n, vent_e en m/s,
        direction VERS laquelle souffle le vent) ;
      - la perte d'altitude par mètre parcouru au sol = taux de chute / vitesse sol ;
      - on s'arrête dès que le planeur passerait à moins de `marge` mètres du sol.
    Renvoie la liste des points (lat, lon) du contour : planeur, bords de l'éventail, planeur.
    """
    v = vitesse_kmh / 3.6
    chute = v / finesse                                     # m/s
    x0, y0 = relief.latlon_vers_xy(lat, lon)
    gamma = relief.convergence(lat, lon)

    s = np.arange(relief.pas, distance_max_km * 1000 + relief.pas, relief.pas)   # distances
    angles = np.arange(-ouverture_deg / 2, ouverture_deg / 2 + 1e-6, pas_angle_deg) + axe_deg
    contour = [(lat, lon)]
    pts_x, pts_y = [], []
    for a in angles:
        t = math.radians(a)                                 # direction vraie
        w_long = vent_n * math.cos(t) + vent_e * math.sin(t)
        w_trav = -vent_n * math.sin(t) + vent_e * math.cos(t)
        if v * v <= w_trav * w_trav:
            pts_x.append(x0); pts_y.append(y0); continue
        g = w_long + math.sqrt(v * v - w_trav * w_trav)     # vitesse sol dans cette direction
        if g <= 1.0:
            pts_x.append(x0); pts_y.append(y0); continue
        h = alt - s * chute / g                             # altitude du planeur le long du rayon
        tg = math.radians(a - gamma)                        # même direction, sur la grille UTM
        xs = x0 - s * math.sin(tg)                          # X Condor croît vers l'ouest
        ys = y0 + s * math.cos(tg)
        sol = relief.altitude_xy(xs, ys)
        trop_bas = np.nonzero(h < sol + marge)[0]
        k = trop_bas[0] - 1 if trop_bas.size else len(s) - 1
        if k < 0:
            pts_x.append(x0); pts_y.append(y0)
        else:
            pts_x.append(xs[k]); pts_y.append(ys[k])
    lats, lons = relief.xy_vers_latlon(pts_x, pts_y)
    contour += list(zip(np.atleast_1d(lats), np.atleast_1d(lons)))
    contour.append((lat, lon))
    return contour


if __name__ == "__main__":
    r = Relief(sys.argv[1])
    print(f"{r.largeur} x {r.hauteur} points, pas {r.pas} m, zone UTM {r.zone}, "
          f"origine E {r.origine_e:.0f} N {r.origine_n:.0f}")
    if len(sys.argv) >= 4:
        la, lo = float(sys.argv[2]), float(sys.argv[3])
        print(f"Altitude du sol en {la}, {lo} : {r.altitude(la, lo):.0f} m")


# =============================================================================
# Variante "vallées" : le planeur peut contourner le relief (suivre les vallées)
# =============================================================================
def zone_atteignable_vallees(relief, lat, lon, alt, axe_deg, vent_n=0.0, vent_e=0.0,
                             finesse=45.0, vitesse_kmh=100.0, marge=300.0,
                             ouverture_deg=90.0, pas_angle_deg=3.0, distance_max_km=50.0,
                             pas_m=270.0):
    """Zone atteignable en autorisant les détours (vallées, cols), affichée en éventail.

    Le terrain autour du planeur est découpé en cases de pas_m mètres. On cherche, case par
    case, l'altitude d'arrivée la plus haute possible (algorithme de Dijkstra : on étend
    toujours d'abord la case atteinte le plus haut). On passe d'une case à l'une de ses 8
    voisines en perdant l'altitude du plané (finesse et vent), et seulement si l'on reste à
    `marge` mètres au moins au-dessus du relief (le plus haut point de la case).
    Le contour renvoyé suit, dans chaque direction de l'éventail, la case atteignable la
    plus éloignée.
    """
    import heapq

    v = vitesse_kmh / 3.6
    chute = v / finesse
    e0, n0 = relief.vers_utm.transform(lon, lat)
    N = int(distance_max_km * 1000 / pas_m)
    cote = 2 * N + 1

    # relief de chaque case : le plus haut de 9 points (évite de "passer" entre deux points)
    off = np.arange(-N, N + 1) * pas_m
    ee, nn = np.meshgrid(e0 + off, n0 + off)                 # lignes = nord, colonnes = est
    sol = None
    for de in (-pas_m / 3, 0.0, pas_m / 3):
        for dn in (-pas_m / 3, 0.0, pas_m / 3):
            a = relief.altitude_xy(relief.origine_e - (ee + de), (nn + dn) - relief.origine_n)
            sol = a if sol is None else np.maximum(sol, a)
    plancher = (sol.astype(float) + marge).ravel().tolist()  # altitude minimale par case

    # perte d'altitude pour chacune des 8 directions (vent compris) ; direction en
    # (colonnes, lignes) = (est, nord)
    voisins = []
    for dc, dl in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
        d = math.hypot(dc, dl)
        ue, un = dc / d, dl / d
        w_long = vent_n * un + vent_e * ue
        w_trav = -vent_n * ue + vent_e * un
        if v * v <= w_trav * w_trav:
            continue
        g = w_long + math.sqrt(v * v - w_trav * w_trav)
        if g <= 1.0:
            continue
        voisins.append((dl * cote + dc, dc, d * pas_m * chute / g))

    meilleure = [-1e9] * (cote * cote)
    depart = N * cote + N
    meilleure[depart] = alt
    tas = [(-alt, depart)]
    while tas:
        h_neg, k = heapq.heappop(tas)
        h = -h_neg
        if h < meilleure[k]:
            continue
        c = k % cote
        for dk, dc, perte in voisins:
            c2 = c + dc
            if c2 < 0 or c2 >= cote:
                continue
            k2 = k + dk
            if k2 < 0 or k2 >= cote * cote:
                continue
            h2 = h - perte
            if h2 > meilleure[k2] and h2 >= plancher[k2]:
                meilleure[k2] = h2
                heapq.heappush(tas, (-h2, k2))

    # contour en éventail : case atteinte la plus lointaine dans chaque secteur angulaire
    atteint = np.array(meilleure) > -1e8
    lignes, cols = np.divmod(np.nonzero(atteint)[0], cote)
    de_ = (cols - N) * pas_m
    dn_ = (lignes - N) * pas_m
    dist = np.hypot(de_, dn_)
    gamma = relief.convergence(lat, lon)
    gisement = (np.degrees(np.arctan2(de_, dn_)) + gamma) % 360        # direction vraie
    angles = np.arange(-ouverture_deg / 2, ouverture_deg / 2 + 1e-6, pas_angle_deg)
    contour = [(lat, lon)]
    pts_e, pts_n = [], []
    for a in angles:
        cible = (axe_deg + a) % 360
        ecart = np.abs((gisement - cible + 180) % 360 - 180)
        dans = ecart <= pas_angle_deg / 2
        r = dist[dans].max() if dans.any() else 0.0
        t = math.radians(cible - gamma)
        pts_e.append(e0 + r * math.sin(t))
        pts_n.append(n0 + r * math.cos(t))
    lons, lats = relief.vers_gps.transform(pts_e, pts_n)
    contour += list(zip(lats, lons))
    contour.append((lat, lon))
    return contour
