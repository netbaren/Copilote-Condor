# condor_fpl.py - Lecture d'un plan de vol Condor (.fpl) et conversion en lat/lon
#
# Prérequis : pip install pyproj
#
# Coordonnées Condor : X (m) croît vers l'OUEST, Y (m) croît vers le NORD,
# origine au coin sud-est du paysage. Pour chaque paysage :
#     Est UTM  = DECALAGE_X - X
#     Nord UTM = DECALAGE_Y + Y
# Valeurs vérifiées sur AA3 avec les points du fichier AA3.cup (écart < 1 m).
# Pour les autres paysages, zone et décalages sont lus dans l'en-tête de leur .trn
# (CHEMIN_PAYSAGES\<paysage>\<paysage>.trn) : mêmes valeurs que ci-dessous pour AA3.
#
# Utilisable seul pour contrôle :  python condor_fpl.py "chemin\du\fichier.fpl"

import configparser
import os
import struct
import sys
from pyproj import Transformer

PAYSAGES = {
    #  nom   : (zone UTM, DECALAGE_X, DECALAGE_Y, hémisphère)
    "AA3": (32, 1059840.0, 4786560.0, "N"),
}

# dossier des paysages de Condor (le copilote y met la valeur de son .ini)
CHEMIN_PAYSAGES = r"E:\ProgramFiles\Condor3\Landscapes"


def parametres_paysage(paysage):
    """(zone UTM, DECALAGE_X, DECALAGE_Y, hémisphère) : table ci-dessus, sinon en-tête du .trn
    (décalages : flottants aux octets 20 et 24 ; zone : entier à 28 ; hémisphère : 'N'/'S' à 32)."""
    if paysage in PAYSAGES:
        return PAYSAGES[paysage]
    chemin = os.path.join(CHEMIN_PAYSAGES, paysage, paysage + ".trn")
    try:
        with open(chemin, "rb") as f:
            entete = f.read(36)
    except OSError:
        raise ValueError(f"Paysage '{paysage}' : fichier introuvable {chemin}")
    dx, dy = struct.unpack("<ff", entete[20:28])
    zone = struct.unpack("<i", entete[28:32])[0]
    hemisphere = "S" if entete[32:33] in (b"S", b"s") else "N"
    PAYSAGES[paysage] = (zone, float(dx), float(dy), hemisphere)
    return PAYSAGES[paysage]


class Convertisseur:
    """Coordonnées Condor (X, Y) -> (latitude, longitude) pour un paysage."""

    def __init__(self, paysage):
        zone, self.dx, self.dy, hemisphere = parametres_paysage(paysage)
        epsg = (32600 if hemisphere == "N" else 32700) + zone      # UTM WGS84 nord / sud
        self.vers_gps = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)

    def latlon(self, x, y):
        lon, lat = self.vers_gps.transform(self.dx - x, self.dy + y)
        return lat, lon


class PointDeVirage:
    def __init__(self, nom, lat, lon, alt, rayon, angle):
        self.nom, self.lat, self.lon = nom, lat, lon
        self.alt = alt          # altitude du point (m)
        self.rayon = rayon      # rayon du secteur (m)
        self.angle = angle      # 360 = cylindre, 180 = demi-cercle, 90 = secteur FAI

    def __repr__(self):
        return f"{self.nom:20s} {self.lat:9.5f} {self.lon:9.5f}  {self.alt:5.0f} m  R={self.rayon:.0f} m"


class Tache:
    """Contenu utile d'un .fpl : paysage, points de virage, vent prévu."""

    def __init__(self, chemin):
        cfg = configparser.ConfigParser(strict=False, interpolation=None)
        cfg.optionxform = str                       # garder la casse des clés
        cfg.read(chemin, encoding="cp1252")

        t = cfg["Task"]
        self.paysage = t["Landscape"]
        conv = Convertisseur(self.paysage)

        self.points = []
        for i in range(int(t["Count"])):
            lat, lon = conv.latlon(float(t[f"TPPosX{i}"]), float(t[f"TPPosY{i}"]))
            self.points.append(PointDeVirage(
                t[f"TPName{i}"], lat, lon,
                float(t[f"TPPosZ{i}"]),
                float(t.get(f"TPRadius{i}", "0")),
                float(t.get(f"TPAngle{i}", "360"))))

        meteo = cfg["WeatherZone0"] if cfg.has_section("WeatherZone0") else {}
        self.vent_dir = float(meteo.get("WindDir", "0"))
        self.vent_vitesse = float(meteo.get("WindSpeed", "0"))
        self.description = cfg.get("Description", "Text", fallback="")
        self.planeur = cfg.get("Plane", "Name", fallback="")    # ex. AS33Me-18


if __name__ == "__main__":
    tache = Tache(sys.argv[1])
    print(f"Paysage {tache.paysage} - {tache.description}")
    for i, p in enumerate(tache.points):
        print(f"TP{i}  {p}")
    print(f"Vent prévu : {tache.vent_dir:.0f}° / {tache.vent_vitesse:.1f}")
