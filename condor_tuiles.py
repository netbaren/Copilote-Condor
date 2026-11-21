# condor_tuiles.py - Fond de carte "Relief Condor" peint d'après le fichier .trn du paysage
#
# Un petit serveur web local (sur cet ordinateur seulement) fabrique à la demande les
# tuiles de carte (256 x 256 pixels) à partir du relief du paysage Condor, avec le
# dégradé de couleurs choisi (par défaut celui de ColorGradient.java, projet Java trn
# de René ; voir PALETTES) et un léger
# ombrage. Les lacs et rivières viennent de la carte .bmp du paysage (même grille). La carte (tkintermapview) les demande comme à OpenStreetMap :
#     http://127.0.0.1:<port>/<paysage>/<z>/<x>/<y>.png
# Marche pour n'importe quel paysage : tout ce qu'il faut est dans l'en-tête du .trn.
#
# Utilisable seul pour contrôle :
#     python condor_tuiles.py "E:\ProgramFiles\Condor3\Landscapes" AA3 10 532 362

import io
import math
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
from PIL import Image

from condor_relief import Relief

# --- dégradés : seuils d'altitude (m) et couleurs (R, V, B) ---
def _hex(*codes):
    return [tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in codes]

PALETTES = {
    # dégradé de René (ColorGradient.java)
    "rene": ([0, 2, 24, 72, 120, 360, 800, 1600, 2500, 3300, 4000],
             _hex("#0000CD", "#C2B280", "#4F7942", "#277245", "#006B3C", "#77AB32",
                  "#008000", "#004225", "#A0522D", "#F0F8FF", "#FFFFF0")),
    # teintes de carte suisse : verts doux en plaine, jaunes et beiges en moyenne
    # montagne, bruns puis gris de roche, neige ; nuances resserrées entre 300 et 2500 m
    #   (plaines et fonds de vallée plus verts, pour un rendu naturel)
    "alpine": ([0, 1, 400, 700, 1000, 1300, 1600, 2000, 2400, 2800, 3300, 4000],
               _hex("#2B5C8A", "#7FB063", "#92BE70", "#B5CF86", "#DCDFA2", "#EED8A2",
                    "#DDB386", "#C59670", "#A98065", "#9C9590", "#D6D6D6", "#FFFFFF")),
    # première version d'alpine (plaines plus pâles)
    "alpine_pale": ([0, 1, 300, 600, 900, 1200, 1600, 2000, 2400, 2800, 3300, 4000],
                    _hex("#2B5C8A", "#A8C88A", "#B7D297", "#D3DEA4", "#ECE6B0", "#EED3A0",
                         "#DDB386", "#C59670", "#A98065", "#9C9590", "#D6D6D6", "#FFFFFF")),
    # sépia : tons chauds et peu éblouissants, sommets ivoire plutôt que blancs
    "sepia": ([0, 1, 300, 700, 1100, 1600, 2100, 2700, 3300, 4000],
              _hex("#4E6E8E", "#E9DFC4", "#E4D5B3", "#D9C397", "#C9AA7B", "#B48E63",
                   "#9C7553", "#8A6B55", "#B9AA96", "#F3EAD6")),
}

SEUILS, COULEURS = None, None          # remplis par choisir_palette()


def choisir_palette(nom):
    global SEUILS, COULEURS
    seuils, couleurs = PALETTES.get(nom, PALETTES["rene"])
    SEUILS = np.array(seuils, dtype=float)
    COULEURS = np.array(couleurs, dtype=float)


choisir_palette("rene")
HORS_PAYSAGE = (200, 200, 200)
COULEUR_EAU = (64, 128, 200)   # lacs et rivières, repérés sur la carte .bmp du paysage
# forêts (dossier ForestMaps du paysage) : 1 = conifères, 2 = feuillus, 3 = mélangée
COULEURS_FORET = {1: (52, 92, 46), 2: (84, 128, 58), 3: (66, 108, 52)}
OPACITE_FORET = 0.55           # 0 = invisible, 1 = couleur de forêt pure
ZOOM_MIN_FORETS = 10           # en deçà, les forêts ne sont pas dessinées (trop de fichiers)


def couleurs_altitude(alt):
    """Couleur (R, V, B) de chaque altitude : interpolation lissée (smoothstep) entre
    les seuils, comme ColorGradient.getColor()."""
    a = np.clip(alt, SEUILS[0], SEUILS[-1])
    i = np.clip(np.searchsorted(SEUILS, a, side="right"), 1, len(SEUILS) - 1)
    bas, haut = SEUILS[i - 1], SEUILS[i]
    t = (a - bas) / (haut - bas)
    t = t * t * (3 - 2 * t)
    c1, c2 = COULEURS[i - 1], COULEURS[i]
    return c1 + (c2 - c1) * t[..., None]


def _decaler(m, dl, dc):
    """Copie de m décalée de (dl, dc), bords remplis avec False."""
    r = np.zeros_like(m)
    h, w = m.shape
    r[max(dl, 0):h + min(dl, 0), max(dc, 0):w + min(dc, 0)] = \
        m[max(-dl, 0):h + min(-dl, 0), max(-dc, 0):w + min(-dc, 0)]
    return r


def fermeture(m, n):
    """Fermeture morphologique (bouche les petits trous et traits fins dans m)."""
    v = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))
    for _ in range(n):                                   # dilatation
        d = m.copy()
        for dl, dc in v:
            d |= _decaler(m, dl, dc)
        m = d
    for _ in range(n):                                   # érosion
        e = m.copy()
        for dl, dc in v:
            e &= _decaler(m, dl, dc)
        m = e
    return m


class FabriqueTuiles:
    def __init__(self, chemin_paysages, ombrage=0.45, palette="rene", forets=True):
        choisir_palette(palette)
        self.forets = forets
        self.cache_forets = {}            # morceaux de ForestMaps déjà lus
        self.chemin = chemin_paysages
        self.ombrage = ombrage            # 0 = carte plate ; 0.45 = relief bien visible
        self.reliefs = {}
        self.verrou = threading.Lock()    # pyproj et le cache ne sont pas partagés en parallèle
        self.cache = {}

    def relief(self, paysage):
        if paysage not in self.reliefs:
            r = Relief(os.path.join(self.chemin, paysage, paysage + ".trn"))
            r.nom = paysage
            r.eau = self._masque_eau(os.path.join(self.chemin, paysage, paysage + ".bmp"), r)
            self.reliefs[paysage] = r
        return self.reliefs[paysage]

    def _peindre_forets(self, r, rgb, x, y, dedans):
        _peindre_forets_dans(self, r, rgb, x, y, dedans)

    @staticmethod
    def _masque_eau(chemin_bmp, r):
        """Le .trn ne connaît pas l'eau, mais la carte .bmp du paysage (même grille de
        points, nord en haut, ouest à gauche) peint lacs et rivières en bleu.
        Renvoie un tableau oui/non de la taille du relief, ou None si pas de .bmp."""
        if not os.path.exists(chemin_bmp):
            return None
        Image.MAX_IMAGE_PIXELS = None
        try:
            carte = Image.open(chemin_bmp)
            if carte.size != (r.largeur, r.hauteur):
                return None
            if carte.mode == "P":                       # image à palette : on classe les couleurs
                palette = np.array(carte.getpalette()[:768]).reshape(-1, 3)
                bleu = ((palette[:, 2] > palette[:, 0] + 60) &
                        (palette[:, 1] > palette[:, 0] + 40))
                return bleu[np.asarray(carte)]
            rgb = np.asarray(carte.convert("RGB")).astype(int)
            return (rgb[..., 2] > rgb[..., 0] + 60) & (rgb[..., 1] > rgb[..., 0] + 40)
        except Exception:
            return None

    def tuile(self, paysage, z, x, y):
        cle = (paysage, z, x, y)
        with self.verrou:
            if cle in self.cache:
                return self.cache[cle]
            png = self._fabriquer(self.relief(paysage), z, x, y)
            if len(self.cache) > 600:
                self.cache.clear()
            self.cache[cle] = png
            return png

    def _fabriquer(self, r, z, x, y):
        n = 2 ** z
        k = (np.arange(256) + 0.5) / 256
        lons = (x + k) / n * 360.0 - 180.0
        lats = np.degrees(np.arctan(np.sinh(math.pi * (1 - 2 * (y + k) / n))))
        lon2, lat2 = np.meshgrid(lons, lats)
        e, nn = r.vers_utm.transform(lon2, lat2)
        fi = (r.origine_e - e) / r.pas                              # X Condor / pas
        fj = (nn - r.origine_n) / r.pas                             # Y Condor / pas
        i = np.rint(fi).astype(int)
        j = np.rint(fj).astype(int)
        dedans = (i >= 0) & (i < r.largeur) & (j >= 0) & (j < r.hauteur)
        alt = np.zeros(i.shape)
        if dedans.any():
            # interpolation bilinéaire entre les 4 points voisins : relief lisse quand on zoome
            a0 = np.clip(np.floor(fi[dedans]).astype(int), 0, r.largeur - 2)
            b0 = np.clip(np.floor(fj[dedans]).astype(int), 0, r.hauteur - 2)
            ta = np.clip(fi[dedans] - a0, 0, 1)
            tb = np.clip(fj[dedans] - b0, 0, 1)
            z00 = r.alt[a0, b0].astype(float);     z10 = r.alt[a0 + 1, b0].astype(float)
            z01 = r.alt[a0, b0 + 1].astype(float); z11 = r.alt[a0 + 1, b0 + 1].astype(float)
            alt[dedans] = ((z00 * (1 - ta) + z10 * ta) * (1 - tb)
                           + (z01 * (1 - ta) + z11 * ta) * tb)
        rgb = couleurs_altitude(alt)
        if self.forets and z >= ZOOM_MIN_FORETS and dedans.any():
            self._peindre_forets(r, rgb, fi * r.pas, fj * r.pas, dedans)
        if self.ombrage > 0:
            # ombrage : lumière venant du nord-ouest, 45° au-dessus de l'horizon
            pas_px = 156543.03 * math.cos(math.radians(lats.mean())) / n   # m par pixel
            gy, gx = np.gradient(alt, pas_px)       # gy : vers le sud (lignes), gx : vers l'est
            pente = np.arctan(np.hypot(gx, gy) * 1.5)
            orient = np.arctan2(-gx, gy)            # direction (vers l'aval) de la pente
            soleil_az, soleil_h = math.radians(315), math.radians(45)
            eclair = (math.sin(soleil_h) * np.cos(pente) + math.cos(soleil_h) * np.sin(pente)
                      * np.cos(soleil_az - orient))
            eclair = np.clip(eclair, 0, 1)
            rgb = rgb * ((1 - self.ombrage) + self.ombrage * eclair[..., None] / math.sin(soleil_h))
        if getattr(r, "eau", None) is not None and dedans.any():
            lig = r.hauteur - 1 - j[dedans]             # carte .bmp : ligne 0 au nord,
            col = r.largeur - 1 - i[dedans]             # colonne 0 à l'ouest
            m = 3                                       # marge autour du morceau utile
            l0, l1 = max(lig.min() - m, 0), min(lig.max() + m + 1, r.hauteur)
            c0, c1 = max(col.min() - m, 0), min(col.max() + m + 1, r.largeur)
            # les noms et les frontières écrits sur les lacs y font des "trous" :
            # on les bouche (fermeture : dilatation puis érosion de 2 points)
            morceau = fermeture(r.eau[l0:l1, c0:c1], 2)
            eau = np.zeros(i.shape, dtype=bool)
            eau[dedans] = morceau[lig - l0, col - c0]
            rgb[eau] = COULEUR_EAU
        rgb = np.clip(rgb, 0, 255)
        rgb[~dedans] = HORS_PAYSAGE
        image = Image.fromarray(rgb.astype(np.uint8), "RGB")
        tampon = io.BytesIO()
        image.save(tampon, "PNG", optimize=False)
        return tampon.getvalue()


def _lire_foret(dossier, col, lig, cache):
    """Un morceau de ForestMaps : fichier CCCLLL.for (CCC = colonne vers l'ouest,
    LLL = ligne vers le nord), carré d'octets rangé comme le .trn (premier indice
    vers l'ouest, second vers le nord). None si le fichier n'existe pas."""
    cle = (dossier, col, lig)
    if cle not in cache:
        if len(cache) > 300:
            cache.clear()
        chemin = os.path.join(dossier, f"{col:03d}{lig:03d}.for")
        try:
            octets = np.fromfile(chemin, dtype=np.uint8)
            n = int(round(math.sqrt(octets.size)))
            cache[cle] = octets.reshape(n, n) if n * n == octets.size else None
        except OSError:
            cache[cle] = None
    return cache[cle]


def _peindre_forets_dans(fabrique, r, rgb, x, y, dedans):
    dossier = os.path.join(fabrique.chemin, r.nom, "ForestMaps")
    if not os.path.isdir(dossier):
        return
    cote = 64 * r.pas                                   # un morceau = 64 x 64 points du relief
    col = np.floor(x / cote).astype(int)
    lig = np.floor(y / cote).astype(int)
    foret = np.zeros(x.shape, dtype=np.uint8)
    for c, l in set(zip(col[dedans].tolist(), lig[dedans].tolist())):
        morceau = _lire_foret(dossier, c, l, fabrique.cache_forets)
        if morceau is None:
            continue
        ici = dedans & (col == c) & (lig == l)
        n = morceau.shape[0]
        a = np.clip(((x[ici] - c * cote) / cote * n).astype(int), 0, n - 1)
        b = np.clip(((y[ici] - l * cote) / cote * n).astype(int), 0, n - 1)
        foret[ici] = morceau[a, b]
    for valeur, couleur in COULEURS_FORET.items():
        m = foret == valeur
        if m.any():
            rgb[m] = rgb[m] * (1 - OPACITE_FORET) + np.array(couleur) * OPACITE_FORET


def demarrer_serveur(fabrique):
    """Lance le serveur de tuiles dans un thread ; renvoie le numéro de port choisi."""

    class Requete(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                paysage, z, x, y = self.path.strip("/").removesuffix(".png").split("/")
                png = fabrique.tuile(paysage, int(z), int(x), int(y))
            except Exception:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.end_headers()
            self.wfile.write(png)

        def log_message(self, *args):       # pas de messages dans la console
            pass

    serveur = ThreadingHTTPServer(("127.0.0.1", 0), Requete)
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    return serveur.server_address[1]


if __name__ == "__main__":
    f = FabriqueTuiles(sys.argv[1])
    paysage, z, x, y = sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
    with open("tuile_test.png", "wb") as sortie:
        sortie.write(f.tuile(paysage, z, x, y))
    print("tuile_test.png écrite")
