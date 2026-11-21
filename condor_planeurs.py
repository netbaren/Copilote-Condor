# condor_planeurs.py - VNE et volets des planeurs Condor (fichier CoTASA_data.txt)
#
# Format du fichier (repris de CoTASA, "Condor True Air Speed Alarm") :
#   [Condor 1] / [Condor 2] / [Condor 3]       : sections par version de Condor
#   TYPE=VNE,BALLAST_MAX                         : planeur sans volets
#   TYPE=VNE,BALLAST_MAX,MASSE_REF,MASSE_MAX,V1,VOLET1,V2,VOLET2,...
#       MASSE_REF : masse minimale dans Condor (sans eau)
#       MASSE_MAX : masse pour laquelle les vitesses de volets sont données
#       Vn,VOLETn : au-dessus de la vitesse Vn (km/h), volet conseillé VOLETn
#
# Utilisable seul pour contrôle :  python condor_planeurs.py CoTASA_data.txt AS33Me-18

import math
import sys


class Planeur:
    def __init__(self, nom, valeurs):
        self.nom = nom
        self.vne = float(valeurs[0])
        self.ballast_max = float(valeurs[1]) if len(valeurs) > 1 else 0.0
        self.masse_ref = self.masse_max = None
        self.volets = []                                  # [(vitesse, réglage), ...]
        if len(valeurs) >= 6:
            self.masse_ref = float(valeurs[2])
            self.masse_max = float(valeurs[3])
            reste = valeurs[4:]
            self.volets = [(float(reste[i]), reste[i + 1]) for i in range(0, len(reste) - 1, 2)]

    def volet_conseille(self, vitesse_kmh, eau_litres=0.0):
        """Réglage de volets conseillé pour la vitesse indiquée.
        Les vitesses du tableau valent pour MASSE_MAX ; elles se corrigent selon la masse
        actuelle (masse minimale + eau) : v x racine(masse actuelle / MASSE_MAX)."""
        if not self.volets:
            return None
        masse = self.masse_ref + max(0.0, eau_litres)
        k = math.sqrt(masse / self.masse_max)
        conseil = self.volets[0][1]
        for v, reglage in self.volets:
            if vitesse_kmh >= v * k:
                conseil = reglage
        return conseil


def fiche_condor(dossier_planes, nom):
    """Lit la fiche technique fournie par Condor : Planes\\<nom>\\<nom>.txt
    (lignes "Max speed, 270 km/h, ..." = VNE, "Water ballast, 175 l, ...").
    Renvoie un dict {"vne": ..., "eau_max": ...} ou None si la fiche est absente."""
    import os
    chemin = os.path.join(dossier_planes, nom, nom + ".txt")
    if not nom or not os.path.exists(chemin):
        return None
    fiche = {}
    with open(chemin, encoding="utf-8", errors="replace") as f:
        for ligne in f:
            morceaux = [m.strip() for m in ligne.split(",")]
            if len(morceaux) < 2:
                continue
            valeur = morceaux[1].split()[0] if morceaux[1] else ""
            try:
                if morceaux[0].lower() == "max speed":
                    fiche["vne"] = float(valeur)
                elif morceaux[0].lower() == "water ballast":
                    fiche["eau_max"] = float(valeur)
            except ValueError:
                pass
    return fiche if "vne" in fiche else None


class Planeurs:
    """Toutes les fiches du fichier ; en cas de doublon, la version de Condor la plus
    récente l'emporte."""

    def __init__(self, chemin):
        self.fiches = {}
        par_version = {}
        section = ""
        with open(chemin, encoding="utf-8-sig", errors="replace") as f:
            for ligne in f:
                ligne = ligne.strip()
                if not ligne or ligne.startswith(("#", "_#", ";")):
                    continue
                if ligne.startswith("["):
                    section = ligne.strip("[]")
                    continue
                if "=" in ligne and section.startswith("Condor"):
                    nom, texte = ligne.split("=", 1)
                    valeurs = [v.strip() for v in texte.split(",") if v.strip()]
                    try:
                        par_version.setdefault(section, {})[nom.strip()] = Planeur(nom.strip(), valeurs)
                    except (ValueError, IndexError):
                        pass                              # ligne mal formée : ignorée
        for version in sorted(par_version):              # Condor 1, puis 2, puis 3
            self.fiches.update(par_version[version])

    def get(self, nom):
        return self.fiches.get(nom)


if __name__ == "__main__":
    p = Planeurs(sys.argv[1])
    print(f"{len(p.fiches)} planeurs")
    if len(sys.argv) > 2:
        f = p.get(sys.argv[2])
        if f is None:
            print(f"{sys.argv[2]} : absent du fichier")
        else:
            print(f"{f.nom} : VNE {f.vne:.0f} km/h, ballast max {f.ballast_max:.0f} L, volets {f.volets}")
            for v in (80, 100, 130, 180):
                print(f"   {v} km/h sans eau -> volets {f.volet_conseille(v)} ; "
                      f"plein d'eau -> {f.volet_conseille(v, f.ballast_max)}")
