# 🤖 KODA - Robot Compagnant Intelligent

## 📋 Table des matières
1. [Vue d'ensemble](#vue-densemble)
2. [Architecture globale](#architecture-globale)
3. [Composants matériels](#composants-matériels)
4. [Architecture Raspberry Pi](#architecture-raspberry-pi)
5. [Flux de communication](#flux-de-communication)
6. [Services principaux](#services-principaux)
7. [Plan de développement](#plan-de-développement)

---

## 🎯 Vue d'ensemble

**KODA** est un robot compagnant intelligent avec :
- **Autonomie locale** : comportements sans Internet
- **Intelligence distribuée** : n8n pour la réflexion
- **Multimodalité** : audio, vision, mouvement, expression
- **Architecture cloud-agnostique** : Supabase pour persistance

---

## 🏗️ Architecture globale

```
┌─────────────────────────────────────────────────────────────────┐
│                         KODA ECOSYSTEM                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌──────────────────┐      ┌──────────────────┐      ┌────────┐ │
│  │  HARDWARE LAYER  │      │   BRAIN LAYER    │      │ STORAGE│ │
│  ├──────────────────┤      ├──────────────────┤      ├────────┤ │
│  │ • Raspberry Pi 4 │      │ • n8n Workflow   │      │Supabase│ │
│  │ • ReSpeaker 4 μ  │      │ • Decision Logic │      │        │ │
│  │ • Arduino UNO    │      │ • Analysis       │      │ Users  │ │
│  │ • Caméra RPi     │      │ • Trends         │      │ Config │ │
│  │ • Nextion Screen │      │ • TTS Generator  │      │ Memory │ │
│  │ • Moteurs DC     │      │                  │      │        │ │
│  │ • Servos         │      │                  │      │        │ │
│  │ • Speaker 2x     │      │                  │      │        │ │
│  └──────────────────┘      └──────────────────┘      └────────┘ │
│           △                        △                       △      │
│           │                        │                       │      │
└───────────┼────────────────────────┼───────────────────────┼──────┘
            │                        │                       │
         ┌──▼────────────────────────▼───────────────────────▼──┐
         │  ORCHESTRATION LAYER - Backend Python               │
         │  • Service Distribution                              │
         │  • API REST                                          │
         │  • State Management                                  │
         └─────────────────────────────────────────────────────┘
            △
            │
         ┌──▼────────────────────────────────────────────────────┐
         │  CONTROL LAYER - CrossPlatform App                   │
         │  • Configuration                                      │
         │  • Supervision                                        │
         │  • Control                                            │
         └─────────────────────────────────────────────────────┘
```

---

## 🔧 Composants matériels

### Cœur système
| Composant | Modèle | Rôle |
|-----------|--------|------|
| **Processeur** | Raspberry Pi 4 Model B | Orchestration globale |
| **Micro** | ReSpeaker 4-Mic Array USB | Capture audio + détection direction |
| **Caméra** | Camera Module Pi | Vision / détection visage |
| **Écran** | Nextion | Affichage visage animé |
| **Amplificateur audio** | PAM8403 | Amplification signal audio |
| **Haut-parleurs** | 2x HP Jack | Synthèse vocale |
| **Contrôle moteurs** | Arduino UNO + L293D | Pilotage moteurs/servos |
| **Liaison sans-fil** | Bluetooth HC-05 | Comm Raspberry ↔ Arduino |
| **Moteurs DC** | 2x | Déplacement du robot |
| **Servos moteurs** | 3x | Bras (2x) + Cou (1x) |

---

## 🧠 Architecture Raspberry Pi

### Couches logiques

```
┌─────────────────────────────────────────────────────────────┐
│                    MAIN.PY - Point d'entrée                 │
├─────────────────────────────────────────────────────────────┤
│  1. Charger config                                           │
│  2. Initialiser logs                                         │
│  3. Lancer hardware_check service                            │
│  4. Démarrer tous les services                               │
│  5. Boucle principale d'événements                           │
└──────────────────────┬──────────────────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        │              │              │
        ▼              ▼              ▼
    ┌────────────────────────────────────────┐
    │       CORE LAYER - Logique métier      │
    ├────────────────────────────────────────┤
    │ • Robot State Machine                  │
    │ • Event Bus                            │
    │ • Personality Engine                   │
    │ • Decision Logic                       │
    └────────────────────────────────────────┘
        │              │              │
        ▼              ▼              ▼
    ┌──────────┬──────────┬──────────┬──────────┬────────────┐
    │          │          │          │          │            │
    ▼          ▼          ▼          ▼          ▼            ▼
┌────────┐┌────────┐┌────────┐┌────────┐┌────────┐┌─────────┐
│ Audio  ││ Vision ││ Motion ││ Display││ Speech ││ Backend │
│Service ││Service ││Service ││Service ││Service ││Service  │
└────┬───┘└────┬───┘└────┬───┘└────┬───┘└────┬───┘└────┬────┘
     │         │         │         │         │         │
     ▼         ▼         ▼         ▼         ▼         ▼
┌──────────────────────────────────────────────────────────────┐
│         ADAPTERS LAYER - Interface matériel                  │
├──────────────────────────────────────────────────────────────┤
│ • RespeakerAdapter      • CameraAdapter   • ArduinoAdapter   │
│ • NexionAdapter         • AudioPlayAdapter • BackendClient  │
└──────────────────────────────────────────────────────────────┘
```

---

## 📡 Flux de communication

### Flux audio (question utilisateur)

```
Utilisateur parle
        │
        ▼
┌─────────────────────────────┐
│ ReSpeaker 4-Mic              │
│ • Écoute continue            │
│ • Détection mot-clé ("Koda") │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│ Audio Service                │
│ • Enregistrement             │
│ • Détection direction        │
│ • Nettoyage audio            │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│ Backend Python Service       │
│ • Reçoit audio WAV           │
│ • Envoie au Backend local    │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│ BACKEND PYTHON              │
│ • STT (Whisper local)        │
│ • Décision (HTTP → n8n)      │
│ • Génère réponse             │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│ Speech Service               │
│ • TTS (Azure ou Local)       │
│ • Synthétise réponse         │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│ PAM8403 + Speakers           │
│ → L'utilisateur entend la    │
│   réponse                    │
└─────────────────────────────┘
```

### Flux visuel (détection + expression)

```
Caméra enregistre
        │
        ▼
┌─────────────────────────────┐
│ Vision Service               │
│ • Détection visage           │
│ • Estimation pose            │
│ • Reconnaissance émotion?    │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│ Core - Personality Engine    │
│ • Décide réaction du robot   │
│ • Prépare animation          │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│ Display Service              │
│ • Envoie commandes Nextion   │
│ • Affiche visage animé       │
└─────────────────────────────┘
```

### Flux moteur (mouvements)

```
Core décide action
        │
        ▼
┌─────────────────────────────┐
│ Motion Service               │
│ • Prépare séquence moteur    │
│ • Sérialise commandes        │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│ Arduino Adapter              │
│ • Envoie via HC-05 BLE       │
│ • Manage connexion           │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│ Arduino UNO + L293D          │
│ • Pilote moteurs DC          │
│ • Actionne servos            │
└──────────┬──────────────────┘
           │
           ▼
    Le robot bouge
```

---

## 🔨 Services principaux

### 1. Audio Service
- **Entrée** : flux ReSpeaker
- **Sortie** : WAV buffer
- **Responsabilités** :
  - écoute continue
  - détection mot-clé
  - enregistrement
  - détection direction
  - nettoyage audio

### 2. Vision Service
- **Entrée** : flux caméra
- **Sortie** : événements visuel
- **Responsabilités** :
  - capture caméra
  - détection visage
  - pose estimation
  - événements visuels

### 3. Motion Service
- **Entrée** : commands haut-niveau
- **Sortie** : ordres Arduino
- **Responsabilités** :
  - séquences moteur
  - ciné inverse (IK)
  - limites de mouvement
  - envoi Arduino

### 4. Display Service
- **Entrée** : state du robot
- **Sortie** : ordres Nextion
- **Responsabilités** :
  - rendu animation
  - gestion écran
  - protocole Nextion

### 5. Speech Service
- **Entrée** : texte réponse
- **Sortie** : audio WAV
- **Responsabilités** :
  - TTS (Azure ou local)
  - gestion cache
  - lecture audio

### 6. Backend Service
- **Entrée** : données du robot
- **Sortie** : décisions + réponses
- **Responsabilités** :
  - HTTP client vers Backend Python
  - HTTP client vers n8n
  - gestion requêtes
  - timeout/retry

### 7. Arduino Service (Bluetooth)
- **Entrée** : ordres moteur
- **Sortie** : confirmation
- **Responsabilités** :
  - connexion HC-05
  - protocole série
  - heartbeat
  - reconnexion auto

---

## 📂 Structure des dossiers

```
codeRaspberry/
├── README.md                      # Ce fichier
├── ARCHITECTURE.md                # Documentation détaillée
├── requirements.txt               # Dépendances Python
├── pyproject.toml                 # Config projet
│
├── app/
│   ├── __init__.py
│   ├── main.py                    # Point d'entrée
│   └── config.py                  # Config centralisée
│
├── core/
│   ├── __init__.py
│   ├── robot_state.py             # État du robot
│   ├── event_bus.py               # Bus d'événements
│   ├── personality_engine.py      # Logique comportement
│   └── decision_logic.py           # Décisions
│
├── services/
│   ├── __init__.py
│   ├── audio/
│   │   ├── __init__.py
│   │   ├── audio_service.py
│   │   ├── wake_word_detector.py
│   │   └── audio_recorder.py
│   ├── vision/
│   │   ├── __init__.py
│   │   └── vision_service.py
│   ├── motion/
│   │   ├── __init__.py
│   │   └── motion_service.py
│   ├── display/
│   │   ├── __init__.py
│   │   └── display_service.py
│   ├── speech/
│   │   ├── __init__.py
│   │   └── speech_service.py
│   ├── backend/
│   │   ├── __init__.py
│   │   └── backend_service.py
│   ├── arduino/
│   │   ├── __init__.py
│   │   └── arduino_service.py
│   └── hardware_check/
│       ├── __init__.py
│       ├── hardware_check_service.py
│       ├── checks/
│       │   ├── mic_check.py
│       │   ├── camera_check.py
│       │   ├── nextion_check.py
│       │   ├── bluetooth_check.py
│       │   ├── audio_check.py
│       │   └── system_check.py
│       └── status_report.py
│
├── adapters/
│   ├── __init__.py
│   ├── respeaker_adapter.py
│   ├── camera_adapter.py
│   ├── nextion_adapter.py
│   ├── bluetooth_adapter.py
│   ├── audio_output_adapter.py
│   └── backend_client.py
│
├── config/
│   ├── __init__.py
│   ├── config.yaml               # Config par défaut
│   └── config.schema.json         # Validation
│
├── utils/
│   ├── __init__.py
│   ├── logger.py                 # Logs structurés
│   ├── decorators.py
│   └── helpers.py
│
├── tests/
│   ├── __init__.py
│   ├── test_services/
│   ├── test_core/
│   └── mocks/
│
├── assets/
│   ├── nextion_ui/                # Images/HMI Nextion
│   └── audio/                     # WAV, TTS cache
│
└── .env                           # Variables d'environnement
```

---

## 📋 Plan de développement

### Phase 1 : Fondation (Semaine 1-2)
- [ ] Architecture projet + dossiers
- [ ] Config centralisée (YAML)
- [ ] Logging structuré
- [ ] Service Hardware Check
- [ ] Mocks pour tous les adapters
- [ ] Tests unitaires de base

### Phase 2 : Connexion matériel (Semaine 3-4)
- [ ] Adapter ReSpeaker
- [ ] Adapter Bluetooth HC-05
- [ ] Adapter Nextion
- [ ] Adapter caméra
- [ ] Audio output adapter
- [ ] Test hardware réel

### Phase 3 : Services core (Semaine 5-6)
- [ ] Audio Service (enregistrement)
- [ ] Arduino Service
- [ ] Display Service
- [ ] Motion Service
- [ ] Event Bus

### Phase 4 : Intelligence locale (Semaine 7-8)
- [ ] Personality Engine
- [ ] Comportements autonomes
- [ ] Détection mot-clé
- [ ] État machine du robot

### Phase 5 : Backend connection (Semaine 9-10)
- [ ] Backend Service
- [ ] Vision Service
- [ ] Speech Service
- [ ] Communication HTTP
- [ ] Gestion erreurs

### Phase 6 : Optimisation (Semaine 11+)
- [ ] Performance tuning
- [ ] Cache optimisé
- [ ] Autostarting systemd
- [ ] OTA updates
- [ ] Monitoring

---

## 🚀 Commandes utiles

```bash
# Installer dépendances
pip install -r requirements.txt

# Lancer le robot
python app/main.py

# Tests
pytest tests/

# Vérifier hardware
python -c "from services.hardware_check import hardware_check_service; hardware_check_service.run_full_check()"
```

---

## 📞 Support & Contribution

Voir les issues GitHub pour les problèmes connus et en cours.

---

**Version** : 0.1.0  
**Date** : 6 Mai 2026  
**Auteur** : Équipe KODA
