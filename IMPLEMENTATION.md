# 🤖 KODA - Implémentation Raspberry Pi

## 📅 Version actuelle
- **Date** : 18 Mai 2026
- **État** : Foundation phase — Hardware check system
- **Plateforme** : Raspberry Pi 4 Model B (Debian-based Linux)

---

## 🏗️ Architecture retenue

### Principes fondamentaux

1. **Découplage strict** : chaque périphérique = un adapter + un service
2. **Async-first** : utilisation systématique de `asyncio` pour ne pas bloquer
3. **Event-driven** : bus d'événements central (`EventBus`)
4. **Hardware-first validation** : checks rigoureux au boot
5. **Environment-based config** : variables `.env` pour tout matériel spécifique
6. **Stateless services** : logique métier indépendante du matériel

### Paradigme choisi

```
┌─────────────────────────────────────────┐
│      main.py (Point d'entrée)           │
├─────────────────────────────────────────┤
│  1. Charger config (app/config.py)      │
│  2. Init logger (utils/logger.py)       │
│  3. Lancer HW checks (hardware_check/)  │
│  4. Si OK → lancer services             │
│  5. Event loop principal                │
└─────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│    Core Layer (logique métier)          │
│  - EventBus                             │
│  - RobotState (futur)                   │
│  - Personality (futur)                  │
└─────────────────────────────────────────┘
         │
    ┌────┼────┬──────┬────────┬──────────┐
    ▼    ▼    ▼      ▼        ▼          ▼
  Audio Video Motion Display  Speech    Backend
  Service Service Service Service Service Service
    │    │    │      │        │          │
    └────┼────┴──────┴────────┴──────────┘
         │
    ┌────▼────────────────────────────────┐
    │  Adapters (Interface matériel)      │
    ├────────────────────────────────────┤
    │ - ReSpeaker  - Camera  - Arduino   │
    │ - Nextion    - Audio   - HTTP      │
    └────────────────────────────────────┘
```

---

## 📁 Structure du projet

```
codeRaspberry/
├── README.md                          # Vue d'ensemble projet
├── ARCHITECTURE_DIAGRAMS.md           # Diagrammes Mermaid
├── IMPLEMENTATION.md                  # Ce fichier
├── guide.txt                          # Quick start + env vars
├── requirements.txt                   # Dépendances Python
│
├── app/                               # Point d'entrée
│   ├── __init__.py
│   ├── main.py                        # Startup orchestration
│   └── config.py                      # Config loader
│
├── core/                              # Logique métier
│   ├── __init__.py
│   └── event_bus.py                   # Simple event bus
│
├── services/                          # Services haut-niveau
│   ├── __init__.py
│   ├── audio/                         # Audio input/output
│   │   ├── __init__.py
│   │   ├── audio_service.py
│   │   ├── wake_word_detector.py      # (futur)
│   │   └── audio_recorder.py          # (futur)
│   ├── vision/                        # Caméra
│   │   └── vision_service.py          # (futur)
│   ├── motion/                        # Contrôle moteurs
│   │   └── motion_service.py          # (futur)
│   ├── display/                       # Nextion screen
│   │   └── display_service.py         # (futur)
│   ├── speech/                        # TTS
│   │   └── speech_service.py          # (futur)
│   ├── backend/                       # Comms avec backend Python
│   │   └── backend_service.py         # (futur)
│   ├── arduino/                       # Bluetooth Arduino
│   │   └── arduino_service.py         # (futur)
│   └── hardware_check/                # ✅ Implémenté
│       ├── __init__.py
│       ├── hardware_check_service.py
│       └── checks/
│           ├── __init__.py
│           ├── mic_check.py           # ReSpeaker (arecord -l)
│           ├── camera_check.py        # Camera (/dev/video*)
│           ├── nextion_check.py       # Display (/dev/serial0)
│           ├── bluetooth_check.py     # HC-05 (/dev/rfcomm0)
│           ├── audio_check.py         # Audio out (ALSA card index)
│           └── system_check.py        # Info système
│
├── adapters/                          # Interface matériel (futur)
│   ├── __init__.py
│   ├── respeaker_adapter.py
│   ├── camera_adapter.py
│   ├── nextion_adapter.py
│   ├── bluetooth_adapter.py
│   ├── audio_output_adapter.py
│   └── backend_client.py
│
├── config/                            # Config YAML (futur)
│   └── config.yaml
│
├── utils/                             # Utilitaires
│   ├── __init__.py
│   ├── logger.py                      # ✅ Logging structuré
│   ├── decorators.py                  # (futur)
│   └── helpers.py                     # (futur)
│
└── tests/                             # Tests unitaires
    ├── __init__.py
    ├── test_hardware_check.py         # ✅ Tests checks
    └── mocks/                         # Mocks pour dev sans hardware
```

---

## ✅ Fonctionnalités implémentées

### 1. **Startup orchestration** (`app/main.py`)

```python
async def main():
    # 1. Charger config
    config = load_config()
    
    # 2. Lancer hardware checks (parallèles)
    statuses = await run_full_check()
    
    # 3. Rapport lisible par composant
    _print_hardware_report(statuses)
    
    # 4. Décider mode démarrage
    if all_ok:
        logger.info("All checks passed")
    else:
        logger.warning("Degraded mode (missing hardware)")
    
    # 5. (Futur) Init services et event loop
```

**Sortie console** :
```
Hardware detection report:
 - Microphone (ReSpeaker)    | DETECTED     | Capture device(s) detected
 - Camera                    | NOT DETECTED | No /dev/video* devices found
 - Nextion Display          | DETECTED     | Nextion port available: /dev/serial0
 - Bluetooth HC-05          | DETECTED     | HC-05 RFCOMM port detected: /dev/rfcomm0
 - Audio Output             | DETECTED     | Configured ALSA card index detected
 - System                   | DETECTED     | System: Linux-6.12.75+rpt-rpi-v8
Detected components: 5/6
```

### 2. **Hardware checks asynchrones et fiables**

#### `mic_check.py`
- Exécute `arecord -l`
- Détecte cartes audio d'entrée
- **Pas de faux positif** : requiert vraiment du matériel

#### `camera_check.py`
- Cherche `/dev/video0`, `/dev/video1`
- Fichier-système simple (rapide, non-bloquant)

#### `nextion_check.py` ⭐
- Port par défaut : `/dev/serial0` (GPIO RX/TX pins 8/10)
- Valide résolution symlink → `/dev/ttyAMA0` ou `/dev/ttyS0`
- **Règle** : `DETECTED` seulement si port GPIO existe
- Variable env : `NEXTION_PORT` + `NEXTION_REQUIRE_GPIO_UART`

#### `bluetooth_check.py` ⭐
- **Logique principale** : cherche `/dev/rfcomm0`
- Prerequis : `sudo rfcomm bind 0 00:22:12:02:35:16`
- **Règle** : `DETECTED` uniquement si port série RFCOMM existe
- Fallback : `bluetoothctl info` si rfcomm absent (informatif uniquement)
- Validation MAC format : `XX:XX:XX:XX:XX:XX`
- Variable env : `HC05_MAC` + `HC05_RFCOMM_DEVICE`

#### `audio_check.py` ⭐
- Exécute `aplay -l` pour lister cartes de sortie
- Parse pattern : `card <index>: <short> [<label>]`
- **Strict matching** par index ou label ALSA
- `PAM8403` détectable via carte audio USB présente
- Variable env : `AUDIO_CARD_INDEX` (ex: `1`) + optionnel `AUDIO_CARD_LABEL`

#### `system_check.py`
- Platform info simple (`python3 -c "import platform"`)
- Confirme que le Pi tourne et Python marche

### 3. **EventBus simple**

```python
class EventBus:
    def subscribe(event_name: str, handler: Callable):
        # Handler appelé quand event_name publié
    
    def publish(event_name: str, payload):
        # Exécute tous les handlers subscribés
```

**Usage futur** :
```python
bus.subscribe("wake_word_detected", on_user_speaks)
bus.publish("question_recorded", audio_buffer)
```

### 4. **Logging structuré**

```python
logger = get_logger(__name__)
logger.info("Startup message")
logger.warning("Missing hardware")
logger.exception("Error occurred", exc_info=e)
```

Format :
```
2026-05-18 10:12:34,567 INFO [app.main] Koda Raspberry startup
```

---

## 🔧 Configuration d'exécution

### Variables d'environnement requises

**Avant de lancer :**

```bash
# Créer venv et installer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Bind RFCOMM (une seule fois, persiste)
sudo rfcomm bind 0 00:22:12:02:35:16

# Exporter variables
export NEXTION_PORT=/dev/serial0
export NEXTION_REQUIRE_GPIO_UART=1
export HC05_MAC=00:22:12:02:35:16
export HC05_RFCOMM_DEVICE=/dev/rfcomm0
export AUDIO_CARD_INDEX=1

# Lancer
python -m app.main
```

### Profiler l'audio ALSA

```bash
# Voir toutes les cartes audio
aplay -l

# Exemple résultat :
# **** List of PLAYBACK Hardware Devices ****
# card 0: Headphones [bcm2835 Headphones], device 0: bcm2835 Headphones [bcm2835 Headphones]
# card 1: Device [USB Audio Device], device 0: USB Audio [USB Audio]
#
# → AUDIO_CARD_INDEX=1
```

### Profiler Bluetooth HC-05

```bash
# Lister appareils appairés
bluetoothctl paired-devices

# Afficher infos MAC cible
bluetoothctl info 00:22:12:02:35:16

# Bind RFCOMM (requires admin)
sudo rfcomm bind 0 00:22:12:02:35:16

# Vérifier port créé
ls -l /dev/rfcomm0

# Débind (optionnel)
sudo rfcomm release 0
```

---

## 🎯 Flux d'exécution actuel

### 1. Boot du Koda

```
user$ python -m app.main
│
├─ Config chargée
├─ Logger initialisé
│
├─ Hardware checks lancés (parallellelism)
│  ├─ mic_check → arecord -l
│  ├─ camera_check → /dev/video*
│  ├─ nextion_check → /dev/serial0
│  ├─ bluetooth_check → /dev/rfcomm0
│  ├─ audio_check → aplay -l (ALSA card)
│  └─ system_check → platform info
│
└─ Rapport affiché
   └─ Compte des détections (X/6)
   └─ Mode dégradé si besoin
```

### 2. Décisions de démarrage (futur)

```
Si OK (tous composants détectés)
 → Lancer tous les services
 → Démarrer event loop principal
 → Prêt pour utilisation

Si dégradé (quelques composants manquants)
 → Lancer services compatibles uniquement
 → Désactiver requête pour matériel absent
 → Log avertissements

Si critique (composant indispensable absent)
 → Stop ou mode lecture seule
```

---

## 🔐 Sécurité et validation

### Validation stricte

1. **MAC Bluetooth** : format `XX:XX:XX:XX:XX:XX`, normalisé en majuscules
2. **Ports série** : résolution symlink + vérification existence
3. **Cartes ALSA** : regex parsing, index numérique
4. **Commandes système** : via `asyncio.create_subprocess_exec()` (pas de shell)

### Pas de faux positifs

- `/dev/ttyS0` seul ≠ Nextion connecté
- Contrôleur Bluetooth ≠ HC-05 connecté
- Carte audio ≠ Amplificateur physique présent
- → Validation dépend de symlinks/NFD/binding réels

---

## 📊 État du code

### Implémenté ✅
- [x] Project scaffold (dossiers + `__init__.py`)
- [x] `app/main.py` (startup principal)
- [x] `app/config.py` (loader config)
- [x] `core/event_bus.py` (bus événements)
- [x] `utils/logger.py` (logging)
- [x] `services/hardware_check/` (6 checks)
- [x] `tests/test_hardware_check.py` (validation)
- [x] Hardware report display (lisible)
- [x] Environment-based config (variables)

### À faire (Phase 2-6)

- [ ] Adapters matériel (ReSpeaker, caméra, etc.)
- [ ] Services audio/vision/motion/display/speech
- [ ] Backend client + offline queue
- [ ] Event-driven comportement autonome
- [ ] TTS local (Piper) optionnel
- [ ] Systemd autostart
- [ ] OTA updates
- [ ] Monitoring & telemetry

---

## 💡 Rationales architecturales

### 1. Pourquoi async ?

**Motif** : Koda doit traiter audio, vidéo, Bluetooth, réseau simultanément sans lag.

**Exemple** :
- Micro parle (bloquent traditionnellement) + affichage visage (non-bloquant)
- Sans async → visage gèle pendant record
- Avec async → tout s'exécute en parallèle

### 2. Pourquoi EventBus ?

**Motif** : Découpler services.

**Exemple** :
- Audio service détecte mot-clé → publie `wake_word_detected`
- Core s'abonne → décide réaction
- Nextion s'abonne → affiche animation
- Aucun ne dépend de l'autre directement

### 3. Pourquoi hardware checks stricts ?

**Motif** : Éviter faux positifs et diagnostics trompeurs.

**Sans** :
```
Nextion Display   | DETECTED | ttyS0 exists
(utilisateur branche caméra)
→ Nextion toujours " DETECTED" même débranché
```

**Avec** :
```
Nextion Display   | DETECTED | /dev/serial0 → /dev/ttyAMA0
(utilisateur débranche)
→ Immédiatement "NOT DETECTED"
```

### 4. Pourquoi environment variables ?

**Motif** : Chaque robot Koda a du matériel différent

**Exemple** :
- Robot A : AUDIO_CARD_INDEX=1 (USB audio)
- Robot B : AUDIO_CARD_INDEX=0 (onboard analog)
- → Même code, différente config

---

## 🚀 Prochaines étapes

### Semaine 1-2 : Services haut-niveau
- Implémenter `AudioService` (ReSpeaker driver)
- Implémenter `DisplayService` (Nextion protocol serial)
- Implémenter `ArduinoService` (RFCOMM binaire)

### Semaine 3-4 : Intelligence locale
- `PersonalityEngine` (comportements)
- `RobotStateMachine` (workflow états)
- Event-driven actions

### Semaine 5-6 : Backend integration
- `BackendService` (HTTP client)
- Offline queue (SQLite)
- Response + state envelope

### Semaine 7+ : Production
- Optimisation performance
- Monitoring/telemetry
- Systemd integration
- OTA updates

---

## 📞 Troubleshooting

### "ModuleNotFoundError: No module named 'app'"

**Cause** : Python path incorrect.

**Fix** :
```bash
python -m app.main  # Au lieu de : python app/main.py
# ou
export PYTHONPATH="$PWD"
python app/main.py
```

### "Hardware checks all failed"

**Cause** : Périfériques non branchés ou mal configurés.

**Fix** :
```bash
# Vérifier chaque composant
arecord -l        # Mic
ls /dev/video*    # Camera
ls /dev/serial0   # Nextion
ls /dev/rfcomm0   # HC-05
aplay -l          # Audio
```

### "No ALSA devices found"

**Cause** : PAM8403 amplifier off ou USB non detected.

**Fix** :
```bash
# Vérifier USB connection
lsusb | grep -i audio

# Revérifier card index
aplay -l

# Mettre à jour AUDIO_CARD_INDEX
```

### "HC-05 not in /dev/rfcomm0"

**Cause** : RFCOMM bind non exécuté ou HC-05 pas en pairing.

**Fix** :
```bash
# Pairing HC-05 d'abord
bluetoothctl pair 00:22:12:02:35:16

# Puis bind
sudo rfcomm bind 0 00:22:12:02:35:16

# Vérifier
ls -l /dev/rfcomm0
```

---

**Fin de la documentation implémentation.**
