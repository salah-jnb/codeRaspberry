# 📊 Diagrammes Architecture KODA

## 1. Architecture globale système

```mermaid
graph TB
    USER["👤 Utilisateur"]
    
    USER -->|Parole| HARD["🤖 Hardware Layer"]
    USER -->|Visage| HARD
    
    HARD -->|Questions<br/>Images| KODA["🧠 KODA<br/>Raspberry Pi 4"]
    
    KODA -->|Requêtes| BACKEND["⚙️ Backend<br/>Python"]
    BACKEND -->|Décisions| N8N["🔄 n8n<br/>Workflow"]
    N8N -->|Données| SUPABASE["💾 Supabase"]
    
    BACKEND -->|Réponses| KODA
    KODA -->|Audio| SPEAKERS["🔊 Haut-parleurs"]
    KODA -->|Animation| NEXTION["📱 Nextion"]
    KODA -->|Mouvements| ARDUINO["🎮 Arduino<br/>HC-05"]
    
    SPEAKERS -->|Son| USER
    NEXTION -->|Visage| USER
    ARDUINO -->|Mouvements| USER
```

## 2. Couches Raspberry Pi

```mermaid
graph TD
    MAIN["main.py<br/>Point d'entrée"]
    
    MAIN --> CHECK["Hardware Check<br/>Service"]
    CHECK -->|OK| INIT["Init Services"]
    CHECK -->|Failed| ERROR["Error Report"]
    
    INIT --> CORE["Core Layer<br/>Event Bus + State"]
    
    CORE --> AUDIO["Audio Service"]
    CORE --> VISION["Vision Service"]
    CORE --> MOTION["Motion Service"]
    CORE --> DISPLAY["Display Service"]
    CORE --> SPEECH["Speech Service"]
    CORE --> BACKEND["Backend Service"]
    CORE --> ARDUINO["Arduino Service"]
    
    AUDIO --> ADAPT1["ReSpeaker<br/>Adapter"]
    VISION --> ADAPT2["Camera<br/>Adapter"]
    MOTION --> ADAPT3["Motion Calc<br/>Adapter"]
    DISPLAY --> ADAPT4["Nextion<br/>Adapter"]
    SPEECH --> ADAPT5["Audio Output<br/>Adapter"]
    BACKEND --> ADAPT6["HTTP Client<br/>Adapter"]
    ARDUINO --> ADAPT7["Bluetooth HC-05<br/>Adapter"]
    
    ADAPT1 --> HW1["Matériel"]
    ADAPT2 --> HW1
    ADAPT3 --> HW1
    ADAPT4 --> HW1
    ADAPT5 --> HW1
    ADAPT6 --> NET["Réseau"]
    ADAPT7 --> HW1
```

## 3. Flux audio complet

```mermaid
sequenceDiagram
    actor User
    participant Audio as Audio<br/>Service
    participant Core as Core<br/>Bot
    participant Backend as Backend<br/>Python
    participant N8N as n8n<br/>Workflow
    participant TTS as Speech<br/>Service
    participant Speaker as 🔊 Speaker

    User->>Audio: Parle "Salut Koda..."
    
    Audio->>Audio: Détecte mot-clé
    Audio->>Audio: Enregistre audio
    Audio->>Audio: Détecte direction
    
    Audio->>Core: Événement: question_recorded
    Core->>Backend: POST /question<br/>{audio_wav}
    
    Backend->>N8N: HTTP → Workflow
    N8N->>N8N: Analyse<br/>Classification<br/>Décision
    N8N->>Backend: Réponse JSON
    
    Backend->>Core: Réponse texte
    Core->>TTS: Synthétise texte
    TTS->>Speaker: Audio WAV
    Speaker->>User: 🔊 Réponse vocale
```

## 4. Flux visuel (caméra + Nextion)

```mermaid
sequenceDiagram
    actor User
    participant Vision as Vision<br/>Service
    participant Core as Core<br/>Bot
    participant Display as Display<br/>Service
    participant Nextion as Nextion<br/>Screen

    par Caméra
        Vision->>Vision: Capture frame
        Vision->>Vision: Détecte visage
        Vision->>Core: face_detected<br/>pose={x,y,size}
    and Réaction
        Core->>Core: Personality Engine<br/>Décide expression
        Core->>Display: show_expression<br/>{smile,blink}
    end
    
    Display->>Nextion: Serial command
    Nextion->>Nextion: Render animation
    Nextion->>User: 😊 Affichage visage
    
    
    User->>Vision: (continu)
```

## 5. Flux moteur et Arduino

```mermaid
sequenceDiagram
    participant Core as Core<br/>Bot
    participant Motion as Motion<br/>Service
    participant Arduino as Arduino<br/>Service
    participant BT as HC-05<br/>Bluetooth
    participant Uno as Arduino<br/>UNO
    participant Motors as 🔌 Moteurs<br/>DC + Servos

    Core->>Motion: move_forward<br/>{speed: 100}
    Motion->>Motion: Calcul trajectoire
    Motion->>Motion: Sérialise commandes
    
    Motion->>Arduino: Arduino cmd
    Arduino->>BT: Send via BLE
    BT->>Uno: Reçoit données
    Uno->>Uno: Parse + Execute
    Uno->>Motors: PWM signals
    Motors->>Motors: Rotation moteurs
    Motors->>Core: (feedback optionnel)
```

## 6. État machine du robot

```mermaid
stateDiagram-v2
    [*] --> IDLE: Boot

    IDLE --> LISTENING: Détecte mot-clé
    IDLE --> AUTONOMOUS: Mode autonome

    LISTENING --> RECORDING: Utilisateur parle
    RECORDING --> PROCESSING: Audio complet
    PROCESSING --> THINKING: Envoie au backend
    THINKING --> SPEAKING: Reçoit réponse
    SPEAKING --> IDLE: Parole terminée

    IDLE --> SLEEP: Inactivité 30s
    SLEEP --> IDLE: Détecte mot-clé ou mouvement
    SLEEP --> ERROR_STATE: Erreur critique

    AUTONOMOUS --> EXPRESSING: Ennui/Joie
    EXPRESSING --> AUTONOMOUS: Fin du comportement

    ERROR_STATE --> IDLE: Reset manuel
```

## 7. Communication événements (Event Bus)

```mermaid
graph LR
    AUDIO["📢 Audio<br/>Service"]
    VISION["📷 Vision<br/>Service"]
    MOTION["🎮 Motion<br/>Service"]
    
    BUS["🔴 Event Bus<br/>Central"]
    
    AUDIO -->|publish: wake_word_detected| BUS
    AUDIO -->|publish: question_recorded| BUS
    VISION -->|publish: face_detected| BUS
    MOTION -->|publish: motion_complete| BUS
    
    BUS -->|subscribe| CORE["🧠 Core Logic"]
    BUS -->|subscribe| DISPLAY["📺 Display"]
    BUS -->|subscribe| BACKEND["⚙️ Backend"]
    
    CORE -->|publish: order_motion| BUS
    CORE -->|publish: show_emotion| BUS
    BACKEND -->|publish: response_ready| BUS
```

## 8. Hiérarchie services & adapters

```mermaid
graph TB
    subgraph Services
        S1["Audio Service"]
        S2["Vision Service"]
        S3["Motion Service"]
        S4["Display Service"]
        S5["Speech Service"]
        S6["Backend Service"]
        S7["Arduino Service"]
    end
    
    subgraph Adapters
        A1["ReSpeaker Adapter"]
        A2["Camera Adapter"]
        A3["Nextion Adapter"]
        A4["Bluetooth Adapter"]
        A5["Audio Output Adapter"]
        A6["HTTP Client"]
    end
    
    subgraph Hardware
        H1["🎤 ReSpeaker"]
        H2["📷 Camera"]
        H3["📱 Nextion"]
        H4["🔌 HC-05"]
        H5["🔊 Speaker"]
        H6["☁️ Backend"]
    end
    
    S1 --> A1 --> H1
    S2 --> A2 --> H2
    S4 --> A3 --> H3
    S7 --> A4 --> H4
    S5 --> A5 --> H5
    S6 --> A6 --> H6
```

## 9. Dépendances Python

```
📦 KODA Backend

├── 🔧 Core
│   ├── asyncio (async event loop)
│   ├── pydantic (validation)
│   └── python-dotenv (config)
│
├── 🎤 Audio
│   ├── pyaudio (capture)
│   ├── respeaker (driver)
│   └── scipy (traitement audio)
│
├── 📷 Vision
│   ├── opencv-python (caméra + detection)
│   └── numpy (algos)
│
├── 📱 Display
│   ├── serial (Nextion)
│   └── PIL (images)
│
├── 🌐 Communication
│   ├── requests (HTTP)
│   ├── aiohttp (async HTTP)
│   └── paho-mqtt (MQTT optionnel)
│
├── 🔊 Audio Output
│   ├── pydub (WAV processing)
│   └── pyaudio (playback)
│
└── 🧪 Tests
    ├── pytest
    ├── pytest-asyncio
    └── pytest-mock
```

## 10. Timeline de développement

```mermaid
gantt
    title Développement KODA - Raspberry Pi
    
    section Foundation
    Architecture :arch, 2026-05-06, 7d
    Config + Logs :config, after arch, 7d
    Hardware Check :hwcheck, after config, 5d
    
    section Hardware
    Adapters :adapt, after hwcheck, 14d
    Tests adapters :test_adapt, after adapt, 7d
    
    section Core
    Event Bus :bus, after hwcheck, 7d
    Services :serv, after bus, 14d
    State Machine :state, after serv, 7d
    
    section Intelligence
    Personality :pers, after state, 10d
    Autonomy :auton, after pers, 10d
    
    section Backend
    Backend Client :backend, after state, 14d
    Integration :integ, after backend, 10d
    
    section Release
    Optimization :opt, after integ, 14d
    Production :prod, after opt, 10d
    
    milestone mvp, 2026-08-01, 1d
```

---

**Ces diagrammes décrivent :**
1. L'écosystème global
2. L'architecture interne Raspberry
3. Le flux audio complet (parole)
4. Le flux visuel (vision)
5. Le pilotage des moteurs
6. L'état machine du robot
7. Le bus d'événements
8. La hiérarchie des services
9. Les dépendances
10. Le timeline de développement
