# lancer_copilote.ps1 - Lance le Copilote Condor depuis son propre dossier
# Raccourci possible (cible) :
#   powershell.exe -ExecutionPolicy Bypass -File "<dossier du copilote>\lancer_copilote.ps1"
Set-Location $PSScriptRoot
python copilote_condor.py
