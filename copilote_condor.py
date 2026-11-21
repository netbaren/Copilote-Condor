# copilote_condor.py - Copilote pour Condor 3 : carte en direct, instruments, zone atteignable
#
# Prérequis : pip install pyserial pynmea2 tkintermapview pyproj numpy pillow keyboard
# Fichiers à garder dans le même dossier : condor_fpl.py, condor_relief.py, condor_planeurs.py,
# condor_tuiles.py, copilote_condor.ini, planeur_rouge.png
# (facultatif : CoTASA_data.txt, pour les volets conseillés)
#
# Deux sources de données Condor, lues chacune dans son propre thread :
#   - NMEA (port COM virtuel VSPE : Condor écrit sur COM10, le script lit COM11)
#       $GPGGA -> position, altitude : carte et trace
#       $GPRMC -> vitesse sol et route : calcul du vent
#   - UDP  (UDP.ini de Condor : Enabled=1, Host=127.0.0.1, Port=55278)
#       -> tangage, inclinaison, vitesse air, vario, cap compas
#
# Fenêtre : la carte à gauche ; à droite un panneau "tableau noir" qui contient
# de haut en bas : les commandes, l'horizon artificiel, la position, les
# instruments, l'indicateur de vent et la tâche.

import math
import os
import configparser
import queue
import socket
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

import serial          # lecture du port série virtuel
import pynmea2         # décodage des phrases NMEA
import tkintermapview  # widget carte (tuiles)
from tkintermapview import decimal_to_osm
from PIL import Image, ImageDraw, ImageTk   # icônes (Pillow, installé avec tkintermapview)

import condor_fpl
from condor_fpl import Tache     # lecture du .fpl + conversion X/Y -> lat/lon
from condor_relief import Relief, zone_atteignable, zone_atteignable_vallees
from condor_planeurs import Planeurs, fiche_condor   # VNE (fiches Condor) et volets (CoTASA)
from condor_tuiles import FabriqueTuiles, demarrer_serveur   # fond "Relief Condor"


# =============================================================================
# RÉGLAGES : valeurs par défaut. Le fichier copilote_condor.ini (même dossier) les
# remplace : c'est lui qu'il faut modifier, pas ce bloc.
# =============================================================================
PORT_COM = "COM11"     # côté "lecture" de la paire VSPE
BAUD = 4800            # débit utilisé par Condor
PORT_UDP = 55278       # doit correspondre au Port= de UDP.ini

# Horizon artificiel
TAILLE_HORIZON = 260          # diamètre de l'horizon (170 à 330 pixels)
SENS_INCLINAISON = 1          # mettre -1 si l'horizon penche du mauvais côté en virage
SENS_TANGAGE = 1              # mettre -1 si l'horizon monte quand on cabre

# Indicateur de vent
RAPPORT_VENT = 0.8            # diamètre du cadran de vent / diamètre de l'horizon
VENT_MAX_KMH = 50             # vent qui donne une flèche de longueur maximale
LISSAGE_VENT = 0.05           # 0.05 : moyenne sur ~20 s ; plus grand = plus nerveux
VITESSE_AIR_EST_IAS = True    # True : Condor envoie la vitesse indiquée -> corrigée en vitesse vraie

# Carte
COULEUR_TRACE = "#1f5fd0"     # trace du planeur (bleu)
EPAISSEUR_TRACE = 2
COULEUR_TACHE = "#d00000"     # segments de la tâche (rouge)
EPAISSEUR_TACHE = 2
COULEUR_TP = "purple"         # épingles et noms des points de virage
COULEUR_TP_OPENTOPOMAP = "#0058c8"   # sur OpenTopoMap (relief à dominante rouge) : bleu
HAUTEUR_TP = 30               # hauteur des épingles (pixels)
# Zone atteignable en plané ("phares") : éventail devant le planeur, relief compris
CHEMIN_PAYSAGES = r"E:\ProgramFiles\Condor3\Landscapes"   # contient AA3\AA3.trn, etc.
PAYSAGE_DEFAUT = "AA3"        # utilisé tant qu'aucun .fpl n'est chargé
# La finesse et la vitesse sont MESURÉES en vol : aucune donnée du planeur n'est nécessaire.
FENETRE_FINESSE_S = 4         # moyenne des mesures (10 par seconde) sur cette durée
FINESSE_MIN = 5               # bornes de sécurité de la mesure, valables pour tout planeur
FINESSE_MAX = 60              #   (air porteur : on plafonne au lieu d'annoncer l'infini)
INCLINAISON_MAX_MESURE = 15   # au-delà (spirale, virage), on ne mesure pas
MARGE_HAUTEUR = 300           # hauteur minimale à garder au-dessus du relief (m)
OUVERTURE_PHARE = 90          # largeur de l'éventail (degrés)
AXE_PHARE = "route"           # "route" (trajectoire sol, NMEA) ou "cap" (nez du planeur)
PORTEE_AVEC_VENT = True       # False : calcul en air calme
MODE_PHARE = "vallees"        # "vallees" : détours permis (suit vallées et cols)
                              # "droit"   : lignes droites seulement (plus prudent)
DISTANCE_MAX_KM = 60          # on ne cherche pas au-delà
PERIODE_PORTEE_S = 4          # recalcul toutes les 4 secondes
COULEUR_PORTEE = "black"
EPAISSEUR_PORTEE = 2
COULEUR_BORD_PORTEE = "black" # liseré sous le contour (lisible sur les zones vertes) ; "" = aucun
LARGEUR_BORD_PORTEE = 1       # largeur du liseré de chaque côté du contour (pixels)
# Recentrage automatique de la carte sur le planeur
RECENTRAGE_AUTO = True
# Raccourcis clavier GLOBAUX : marchent même quand Condor a la main (pip install keyboard).
# La touche Alt n'est pas utilisée par Condor ; la combinaison est interceptée et
# n'arrive pas jusqu'à Condor (Alt+C ne recentre pas son trim, par exemple).
RACCOURCIS_GLOBAUX = True
TOUCHE_ZOOM_PLUS = "alt+plus"
TOUCHE_ZOOM_MOINS = "alt+-"
TOUCHE_RECENTRER = "alt+c"
TOUCHE_FOND_SUIVANT = "alt+s"
ZONE_RECENTRAGE = 0.25        # recentre quand le planeur entre dans la bande extérieure
                              # de 25 % de la carte (0.5 = recentrage permanent)
PAUSE_RECENTRAGE_S = 20       # pas de recentrage pendant 20 s après un geste sur la carte
ICONE_PLANEUR = "planeur_rouge.png"   # nez vers le haut
LARGEUR_ICONE = 56                    # largeur de l'icône (pixels)

# Panneau "tableau noir"
COULEUR_PANNEAU = "#1c1c1c"
COULEUR_TEXTE = "white"
COULEUR_ERREUR = "#ff6b6b"
COULEUR_BOUTON = "#333333"
COULEUR_HEURE = "#d01010"      # heure de Condor, en haut du panneau
COULEUR_CADRE_FINESSE = "#d01010"   # cadre autour de la finesse
COULEUR_EAU = "#2f8fff"       # jauge de ballast sous "Eau"
COULEUR_ALERTE_SOL = "orange"  # hauteur sol affichée dans cette couleur sous MARGE_HAUTEUR
# VNE : lue dans la fiche de chaque planeur fournie par Condor (Planes\<nom>\<nom>.txt)
CHEMIN_PLANES = r"E:\ProgramFiles\Condor3\Planes"
# Volets conseillés (et VNE de secours) : fichier de CoTASA, dans le dossier du script
FICHIER_PLANEURS = "CoTASA_data.txt"
COULEUR_VMOY_ARRIVEE = "#40d040"   # vitesse moyenne figée à l'arrivée
SEUIL_VNE = 0.9               # alerte orange à partir de 90 % de la VNE (vitesse vraie)
COULEUR_VNE_PROCHE = "orange"
COULEUR_VNE_DEPASSEE = "red"
# VNE inconnue (pas de fiche Condor ni CoTASA) : valeur par défaut, modifiable d'un clic
# sur la case VNE ; la valeur choisie est retenue par planeur dans FICHIER_VNE_PERSO
VNE_DEFAUT = 250
COULEUR_VNE_PROPOSEE = "#7fb8ff"   # VNE proposée (cliquable), à distinguer d'une VNE connue
FICHIER_VNE_PERSO = "vne_perso.txt"

FONDS = {
    "OpenTopoMap (relief)":
        ("https://a.tile.opentopomap.org/{z}/{x}/{y}.png", 17),
    "OpenStreetMap":
        ("https://tile.openstreetmap.org/{z}/{x}/{y}.png", 19),
    "Satellite (Esri)":
        ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", 19),
    # peinte d'après le relief .trn du paysage Condor (condor_tuiles.py), sans internet
    "Relief Condor": (None, 13),
}
OMBRAGE_RELIEF = 0.45         # "Relief Condor" : 0 = couleurs seules, 0.45 = relief ombré
PALETTE_RELIEF = "rene"       # "Relief Condor" : dégradé rene, alpine ou sepia
FORETS_RELIEF = True          # "Relief Condor" : forêts (dossier ForestMaps du paysage)
FOND_DEPART = "OpenTopoMap (relief)"
# Fenêtre : position (x, y : coin haut gauche, en pixels) et dimensions au démarrage
FENETRE_X = 0
FENETRE_Y = 0
FENETRE_LARGEUR = 1250
FENETRE_HAUTEUR = 1000
FENETRE_AGRANDIE = False      # True : plein écran (agrandie) au démarrage
MEMORISER_FENETRE = True      # True : reprend la position et la taille de la dernière fermeture
# Plan de vol "courant" que Condor réécrit à chaque vol (solo ou réseau) :
# Documents\Condor3\Pilots\<pilote>\Flightplan.fpl. Surveillé et rechargé tout seul.
FPL_COURANT = ""
SURVEILLANCE_FPL_S = 5        # vérification toutes les 5 secondes
DOSSIER_FPL = os.path.join(os.path.expanduser("~"), "Documents", "Condor 3", "FlightPlans")


def charger_reglages():
    """Lit copilote_condor.ini et remplace les valeurs par défaut ci-dessus.
    Chaque clé du fichier porte le nom d'un réglage (majuscules ou minuscules) ;
    sa valeur est convertie dans le type de la valeur par défaut (nombre, texte, oui/non).
    Renvoie la liste des problèmes rencontrés (clé inconnue, valeur illisible)."""
    chemin = os.path.join(os.path.dirname(os.path.abspath(__file__)), "copilote_condor.ini")
    problemes = []
    if not os.path.exists(chemin):
        return ["copilote_condor.ini absent : réglages par défaut"]
    cfg = configparser.ConfigParser(inline_comment_prefixes=(";",), interpolation=None)
    try:
        cfg.read(chemin, encoding="utf-8-sig")
    except UnicodeDecodeError:
        cfg.read(chemin, encoding="cp1252")
    for section in cfg.sections():
        for cle, texte in cfg[section].items():
            nom = cle.upper()
            if nom not in REGLABLES:
                problemes.append(f"copilote_condor.ini : réglage inconnu « {cle} »")
                continue
            defaut = globals()[nom]
            texte = texte.strip().strip('"')
            try:
                if isinstance(defaut, bool):
                    valeur = texte.lower() in ("1", "oui", "vrai", "true", "yes")
                elif isinstance(defaut, int):
                    valeur = int(float(texte))
                elif isinstance(defaut, float):
                    valeur = float(texte)
                else:
                    valeur = texte
                globals()[nom] = valeur
            except ValueError:
                problemes.append(f"copilote_condor.ini : valeur illisible pour {cle} = {texte}")
    return problemes


REGLABLES = {nom for nom in list(globals()) if nom.isupper() and nom != "FONDS"}
problemes_reglages = charger_reglages()

# serveur local des tuiles "Relief Condor" (les requêtes vers 127.0.0.1 ne passent
# jamais par un éventuel proxy)
os.environ["NO_PROXY"] = ",".join(filter(None, [os.environ.get("NO_PROXY"), "127.0.0.1,localhost"]))
condor_fpl.CHEMIN_PAYSAGES = CHEMIN_PAYSAGES    # zone et décalages lus dans les .trn
PORT_TUILES = demarrer_serveur(FabriqueTuiles(CHEMIN_PAYSAGES, OMBRAGE_RELIEF, PALETTE_RELIEF,
                                              FORETS_RELIEF))
if FOND_DEPART not in FONDS:
    problemes_reglages.append(f"copilote_condor.ini : fond de carte inconnu « {FOND_DEPART} »")
    FOND_DEPART = next(iter(FONDS))


# =============================================================================
# Unité 1 : acquisition NMEA (thread)
# =============================================================================
positions = queue.Queue()     # (lat, lon, alt)
sol = queue.Queue()           # (vitesse sol m/s, route degrés)
instruments = queue.Queue()   # dict des valeurs UDP
erreurs = queue.Queue()       # messages d'erreur des threads

def lecteur_nmea():
    try:
        ser = serial.Serial(PORT_COM, BAUD, timeout=1)
    except serial.SerialException as e:
        erreurs.put(f"NMEA : impossible d'ouvrir {PORT_COM} ({e})")
        return
    with ser:
        while True:
            ligne = ser.readline().decode("ascii", errors="ignore").strip()
            try:
                if ligne.startswith("$GPGGA"):
                    msg = pynmea2.parse(ligne)
                    positions.put((msg.latitude, msg.longitude, msg.altitude))
                elif ligne.startswith("$GPRMC"):
                    msg = pynmea2.parse(ligne)
                    if msg.spd_over_grnd is not None and msg.true_course is not None:
                        sol.put((float(msg.spd_over_grnd) * 0.514444,   # nœuds -> m/s
                                 float(msg.true_course)))
            except (pynmea2.ParseError, ValueError, TypeError):
                pass                          # ligne abîmée : on l'ignore


# =============================================================================
# Unité 2 : acquisition UDP (thread)
# =============================================================================
def lecteur_udp():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", PORT_UDP))
    except OSError as e:
        erreurs.put(f"UDP : port {PORT_UDP} indisponible ({e})")
        return
    while True:
        data, _ = sock.recvfrom(8192)
        valeurs = {}
        for ligne in data.decode("ascii", errors="ignore").splitlines():
            if "=" in ligne:
                cle, val = ligne.split("=", 1)
                try:
                    valeurs[cle.strip()] = float(val)
                except ValueError:
                    pass
        if valeurs:
            instruments.put(valeurs)


# =============================================================================
# Unité 3 : calcul du vent
#   vent = vecteur vitesse sol (NMEA)  -  vecteur vitesse air (UDP : vitesse + cap)
#   lissé dans le temps, car chaque mesure isolée est bruitée.
# =============================================================================
class CalculVent:
    def __init__(self):
        self.n = self.e = 0.0         # vent lissé (composantes nord et est, m/s, "vers")
        self.pret = False

    @staticmethod
    def vitesse_vraie(vitesse_air, altitude):
        if not VITESSE_AIR_EST_IAS:
            return vitesse_air
        sigma = max(0.3, (1 - 2.25577e-5 * altitude) ** 4.2559)   # atmosphère standard
        return vitesse_air / math.sqrt(sigma)

    def ajouter(self, v_sol, route, v_air, cap, altitude):
        if v_sol < 5 or v_air < 15:   # au sol ou à l'arrêt : pas de calcul
            return
        tas = self.vitesse_vraie(v_air, altitude)
        wn = v_sol * math.cos(math.radians(route)) - tas * math.cos(math.radians(cap))
        we = v_sol * math.sin(math.radians(route)) - tas * math.sin(math.radians(cap))
        if not self.pret:
            self.n, self.e, self.pret = wn, we, True
        else:
            self.n += LISSAGE_VENT * (wn - self.n)
            self.e += LISSAGE_VENT * (we - self.e)

    def vitesse_kmh(self):
        return math.hypot(self.n, self.e) * 3.6

    def provenance(self):
        """Direction d'où vient le vent (degrés, convention météo)."""
        return (math.degrees(math.atan2(-self.e, -self.n)) + 360) % 360


# =============================================================================
# Unité 4 : fenêtre, panneau et carte
# =============================================================================
fenetre = tk.Tk()
fenetre.title("Copilote Condor")
# position et taille : celles de la dernière fermeture si elles ont été mémorisées,
# sinon celles du fichier de réglages
FICHIER_FENETRE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "derniere_fenetre.txt")
geometrie = f"{FENETRE_LARGEUR}x{FENETRE_HAUTEUR}+{FENETRE_X}+{FENETRE_Y}"
if MEMORISER_FENETRE and os.path.exists(FICHIER_FENETRE):
    with open(FICHIER_FENETRE) as f:
        geometrie = f.read().strip() or geometrie
try:
    fenetre.geometry(geometrie)
except tk.TclError:
    fenetre.geometry(f"{FENETRE_LARGEUR}x{FENETRE_HAUTEUR}+{FENETRE_X}+{FENETRE_Y}")
if FENETRE_AGRANDIE:
    try:
        fenetre.state("zoomed")
    except tk.TclError:
        pass

def fermer_fenetre():
    """À la fermeture : mémorise position et taille (si la fenêtre n'est pas agrandie)."""
    if MEMORISER_FENETRE and fenetre.state() == "normal":
        try:
            with open(FICHIER_FENETRE, "w") as f:
                f.write(fenetre.geometry())
        except OSError:
            pass
    fenetre.destroy()

fenetre.protocol("WM_DELETE_WINDOW", fermer_fenetre)

# Le panneau est placé AVANT la carte : la carte prend ensuite tout l'espace restant.
# Sa largeur est fixée par le diamètre de l'horizon (pack_propagate(False) : le contenu
# ne peut pas l'élargir ; les textes trop longs passent à la ligne).
panneau = tk.Frame(fenetre, padx=8, pady=8, bg=COULEUR_PANNEAU, width=TAILLE_HORIZON + 16)
panneau.pack(side="right", fill="y")
panneau.pack_propagate(False)

carte = tkintermapview.TkinterMapView(fenetre, corner_radius=0)
carte.pack(side="left", fill="both", expand=True)
carte.set_zoom(11)
if hasattr(carte, "show_zoom_buttons"):
    carte.show_zoom_buttons(False)     # zoom par les boutons du panneau (en bas)


etiquettes = []          # toutes les étiquettes du panneau (pour le retour à la ligne)

def etiquette(texte, police=("Arial", 12), parent=None, largeur=0):
    """Étiquette du panneau. largeur = place enlevée à droite ou à gauche (ex. indicateur)."""
    lbl = tk.Label(parent or panneau, text=texte, font=police, anchor="w", justify="left",
                   bg=COULEUR_PANNEAU, fg=COULEUR_TEXTE, wraplength=TAILLE_HORIZON - largeur)
    lbl.pack(fill="x", pady=1)
    lbl.largeur = largeur
    etiquettes.append(lbl)
    return lbl


# =============================================================================
# Unité 5 : horizon artificiel (transposé de ArtificialHorizonPanel.java)
# =============================================================================
class HorizonArtificiel(tk.Canvas):
    CIEL = "#66b2ff"
    TERRE = "#cc6600"

    def __init__(self, parent, taille):
        super().__init__(parent, width=taille, height=taille,
                         bg=COULEUR_PANNEAU, highlightthickness=0)
        self.taille = taille
        self.tangage = self.inclinaison = 0.0
        self.dessiner(0.0, 0.0)

    def redimensionner(self, taille):
        self.taille = taille
        self.config(width=taille, height=taille)
        self.dessiner(self.tangage, self.inclinaison)

    def dessiner(self, tangage_deg, inclinaison_deg):
        self.tangage, self.inclinaison = tangage_deg, inclinaison_deg
        self.delete("all")
        t = self.taille
        k = t / 260                             # échelle : tout suit le diamètre
        cx = cy = t / 2
        rayon = t / 2 - 6
        px_par_degre = t / 3.0 / 30.0          # 30° visibles de chaque côté

        a = math.radians(inclinaison_deg)
        ux, uy = math.cos(a), -math.sin(a)     # direction de la ligne d'horizon
        nx, ny = math.sin(a), math.cos(a)      # perpendiculaire, vers la terre
        d = tangage_deg * px_par_degre         # nez haut -> horizon bas
        hx, hy = cx + nx * d, cy + ny * d

        L = 2 * t
        self.create_rectangle(0, 0, t, t, fill=self.CIEL, outline="")
        self.create_polygon(hx - ux * L, hy - uy * L, hx + ux * L, hy + uy * L,
                            hx + ux * L + nx * L, hy + uy * L + ny * L,
                            hx - ux * L + nx * L, hy - uy * L + ny * L,
                            fill=self.TERRE, outline="")
        self.create_line(hx - ux * L, hy - uy * L, hx + ux * L, hy + uy * L,
                         fill="white", width=2)
        for deg in (-30, -20, -10, 10, 20, 30):
            mx = hx - nx * deg * px_par_degre
            my = hy - ny * deg * px_par_degre
            demi = (40 if deg % 20 == 0 else 20) * k
            self.create_line(mx - ux * demi, my - uy * demi,
                             mx + ux * demi, my + uy * demi, fill="white")

        # anneau épais couleur du fond : cache ce qui dépasse du cercle
        e = t
        self.create_oval(cx - rayon - e / 2, cy - rayon - e / 2,
                         cx + rayon + e / 2, cy + rayon + e / 2,
                         outline=COULEUR_PANNEAU, width=e)
        # maquette fixe et cadran
        self.create_line(cx - 40 * k, cy, cx - 10 * k, cy, fill="black", width=4)
        self.create_line(cx + 10 * k, cy, cx + 40 * k, cy, fill="black", width=4)
        self.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill="black")
        self.create_oval(cx - rayon, cy - rayon, cx + rayon, cy + rayon,
                         outline="gray60", width=3)


# =============================================================================
# Unité 6 : indicateur de vent (vu depuis le planeur, nez vers le haut)
#   La flèche montre le vent tel qu'il frappe le planeur : elle part du côté
#   d'où il vient ; sa longueur est proportionnelle à sa force.
#   Le "N" sur le cadran indique où se trouve le nord.
# =============================================================================
class IndicateurVent(tk.Canvas):
    def __init__(self, parent, taille):
        super().__init__(parent, width=taille, height=taille,
                         bg=COULEUR_PANNEAU, highlightthickness=0)
        self.taille = taille
        self.dernier = (None, 0, 0)
        self.dessiner(None, 0, 0)

    def redimensionner(self, taille):
        self.taille = taille
        self.config(width=taille, height=taille)
        self.dessiner(*self.dernier)

    def dessiner(self, provenance, vitesse_kmh, cap):
        self.dernier = (provenance, vitesse_kmh, cap)
        self.delete("all")
        t = self.taille
        k = t / 200                             # échelle des traits et du texte
        cx = cy = t / 2
        r = t / 2 - 16 * k
        self.create_oval(cx - r, cy - r, cx + r, cy + r, outline="gray60", width=3)
        # nord, placé selon le cap
        an = math.radians(-cap)
        self.create_text(cx + (r + 9 * k) * math.sin(an), cy - (r + 9 * k) * math.cos(an),
                         text="N", fill="gray70", font=("Arial", round(11 * k), "bold"))
        # petit planeur au centre, nez en haut
        s = r / 4
        self.create_line(cx - 1.6 * s, cy, cx + 1.6 * s, cy, fill="white", width=round(4 * k))
        self.create_line(cx, cy - s, cx, cy + 1.2 * s, fill="white", width=round(3 * k))
        self.create_line(cx - 0.5 * s, cy + 1.2 * s, cx + 0.5 * s, cy + 1.2 * s,
                         fill="white", width=round(3 * k))
        if provenance is None:
            self.create_text(cx, cy + r / 2, text="vent : calcul...",
                             fill="gray70", font=("Arial", round(10 * k)))
            return
        rel = math.radians(provenance - cap)  # direction d'où vient le vent, vue du planeur
        long_ = r * min(vitesse_kmh / VENT_MAX_KMH, 1.0)
        x0, y0 = cx + r * math.sin(rel), cy - r * math.cos(rel)         # bord du cadran
        x1, y1 = x0 - long_ * math.sin(rel), y0 + long_ * math.cos(rel)  # vers le centre
        self.create_line(x0, y0, x1, y1, fill="#ffd23f", width=round(6 * k),
                         arrow=tk.LAST, arrowshape=(16 * k, 18 * k, 7 * k))
        # direction et force, écrites dans le cadran (sous le petit planeur)
        self.create_text(cx, cy + r / 2, text=f"{provenance:03.0f}° / {vitesse_kmh:.0f} km/h",
                         fill="gray80", font=("Arial", round(12 * k)))


# =============================================================================
# Unité 7 : contenu du panneau (de haut en bas)
# =============================================================================
STYLE_BOUTON = dict(bg=COULEUR_BOUTON, fg=COULEUR_TEXTE, activebackground="#555555",
                    activeforeground=COULEUR_TEXTE, highlightthickness=0, relief="flat")

# --- 7a : commandes ---
cadre_cmd = tk.Frame(panneau, bg=COULEUR_PANNEAU)
cadre_cmd.pack(fill="x", pady=(0, 6))

def changer_fond(nom):
    url, zoom_max = FONDS[nom]
    if url is None:                         # "Relief Condor" : tuiles du serveur local
        paysage = globals().get("paysage_charge") or PAYSAGE_DEFAUT
        url = f"http://127.0.0.1:{PORT_TUILES}/{paysage}/{{z}}/{{x}}/{{y}}.png"
    carte.set_tile_server(url, max_zoom=zoom_max)
    if globals().get("tps_affiches"):
        dessiner_tps()                      # couleur des points de virage adaptée au fond

choix_fond = tk.StringVar(value=FOND_DEPART)
menu_fond = tk.OptionMenu(cadre_cmd, choix_fond, *FONDS, command=changer_fond)
menu_fond.config(**STYLE_BOUTON)
menu_fond["menu"].config(bg=COULEUR_BOUTON, fg=COULEUR_TEXTE)
menu_fond.pack(fill="x", pady=2)
changer_fond(FOND_DEPART)

bouton_fpl = tk.Button(cadre_cmd, text="Ouvrir .fpl...", **STYLE_BOUTON)
bouton_fpl.pack(fill="x", pady=2)


# --- 7b : heure de Condor (UDP "time", en heures décimales) ---
lbl_heure = tk.Label(panneau, text="--h--", font=("Arial", 18, "bold"), anchor="w",
                     bg=COULEUR_PANNEAU, fg=COULEUR_HEURE)
lbl_heure.pack(fill="x", pady=(0, 2))

# --- 7c : horizon et angles ---
TAILLE_HORIZON = max(170, min(330, TAILLE_HORIZON))
horizon = HorizonArtificiel(panneau, TAILLE_HORIZON)
horizon.pack(pady=(2, 0))
lbl_angles = tk.Label(panneau, text="Tang. — / Incl. —", font=("Arial", 10),
                      bg=COULEUR_PANNEAU, fg=COULEUR_TEXTE)
lbl_angles.pack(pady=(0, 6))                          # centré sous l'horizon

# --- 7d : finesse, encadrée ---
cadre_finesse = tk.Frame(panneau, bg=COULEUR_CADRE_FINESSE, padx=2, pady=2)
cadre_finesse.pack(anchor="w", pady=(0, 4))
lbl_finesse = tk.Label(cadre_finesse, text="Finesse : —", font=("Arial", 13), width=12,
                       anchor="w", bg=COULEUR_PANNEAU, fg=COULEUR_TEXTE)
lbl_finesse.pack()

# --- 7e : altitude (et messages d'erreur), instruments ---
info = etiquette("En attente de données Condor...")
# tableau des instruments (maquette de René) : titre en haut de chaque case,
# valeur en dessous ; vitesses en km/h, vario et netto en m/s
COULEUR_GRILLE = "gray40"
COULEUR_TITRE_CASE = "gray75"
tableau = tk.Frame(panneau, bg=COULEUR_GRILLE, padx=1, pady=1)
tableau.pack(fill="x", pady=(2, 6))
for _c in range(4):
    tableau.columnconfigure(_c, weight=1, uniform="case")

def case(ligne, colonne, titre, largeur=1):
    """Une case du tableau : titre (petit, gris) au-dessus de la valeur. Renvoie la valeur."""
    tk.Label(tableau, text=titre, font=("Arial", 10), bg=COULEUR_PANNEAU,
             fg=COULEUR_TITRE_CASE).grid(row=2 * ligne, column=colonne, columnspan=largeur,
                                         sticky="nsew", padx=(0, 1), pady=(0, 1))
    valeur = tk.Label(tableau, text="—" if titre else "", font=("Arial", 13, "bold"),
                      bg=COULEUR_PANNEAU, fg=COULEUR_TEXTE)
    valeur.grid(row=2 * ligne + 1, column=colonne, columnspan=largeur,
                sticky="nsew", padx=(0, 1), pady=(0, 1))
    return valeur

lbl_vitesse = case(0, 0, "V. air")
lbl_sol = case(0, 1, "V. sol")
lbl_vmoy = case(0, 2, "V. moy")         # vitesse moyenne sur la tâche
lbl_vne = case(0, 3, "VNE")
lbl_cap = case(1, 0, "Cap")             # cap : nez du planeur
lbl_route = case(1, 1, "Route")         # route : trajectoire sol
case(1, 2, "")                          # cases libres (pour plus tard)
case(1, 3, "")
lbl_vario = case(2, 0, "Vario")
lbl_netto = case(2, 1, "Netto")
lbl_volets = case(2, 2, "Volets", largeur=2)   # entre parenthèses : réglage conseillé

lbl_eau = etiquette("Eau : —")
# soulignement bleu proportionnel au remplissage du ballast (piste grise = plein)
LARGEUR_JAUGE_EAU = 140
jauge_eau = tk.Canvas(panneau, width=LARGEUR_JAUGE_EAU, height=5, bg=COULEUR_PANNEAU,
                      highlightthickness=0)
jauge_eau.pack(anchor="w", pady=(0, 2))

def dessiner_jauge_eau(eau, eau_max):
    jauge_eau.delete("all")
    if not eau_max:                         # capacité inconnue ou planeur sans ballast
        return
    jauge_eau.create_rectangle(0, 0, LARGEUR_JAUGE_EAU, 5, fill="#333333", outline="")
    plein = max(0.0, min(1.0, eau / eau_max))
    if plein > 0:
        jauge_eau.create_rectangle(0, 0, round(LARGEUR_JAUGE_EAU * plein), 5,
                                   fill=COULEUR_EAU, outline="")

# --- 7f : vent ---
indic_vent = IndicateurVent(panneau, round(TAILLE_HORIZON * RAPPORT_VENT))
indic_vent.pack(pady=(8, 0))

# --- 7g : coordonnées, puis tâche ---
lbl_coord = tk.Label(panneau, text="", font=("Arial", 10), anchor="w",
                     bg=COULEUR_PANNEAU, fg=COULEUR_TEXTE)
lbl_coord.pack(fill="x", pady=(6, 0))
lbl_tache = tk.Label(panneau, text="Tâche : aucune", font=("Arial", 10), anchor="w",
                     justify="left", bg=COULEUR_PANNEAU, fg=COULEUR_TEXTE)
lbl_tache.pack(fill="x", pady=(4, 0))

# --- 7h : zoom de la carte, en bas du panneau ---
ligne_zoom = tk.Frame(panneau, bg=COULEUR_PANNEAU)
ligne_zoom.pack(side="bottom", fill="x", pady=(4, 6))
STYLE_ZOOM = dict(STYLE_BOUTON, font=("Arial", 16, "bold"), width=3)

def zoomer(pas):
    carte.set_zoom(round(carte.zoom) + pas)

tk.Label(ligne_zoom, text="zoom\nscène", font=("Arial", 10), justify="center",
         bg=COULEUR_PANNEAU, fg=COULEUR_TEXTE).pack(side="left", padx=(6, 14))
tk.Button(ligne_zoom, text="−", command=lambda: zoomer(-1), **STYLE_ZOOM).pack(side="left", padx=(0, 6))
tk.Button(ligne_zoom, text="+", command=lambda: zoomer(+1), **STYLE_ZOOM).pack(side="left")

# --- recentrage automatique (remplace le bouton "Centrer") ---
dernier_geste = 0.0       # instant du dernier clic / glissé / molette sur la carte

def noter_geste(event=None):
    global dernier_geste
    dernier_geste = time.time()

for evenement in ("<ButtonPress-1>", "<B1-Motion>", "<MouseWheel>", "<Button-4>", "<Button-5>"):
    carte.canvas.bind(evenement, noter_geste, add="+")

def recentrer_si_besoin():
    """Toutes les 2 s : si le planeur s'approche du bord de la carte, on le recentre
    (sauf si l'on vient de déplacer ou zoomer la carte à la main)."""
    if (RECENTRAGE_AUTO and derniere_pos is not None
            and time.time() - dernier_geste > PAUSE_RECENTRAGE_S):
        x, y = decimal_to_osm(derniere_pos[0], derniere_pos[1], carte.zoom)
        (x0, y0), (x1, y1) = carte.upper_left_tile_pos, carte.lower_right_tile_pos
        if x1 != x0 and y1 != y0:
            rx, ry = (x - x0) / (x1 - x0), (y - y0) / (y1 - y0)   # 0..1 dans la carte
            z = ZONE_RECENTRAGE
            if not (z < rx < 1 - z and z < ry < 1 - z):
                carte.set_position(*derniere_pos)
    fenetre.after(2000, recentrer_si_besoin)


def degres_minutes(valeur):
    """46.9051 -> 46°54.3'"""
    d = int(abs(valeur))
    m = (abs(valeur) - d) * 60
    return f"{'-' if valeur < 0 else ''}{d}°{m:04.1f}'"


# =============================================================================
# Unité 8 : tâche du plan de vol (.fpl)
# =============================================================================
objets_tache = []
_icones_tp = {}           # une épingle par couleur
tps_affiches = []         # (lat, lon, nom, rayon) des points de virage de la tâche
objets_tps = []           # épingles et cercles dessinés (redessinés si le fond change)

def couleur_tp():
    """Couleur des points de virage selon le fond : complémentaire sur OpenTopoMap."""
    fond = choix_fond.get() if "choix_fond" in globals() else FOND_DEPART
    return COULEUR_TP_OPENTOPOMAP if fond.startswith("OpenTopoMap") else COULEUR_TP

def dessiner_tps():
    for obj in objets_tps:
        obj.delete()
    objets_tps.clear()
    c = couleur_tp()
    for lat, lon, nom, rayon in tps_affiches:
        if rayon > 0:
            objets_tps.append(carte.set_path(cercle(lat, lon, rayon), color=c, width=1))
        objets_tps.append(carte.set_marker(lat, lon, text=nom, icon=icone_tp(c),
                                           icon_anchor="s", font=("Tahoma", 9, "bold"),
                                           text_color=c))

def flanc_tp(signe, h, a, r, cx, cy):
    """Flanc de l'épingle (forme de René, TPdesign.py) : Bézier cubique raccordée
    tangentiellement au cercle de tête.  signe = -1 : flanc gauche, +1 : flanc droit."""
    p0 = (cx + signe * r * math.sin(a), cy + r * math.cos(a))   # raccord sur le cercle
    L = r * 0.65
    p1 = (p0[0] - signe * L * math.cos(a), p0[1] + L * math.sin(a))  # tangence au cercle
    p2 = (cx + signe * r * 0.04, h * 0.52)                      # courbure des flancs
    p3 = (cx, h)                                                # pointe
    points = []
    for i in range(101):
        t = i / 100
        u = 1 - t
        points.append((u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0],
                       u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1]))
    return points

def icone_tp(couleur=None):
    """Épingle des points de virage (forme de René) : tête ronde, raccord tangent sans
    décrochement, flancs concaves, longue pointe fine (la pointe marque la position)."""
    couleur = couleur or COULEUR_TP
    if couleur not in _icones_tp:
        k = 6                                           # dessin 6 fois plus grand, puis réduit
        h = HAUTEUR_TP * k
        r = h * 0.19                                    # rayon de la tête
        img = Image.new("RGBA", (round(2 * r + 2 * k), h + k), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        cx, cy = img.width / 2, k + r                   # centre de la tête
        a = math.radians(35)                            # angle de raccordement
        theta0 = math.pi / 2 - a                        # arc supérieur : raccord droit ->
        theta1 = -3 * math.pi / 2 + a                   #   sommet -> raccord gauche
        arc = [(cx + r * math.cos(theta0 + (theta1 - theta0) * i / 200),
                cy + r * math.sin(theta0 + (theta1 - theta0) * i / 200)) for i in range(201)]
        contour = arc + flanc_tp(-1, h, a, r, cx, cy) + flanc_tp(+1, h, a, r, cx, cy)[::-1]
        d.polygon(contour, fill=couleur)
        d.ellipse((cx - r * 0.42, cy - r * 0.42, cx + r * 0.42, cy + r * 0.42), fill="white")
        img = img.resize((img.width // k, img.height // k), Image.LANCZOS)
        _icones_tp[couleur] = ImageTk.PhotoImage(img)
    return _icones_tp[couleur]

def cercle(lat, lon, rayon_m, n=48):
    dlat = rayon_m / 111320.0
    dlon = rayon_m / (111320.0 * math.cos(math.radians(lat)))
    pts = [(lat + dlat * math.cos(2 * math.pi * k / n),
            lon + dlon * math.sin(2 * math.pi * k / n)) for k in range(n)]
    return pts + [pts[0]]

# --- vitesse moyenne sur la tâche ---
#   Convention Condor : point 0 = aérodrome de départ, point 1 = DÉPART, dernier = ARRIVÉE.
#   Départ : sortie de la zone du point 1 (y revenir avant le 1er virage = re-départ).
#   Point validé : entrée dans son rayon. Distance faite = branches validées + avancée
#   sur la branche en cours. Le temps est celui de Condor (la pause ne compte pas).
course = {}

def reinitialiser_course():
    course.clear()
    course.update(etape=1, dans_depart=False, t0=None, finie=False, vmoy=None)

reinitialiser_course()

def distance_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(a))

def heure_course():
    """Heure de Condor en secondes (repli : horloge du PC)."""
    if "time" in dernier_udp:
        return dernier_udp["time"] * 3600.0
    return time.time()

def suivre_course(lat, lon):
    tps = tps_affiches
    if len(tps) < 3 or course["finie"]:
        return
    def d(i):
        return distance_m(lat, lon, tps[i][0], tps[i][1])
    def rayon(i):
        return max(tps[i][3], 500.0)
    # départ (et re-départ, tant que le 1er point de virage n'est pas validé ;
    # ensuite, repasser au départ est normal : aller-retour, triangle...)
    if d(1) < rayon(1) and course["etape"] <= 2:
        if course["t0"] is not None:              # retour dans la zone : on repart à zéro
            reinitialiser_course()
            afficher_vmoy()
        course["dans_depart"] = True
        return
    if course["dans_depart"] and course["t0"] is None:
        course.update(t0=heure_course(), etape=2, dans_depart=False)
    if course["t0"] is None:
        return
    # points validés
    while course["etape"] < len(tps) and d(course["etape"]) < rayon(course["etape"]):
        course["etape"] += 1
    e = course["etape"]
    faite = sum(distance_m(tps[i][0], tps[i][1], tps[i + 1][0], tps[i + 1][1])
                for i in range(1, e - 1))
    if e >= len(tps):
        course["finie"] = True
    else:
        branche = distance_m(tps[e - 1][0], tps[e - 1][1], tps[e][0], tps[e][1])
        faite += max(0.0, branche - d(e))
    duree = heure_course() - course["t0"]
    if duree > 60:
        course["vmoy"] = faite / duree * 3.6
    afficher_vmoy()

def afficher_vmoy():
    if course["vmoy"] is None:
        lbl_vmoy.config(text="—", fg=COULEUR_TEXTE)
    else:
        # à l'arrivée, la moyenne se fige et passe en vert
        lbl_vmoy.config(text=f"{course['vmoy']:.0f}",
                        fg=COULEUR_VMOY_ARRIVEE if course["finie"] else COULEUR_TEXTE)

def afficher_tache(chemin, silencieux=False):
    """Lit et dessine la tâche. Renvoie True si tout s'est bien passé."""
    try:
        tache = Tache(chemin)
    except Exception as e:
        if not silencieux:
            messagebox.showerror("Plan de vol", f"Lecture impossible :\n{e}")
        elif str(e) != getattr(afficher_tache, "derniere_erreur", None):
            erreurs.put(f"Plan de vol illisible : {e}")      # signalé une seule fois
        afficher_tache.derniere_erreur = str(e)
        return False
    for obj in objets_tache:
        obj.delete()
    objets_tache.clear()
    pts = tache.points
    objets_tache.append(carte.set_path([(p.lat, p.lon) for p in pts],
                                       color=COULEUR_TACHE, width=EPAISSEUR_TACHE))
    tps_affiches[:] = [(p.lat, p.lon, f"{i} {p.nom}", p.rayon) for i, p in enumerate(pts)]
    reinitialiser_course()
    afficher_vmoy()
    dessiner_tps()
    lats = [p.lat for p in pts]
    lons = [p.lon for p in pts]
    carte.fit_bounding_box((max(lats) + 0.05, min(lons) - 0.05),
                           (min(lats) - 0.05, max(lons) + 0.05))
    fenetre.title(f"Copilote Condor - {os.path.basename(chemin)} - {tache.description}")
    charger_relief(tache.paysage)
    choisir_planeur(tache.planeur)
    lbl_tache.config(text=f"Scène  {tache.paysage}\nTâche : {len(pts)} points\n"
                          f"Vent prévu (.fpl) : {tache.vent_dir:.0f}° / {tache.vent_vitesse:.1f}\n"
                          f"Planeur : {tache.planeur}")
    return True

date_fpl_courant = None

def surveiller_fpl():
    """Recharge la tâche dès que Condor réécrit son plan de vol courant
    (nouveau vol solo, ou tâche téléchargée depuis un serveur en réseau)."""
    global date_fpl_courant
    if FPL_COURANT and os.path.exists(FPL_COURANT):
        date = os.path.getmtime(FPL_COURANT)
        if date != date_fpl_courant:
            if afficher_tache(FPL_COURANT, silencieux=True):   # sinon : on réessaiera
                date_fpl_courant = date
    fenetre.after(int(SURVEILLANCE_FPL_S * 1000), surveiller_fpl)

def ouvrir_fpl():
    chemin = filedialog.askopenfilename(
        title="Choisir un plan de vol Condor",
        initialdir=DOSSIER_FPL if os.path.isdir(DOSSIER_FPL) else os.getcwd(),
        filetypes=[("Plans de vol Condor", "*.fpl"), ("Tous les fichiers", "*.*")])
    if chemin:
        afficher_tache(chemin)

bouton_fpl.config(command=ouvrir_fpl)


# =============================================================================
# Unité 9 : icône du planeur orientée selon le cap (72 images, une tous les 5°)
# =============================================================================
def preparer_icones():
    chemin = os.path.join(os.path.dirname(os.path.abspath(__file__)), ICONE_PLANEUR)
    if not os.path.exists(chemin):
        return None
    img = Image.open(chemin).convert("RGBA")
    h = round(img.height * LARGEUR_ICONE / img.width)
    img = img.resize((LARGEUR_ICONE, h), Image.LANCZOS)
    # PIL tourne dans le sens inverse des aiguilles d'une montre : d'où le signe moins
    return [ImageTk.PhotoImage(img.rotate(-5 * k, resample=Image.BICUBIC, expand=True))
            for k in range(72)]

icones = preparer_icones()


# =============================================================================
# Unité 10 : mise à jour périodique de l'affichage (10 fois par seconde)
# =============================================================================
trace = []
marqueur = None
ligne_trace = None
indice_icone = None
dernier_udp = {}          # dernières valeurs UDP reçues
derniere_alt = 0.0
vent = CalculVent()
derniere_pos = None       # (lat, lon) la plus récente
derniere_route = None     # route sol (NMEA), degrés


# =============================================================================
# Unité 11 : zone atteignable en plané ("phares"), recalculée toutes les 4 s
# =============================================================================
relief = None             # relief du paysage (condor_relief.Relief)
paysage_charge = None
trace_phare = None        # contour de l'éventail sur la carte
traces_bord = []          # liseré sous le contour

def charger_relief(paysage):
    global relief, paysage_charge
    if paysage == paysage_charge:
        return
    chemin = os.path.join(CHEMIN_PAYSAGES, paysage, paysage + ".trn")
    try:
        relief = Relief(chemin)
        paysage_charge = paysage
        if FONDS[choix_fond.get()][0] is None:   # fond "Relief Condor" : nouveau paysage
            changer_fond(choix_fond.get())
    except Exception as e:
        relief = None
        erreurs.put(f"Relief : lecture impossible de {chemin} ({e})")

resultat_phare = queue.Queue()   # contours calculés par le thread de calcul
calcul_en_cours = threading.Event()

# --- finesse réelle, mesurée sur les FENETRE_FINESSE_S dernières secondes ---
#   finesse = vitesse air vraie / taux de chute "énergie totale" (evario de Condor :
#   une ressource ou un piqué ne faussent pas la mesure). Comme le taux de chute
#   comprend les mouvements de l'air traversé, c'est la finesse obtenue dans l'air
#   du moment (dégueulante : plus faible ; bonne masse d'air : plus forte).
mesures_finesse = []      # (instant, vitesse vraie m/s, evario m/s, inclinaison degrés)
finesse_estimee = None    # inconnue tant qu'on n'a pas plané quelques secondes en ligne droite
vitesse_estimee_kmh = None

def enregistrer_mesure(udp):
    tas = CalculVent.vitesse_vraie(udp.get("airspeed", 0.0), udp.get("altitude", 0.0))
    mesures_finesse.append((time.time(), tas, udp.get("evario", udp.get("vario", 0.0)),
                            abs(math.degrees(udp.get("bank", 0.0)))))
    limite = time.time() - FENETRE_FINESSE_S
    while mesures_finesse and mesures_finesse[0][0] < limite:
        mesures_finesse.pop(0)

def estimer_finesse():
    """Met à jour finesse_estimee ; garde l'ancienne valeur si la mesure n'est pas fiable."""
    global finesse_estimee, vitesse_estimee_kmh
    if len(mesures_finesse) < 5:
        return
    tas = sum(m[1] for m in mesures_finesse) / len(mesures_finesse)
    chute = -sum(m[2] for m in mesures_finesse) / len(mesures_finesse)
    incl = max(m[3] for m in mesures_finesse)
    if tas < 15 or incl > INCLINAISON_MAX_MESURE:
        return                                  # au sol, en virage ou en spirale
    vitesse_estimee_kmh = tas * 3.6
    if chute <= tas / FINESSE_MAX:              # air porteur ou montée : plafond
        finesse_estimee = FINESSE_MAX
    else:
        finesse_estimee = max(FINESSE_MIN, tas / chute)

def calculer_phare(lat, lon, alt, axe, vn, ve, finesse, vitesse_kmh):
    """Tourne dans un thread : le calcul "vallées" peut prendre ~1 s en plaine."""
    try:
        fonction = zone_atteignable_vallees if MODE_PHARE == "vallees" else zone_atteignable
        contour = fonction(relief, lat, lon, alt, axe, vn, ve, finesse, vitesse_kmh,
                           MARGE_HAUTEUR, OUVERTURE_PHARE, 3.0, DISTANCE_MAX_KM)
        resultat_phare.put(contour)
    except Exception as e:
        erreurs.put(f"Calcul de portée : {e}")
    finally:
        calcul_en_cours.clear()

def mettre_a_jour_phare():
    """Toutes les PERIODE_PORTEE_S secondes : lance un calcul s'il n'y en a pas déjà un."""
    estimer_finesse()
    if finesse_estimee is not None:
        lbl_finesse.config(text=f"Finesse : {finesse_estimee:.0f}")
    if (relief is not None and derniere_pos is not None and dernier_udp
            and finesse_estimee is not None and not calcul_en_cours.is_set()):
        if AXE_PHARE == "route" and derniere_route is not None:
            axe = derniere_route
        else:
            axe = dernier_udp.get("compass", 0.0)
        vn, ve = (vent.n, vent.e) if (PORTEE_AVEC_VENT and vent.pret) else (0.0, 0.0)
        calcul_en_cours.set()
        threading.Thread(target=calculer_phare, daemon=True,
                         args=(derniere_pos[0], derniere_pos[1], derniere_alt, axe, vn, ve,
                               finesse_estimee, vitesse_estimee_kmh)).start()
    fenetre.after(int(PERIODE_PORTEE_S * 1000), mettre_a_jour_phare)

def dessiner_phare():
    """Appelé par mise_a_jour : affiche le dernier contour calculé."""
    global trace_phare
    contour = None
    while not resultat_phare.empty():
        contour = resultat_phare.get()
    if contour is None:
        return
    if trace_phare is None:
        if COULEUR_BORD_PORTEE:                     # liseré dessiné d'abord, donc dessous
            trace_phare_bord = carte.set_path(contour, color=COULEUR_BORD_PORTEE,
                                              width=EPAISSEUR_PORTEE + 2 * LARGEUR_BORD_PORTEE)
            traces_bord.append(trace_phare_bord)
        trace_phare = carte.set_path(contour, color=COULEUR_PORTEE, width=EPAISSEUR_PORTEE)
    else:
        for t in traces_bord:
            t.set_position_list(contour)
        trace_phare.set_position_list(contour)
    if getattr(marqueur, "canvas_icon", None):
        carte.canvas.tag_raise(marqueur.canvas_icon)       # planeur au-dessus du tracé


# --- fiches planeurs (VNE, volets) ---
try:
    planeurs = Planeurs(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     FICHIER_PLANEURS))
except OSError:
    planeurs = None
planeur_actuel = None     # fiche CoTASA (volets), ou None
vne_actuelle = None       # VNE du planeur du plan de vol (km/h), ou None
eau_max_actuelle = None   # capacité de ballast (L), ou None

planeur_nom = None

def choisir_planeur(nom):
    global planeur_actuel, vne_actuelle, eau_max_actuelle, planeur_nom, vne_proposee
    planeur_nom = nom
    planeur_actuel = planeurs.get(nom) if planeurs else None
    fiche = fiche_condor(CHEMIN_PLANES, nom)
    if fiche and fiche.get("eau_max"):
        eau_max_actuelle = fiche["eau_max"]
    elif planeur_actuel:
        eau_max_actuelle = planeur_actuel.ballast_max
    else:
        eau_max_actuelle = None
    if fiche:
        vne_actuelle = fiche["vne"]               # la fiche de Condor fait foi
    elif planeur_actuel:
        vne_actuelle = planeur_actuel.vne         # sinon, celle de CoTASA
    else:
        vne_actuelle = None
    vne_proposee = vne_actuelle is None
    if vne_proposee:                              # inconnue : valeur retenue, sinon défaut
        vne_actuelle = lire_vne_perso().get(nom, VNE_DEFAUT)
    afficher_vne()

# --- VNE proposée quand elle est inconnue (seul cas où elle se modifie) ---
vne_proposee = False

def chemin_vne_perso():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), FICHIER_VNE_PERSO)

def lire_vne_perso():
    """Fichier texte "nom du planeur = VNE", une ligne par planeur."""
    vne = {}
    try:
        with open(chemin_vne_perso(), encoding="utf-8") as f:
            for ligne in f:
                if "=" in ligne:
                    nom, valeur = ligne.rsplit("=", 1)
                    vne[nom.strip()] = float(valeur)
    except (OSError, ValueError):
        pass
    return vne

def ecrire_vne_perso(nom, valeur):
    vne = lire_vne_perso()
    vne[nom] = valeur
    try:
        with open(chemin_vne_perso(), "w", encoding="utf-8") as f:
            for n, v in sorted(vne.items()):
                f.write(f"{n} = {v:.0f}\n")
    except OSError as e:
        erreurs.put(f"VNE non enregistrée : {e}")

def afficher_vne():
    if vne_actuelle is None:
        lbl_vne.config(text="—", fg=COULEUR_TEXTE, cursor="")
    elif vne_proposee:
        lbl_vne.config(text=f"{vne_actuelle:.0f}?", fg=COULEUR_VNE_PROPOSEE, cursor="hand2")
    else:
        lbl_vne.config(text=f"{vne_actuelle:.0f}", fg=COULEUR_TEXTE, cursor="")

def modifier_vne(event=None):
    global vne_actuelle
    if not vne_proposee:                          # VNE connue : pas de modification
        return
    nom = planeur_nom or "planeur inconnu"
    valeur = simpledialog.askinteger(
        "VNE inconnue", f"VNE de « {nom} » (km/h) :\n(valeur retenue pour ce planeur)",
        initialvalue=round(vne_actuelle), minvalue=100, maxvalue=400, parent=fenetre)
    if valeur:
        vne_actuelle = float(valeur)
        if planeur_nom:
            ecrire_vne_perso(planeur_nom, vne_actuelle)
        afficher_vne()

lbl_vne.bind("<Button-1>", modifier_vne)

def maj_cap_route():
    cap_txt = f"{dernier_udp['compass']:03.0f}°" if "compass" in dernier_udp else "—"
    route_txt = f"{derniere_route:03.0f}°" if derniere_route is not None else "—"
    lbl_cap.config(text=cap_txt)
    lbl_route.config(text=route_txt)

# --- raccourcis clavier globaux (Condor garde la main) ---
commandes_clavier = queue.Queue()     # le thread du clavier dépose, l'affichage exécute

def demarrer_raccourcis():
    if not RACCOURCIS_GLOBAUX:
        return
    try:
        import keyboard
    except ImportError:
        erreurs.put("Raccourcis globaux : faire  pip install keyboard")
        return
    for touche, action in ((TOUCHE_ZOOM_PLUS, "zoom+"), (TOUCHE_ZOOM_MOINS, "zoom-"),
                           (TOUCHE_RECENTRER, "centrer"), (TOUCHE_FOND_SUIVANT, "fond")):
        if not touche:
            continue
        try:
            # suppress=True : la combinaison est "mangée", Condor ne la reçoit pas
            keyboard.add_hotkey(touche, commandes_clavier.put, args=(action,), suppress=True)
        except Exception as e:
            erreurs.put(f"Raccourci « {touche} » refusé ({e})")

def executer_commandes_clavier():
    while not commandes_clavier.empty():
        action = commandes_clavier.get()
        if action == "zoom+":
            zoomer(+1)
        elif action == "zoom-":
            zoomer(-1)
        elif action == "centrer" and derniere_pos is not None:
            carte.set_position(*derniere_pos)
        elif action == "fond":
            noms = list(FONDS)
            suivant = noms[(noms.index(choix_fond.get()) + 1) % len(noms)]
            choix_fond.set(suivant)
            changer_fond(suivant)

def mise_a_jour():
    global marqueur, ligne_trace, indice_icone, dernier_udp, derniere_alt, derniere_pos
    global derniere_route

    while not erreurs.empty():
        info.config(text=erreurs.get(), fg=COULEUR_ERREUR)

    # --- instruments (UDP) : seul le dernier paquet compte ---
    # Condor peut répartir ses données sur plusieurs paquets (données "étendues"
    # envoyées à part) : on les fusionne, chaque valeur gardant sa dernière mise à jour.
    nouveau = None
    while not instruments.empty():
        dernier_udp = {**dernier_udp, **instruments.get()}
        nouveau = dernier_udp
    if nouveau:
        enregistrer_mesure(nouveau)
        tangage = math.degrees(nouveau.get("pitch", 0.0)) * SENS_TANGAGE
        inclinaison = math.degrees(nouveau.get("bank", 0.0)) * SENS_INCLINAISON
        horizon.dessiner(tangage, inclinaison)
        lbl_vitesse.config(text=f"{nouveau.get('airspeed', 0) * 3.6:.0f}")
        lbl_vario.config(text=f"{nouveau.get('vario', 0):+.1f}")
        if "nettovario" in nouveau:          # mouvement de l'air seul (montante/descendante)
            lbl_netto.config(text=f"{nouveau['nettovario']:+.1f}")
        eau = f"{nouveau['water']:.0f} L" if "water" in nouveau else "—"
        volets = f"{nouveau['flaps']:+.0f}" if "flaps" in nouveau else "—"
        if planeur_actuel and planeur_actuel.volets:      # volets conseillés (tableau CoTASA)
            conseil = planeur_actuel.volet_conseille(nouveau.get("airspeed", 0) * 3.6,
                                                     nouveau.get("water", 0.0))
            volets += f" ({conseil})"
        lbl_volets.config(text=volets)
        lbl_eau.config(text=f"Eau : {eau}")
        dessiner_jauge_eau(nouveau.get("water", 0.0), eau_max_actuelle)
        if "time" in nouveau:                              # heure de Condor
            minutes = int((nouveau["time"] % 24) * 60)
            lbl_heure.config(text=f"{minutes // 60}h{minutes % 60:02d}")
        # alarme VNE, sur la vitesse VRAIE (comme CoTASA) : la VNE baisse en altitude
        couleur = COULEUR_TEXTE
        if vne_actuelle:
            tas_kmh = CalculVent.vitesse_vraie(nouveau.get("airspeed", 0.0),
                                               nouveau.get("altitude", 0.0)) * 3.6
            if tas_kmh >= vne_actuelle:
                couleur = COULEUR_VNE_DEPASSEE
            elif tas_kmh >= SEUIL_VNE * vne_actuelle:
                couleur = COULEUR_VNE_PROCHE
        lbl_vitesse.config(fg=couleur)
        maj_cap_route()
        lbl_angles.config(text=f"Tang. {tangage:+.0f}°  /  Incl. {inclinaison:+.0f}°")

    cap = dernier_udp.get("compass", 0.0)

    # --- position (NMEA $GPGGA) ---
    while not positions.empty():
        lat, lon, alt = positions.get()
        derniere_alt = alt
        trace.append((lat, lon))
        if len(trace) >= 2:
            if ligne_trace is None:
                ligne_trace = carte.set_path(trace, color=COULEUR_TRACE, width=EPAISSEUR_TRACE)
            else:
                ligne_trace.set_position_list(trace)
        if marqueur is None:
            carte.set_position(lat, lon)
            if icones:
                indice_icone = round(cap / 5) % 72
                marqueur = carte.set_marker(lat, lon, icon=icones[indice_icone])
            else:
                marqueur = carte.set_marker(lat, lon, text="Planeur")
        else:
            marqueur.set_position(lat, lon)
        if getattr(marqueur, "canvas_icon", None):
            carte.canvas.tag_raise(marqueur.canvas_icon)   # icône au-dessus de la trace
        derniere_pos = (lat, lon)
        suivre_course(lat, lon)
        # hauteur au-dessus du sol : donnée par Condor (UDP "height"), sinon calculée
        # avec le relief du paysage
        if "height" in dernier_udp:
            hauteur = dernier_udp["height"]
        elif relief is not None:
            hauteur = alt - relief.altitude(lat, lon)
        else:
            hauteur = None
        texte = f"Alt : {alt:.0f} m"
        couleur = COULEUR_TEXTE
        if hauteur is not None:
            texte += f"   Sol : {hauteur:.0f} m"
            if hauteur < MARGE_HAUTEUR:              # sous la garde de sécurité : alerte
                couleur = COULEUR_ALERTE_SOL
        info.config(text=texte, fg=couleur)
        lbl_coord.config(text=f"Lat : {degres_minutes(lat)}   Lon : {degres_minutes(lon)}")

    dessiner_phare()

    # orientation de l'icône
    if marqueur is not None and icones:
        k = round(cap / 5) % 72
        if k != indice_icone:
            marqueur.change_icon(icones[k])
            indice_icone = k

    # --- vitesse sol (NMEA $GPRMC) et vent ---
    while not sol.empty():
        v_sol, route = sol.get()
        derniere_route = route
        lbl_sol.config(text=f"{v_sol * 3.6:.0f}")
        maj_cap_route()
        if dernier_udp:
            vent.ajouter(v_sol, route, dernier_udp.get("airspeed", 0.0), cap,
                         dernier_udp.get("altitude", derniere_alt))
    if vent.pret:
        v = vent.vitesse_kmh()
        prov = vent.provenance()
        indic_vent.dessiner(prov, v, cap)
    else:
        indic_vent.dessiner(None, 0, cap)

    executer_commandes_clavier()
    fenetre.after(100, mise_a_jour)


# =============================================================================
# Démarrage
# =============================================================================
for message in problemes_reglages:
    erreurs.put(message)
charger_relief(PAYSAGE_DEFAUT)
if len(sys.argv) > 1:                        # python copilote_condor.py "tache.fpl"
    afficher_tache(sys.argv[1])
else:
    surveiller_fpl()                         # plan de vol courant de Condor

threading.Thread(target=lecteur_nmea, daemon=True).start()
threading.Thread(target=lecteur_udp, daemon=True).start()
demarrer_raccourcis()
mise_a_jour()
mettre_a_jour_phare()
recentrer_si_besoin()
fenetre.mainloop()
