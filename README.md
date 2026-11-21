# Copilote Condor

Un copilote pour **Condor 3**, le simulateur de vol à voile : une carte en direct à côté du simulateur, avec la tâche, la zone atteignable et les instruments essentiels, sur un panneau « tableau noir » facile à lire.

Écrit en Python pour Windows, par un pilote de Condor, pour le plaisir.

## Ce qu'il fait

- **Carte en direct** : position, cap et trace du planeur. Quatre fonds de carte au choix : OpenTopoMap, OpenStreetMap, Satellite, et **Relief Condor**, peint à partir du relief, des lacs et des forêts de la scène Condor elle-même.
- **Tâche du plan de vol** : points de virage, rayons et branches, rechargés automatiquement quand Condor écrit un nouveau plan de vol (vol solo ou tâche téléchargée depuis un serveur). Toutes les scènes Condor sont reconnues.
- **Zone atteignable** : un éventail en « phares », recalculé toutes les 4 secondes, qui tient compte du relief, du vent et d'une marge de sécurité au-dessus du sol.
- **Finesse mesurée en vol**, sur les 4 dernières secondes, sans avoir besoin de la polaire du planeur.
- **Instruments** : horizon artificiel, vitesse air (avec alarme VNE), vitesse sol, vitesse moyenne sur la tâche, cap et route, vario, netto, volets (avec réglage conseillé si disponible), ballast, altitude et hauteur sol, vent calculé.
- **Raccourcis clavier globaux**, qui fonctionnent même quand Condor est au premier plan :

| Raccourci | Action |
|---|---|
| Alt + W | Zoom avant |
| Alt + D | Zoom arrière |
| Alt + C | Centrer sur le planeur |
| Alt + S | Fond de carte suivant |

## Ce qu'il faut

- Windows et **Condor 3**.
- **Python 3.10 ou plus récent** (python.org ; cochez « Add Python to PATH » à l'installation).
- Une **paire de ports série virtuels** (par exemple avec VSPE, Virtual Serial Port Emulator) :
  - Condor écrit sa sortie NMEA sur un port, le copilote lit l'autre. Par défaut : Condor sur **COM10**, copilote sur **COM11**.
  - Une configuration VSPE toute prête est fournie dans le dépôt : `outils/Pairing_com10_11.vspe`. Dans VSPE, ouvrez-la (File › Open), puis démarrez l'émulation.

## Installation

1. Téléchargez le dépôt (bouton vert **Code**, puis **Download ZIP**) et décompressez-le dans un dossier de votre choix.
2. Ouvrez un terminal PowerShell dans ce dossier et installez les bibliothèques (liste dans `requirements.txt`) :
   ```
   pip install -r requirements.txt
   ```
3. Copiez `copilote_condor.exemple.ini` sous le nom **`copilote_condor.ini`**, puis adaptez les chemins à votre PC, au minimum :
   - `chemin_paysages` et `chemin_planes` : les dossiers `Landscapes` et `Planes` de votre installation de Condor ;
   - `fpl_courant` : le fichier `Flightplan.fpl` de votre pilote (dans `Documents\Condor3\Pilots\<votre pilote>\`) ;
   - `port_com` : le port lu par le copilote (COM11 par défaut).

## Réglages de Condor

1. **Sortie NMEA** : dans les options de Condor, activez la sortie NMEA sur le port de la paire virtuelle (COM10 par défaut).
2. **Sortie UDP** : dans le fichier `Settings\UDP.ini` de Condor (faites-en une copie avant de le modifier), vérifiez ces lignes :
   ```
   Enabled=1
   Host=127.0.0.1
   Port=55278
   ExtendedData1=1
   ```
   `ExtendedData1=1` est nécessaire pour afficher le ballast et les volets.

## Lancement

Créez la paire de ports virtuels, lancez Condor, puis le copilote :

```
python copilote_condor.py
```

ou avec `lancer_copilote.ps1`, que vous pouvez associer à un raccourci sur le bureau.

Tous les réglages (couleurs, tailles, marge de sécurité, raccourcis, position de la fenêtre…) sont commentés dans `copilote_condor.ini`.

## Volets conseillés (facultatif)

Le copilote peut afficher le réglage de volets conseillé si le fichier `CoTASA_data.txt` du logiciel CoTASA se trouve dans son dossier. Ce fichier n'est pas fourni ici : sans lui, tout fonctionne, seul le conseil de volets disparaît.

## Outil de diagnostic

`outils/diag_udp.py` liste pendant 15 secondes toutes les valeurs que Condor envoie en UDP. Lancez-le pendant un vol, copilote fermé : un seul programme à la fois peut écouter le port.

## Remerciements

- Fonds de carte : © les contributeurs d'OpenStreetMap, OpenTopoMap (CC-BY-SA), imagerie satellite Esri.
- Carte : bibliothèque [tkintermapview](https://github.com/TomSchimansky/TkinterMapView).
- Réalisé avec l'aide de Claude (Anthropic).

## Licence

MIT : voir le fichier `LICENSE`. Utilisation libre, sans garantie. Condor est une marque de ses auteurs ; ce projet n'y est pas affilié.
