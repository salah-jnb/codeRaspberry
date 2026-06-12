# KODA — Logiciel Raspberry Pi

Vue d'ensemble simple et claire du code embarqué qui tourne sur la
Raspberry Pi du robot KODA. Pour les détails par fix / par historique,
voir [CHANGES.md](CHANGES.md) et [AUDIT_REPORT.md](AUDIT_REPORT.md).

---

## 1. Que fait ce code ?

La Raspberry Pi est le **corps opérationnel** du robot. Elle ne pense pas
elle-même — elle écoute, capture, interroge le backend FastAPI, puis
exécute la réponse (parler, bouger, jouer une musique, dormir).

Cycle de vie d'un tour de parole :

```
mot de réveil  ->  rotation vers le locuteur  ->  salutation
       ->  capture de la question  ->  POST /api/audio/speech-to-action
       ->  dispatch de l'action  ->  retour en attente
```

Lancement :

```bash
source .venv/bin/activate
python -m app.main
```

---

## 2. Arborescence du projet

```
codeRaspberry/
├── app/                    point d'entrée + configuration
│   ├── main.py             boucle async, signaux, shutdown
│   └── config.py           AppConfig (dataclasses, lecture .env)
│
├── services/               logique métier (indépendante du matériel)
│   ├── conversation/       orchestrateur d'un tour de parole
│   ├── wake_word/          mot de réveil (Vosk + Azure hybride)
│   ├── listener/           capture VAD jusqu'au silence
│   ├── audio/              broadcaster PCM, DOA, lecteur musique
│   ├── speech/             TTS via backend
│   ├── display/            expressions Nextion
│   ├── motion/             commandes moteurs + rotation MPU6050
│   ├── vision/             reconnaissance faciale
│   ├── touch/              capteur tactile (interruption GPIO)
│   └── hardware_check/     diagnostic au boot
│
├── adapters/               interfaces concrètes vers le monde extérieur
│   ├── respeaker_adapter.py    capture sox (USB 6 canaux -> mono 16 kHz)
│   ├── arduino_adapter.py      USB série, send + send_line
│   ├── nextion_adapter.py      UART vers l'écran
│   ├── backend_client.py       HTTP + WebSocket FastAPI
│   ├── audio_output_adapter.py Bluetooth / PulseAudio
│   └── camera_adapter.py       MJPEG / rpicam
│
├── utils/                  utilitaires transverses
│   ├── logger.py               RotatingFileHandler (timestamps ms)
│   ├── subprocess_registry.py  track + killpg pour arrêt propre
│   └── states.py               traces "state/warn/error" lisibles
│
├── arduino/                firmware Arduino (sketch + README)
│   └── koda_arduino.ino    AFMotor + Servo + MPU6050 closed-loop
│
├── tests/                  pytest (config, imports, motion, face, WS)
├── scripts/                outils (configs micro, debug)
├── core/  models/          structures partagées
├── requirements.txt        dépendances Python
└── pyproject.toml          packaging
```

---

## 3. Architecture en 4 couches

L'idée : aucun service n'appelle directement un détail technique
(`sox`, `serial.write`, `requests.post`). Tout passe par un adaptateur.

```
┌────────────────────────────────────────────────────────────┐
│  app/         main.py + config.py                          │
│               boucle principale, signaux, démarrage parallèle│
└────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────┐
│  services/    logique métier — pas de "sox" ni de "requests"│
│               WakeWord, Conversation, Motion, Display, ...  │
└────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────┐
│  adapters/    interfaces concrètes — un par périphérique    │
│               Respeaker, Arduino, Nextion, Backend, Audio,  │
│               Camera                                        │
└────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────┐
│  Matériel : ReSpeaker, Arduino+MPU6050, Nextion, Caméra,    │
│             hauteparleur Bluetooth, capteur tactile         │
│  Réseau   : Backend FastAPI -> n8n + Supabase + Azure       │
└────────────────────────────────────────────────────────────┘
```

Avantages :
- chaque adaptateur est testable seul (fake / mock simple) ;
- on peut changer le micro, l'écran ou le backend sans toucher aux
  services métier ;
- la couche `app/` reste lisible : elle fait l'ordonnancement, pas
  les opérations bas niveau.

---

## 4. Démarrage parallèle

Au boot, `app.main` :

1. Charge la config et installe un `ThreadPoolExecutor` pour les
   appels bloquants.
2. Lance le diagnostic matériel (`hardware_check`).
3. Ouvre les adaptateurs série Nextion et Arduino **en parallèle**.
4. Démarre dans un seul `asyncio.gather` (avec
   `return_exceptions=True`, donc un échec n'arrête pas les autres) :
   - `backend.start()` + `/health` (3 tentatives, back-off 1 s / 2 s / 4 s)
   - connexion Bluetooth du hauteparleur
   - chargement du modèle Vosk (~8 s sur Pi 4)
   - init USB de la `DOAReader`
   - frame idle Nextion
   - geste `hello()` (servos)

Le coût total du boot = coût de la tâche la plus lente
(généralement Bluetooth ou Vosk), pas la somme.

---

## 5. Boucle principale (mode passif / actif)

Une seule fonction `_run_robot_loop_with_touch` surveille **trois**
événements simultanément avec `asyncio.wait(..., FIRST_COMPLETED)` :

| Événement                         | Action                                                |
|---|---|
| `wake_word_loop` termine          | tour de conversation fini, on en relance un          |
| `touch_event.set` (capteur)       | annule tout, joue la réaction "chatouilles"          |
| `stop_event.set` (SIGINT/SIGTERM) | arrêt propre                                          |

En mode actif, après le 1er tour, le robot reste à l'écoute pendant
`max_active_silences` tours avant de retomber en passif.

---

## 6. Pipeline vocal hybride

Le mot de réveil utilise une stratégie en deux temps :

| État        | Ce qui tourne                                | Pourquoi                         |
|---|---|---|
| `SLEEP`     | Vosk local seulement                         | zéro appel cloud, < 300 ms         |
| `AWAITING`  | Vosk + WebSocket Azure en parallèle (race)   | confirme un candidat Vosk         |

Si aucun moteur ne confirme dans `HYBRID_WAKE_WORD_AWAITING_TIMEOUT`
secondes, on retourne en `SLEEP` (faux positif).

La capture de la question est faite par sox en mode VAD
(`ContinuousListenerService`) : on s'arrête au premier silence assez
long, et on rejette les WAV trop courts (44 octets = sox n'a pas
démarré → on ne pollue pas le backend).

---

## 7. Rotation en boucle fermée (MPU6050)

L'Arduino expose **deux** familles de commandes :

```
# Commandes legacy (un caractère, sans réponse)
F B S L R H T G D A + - ?

# Commandes étendues (avec réponse, bloquantes)
L045\n      ->  DONE:46\n     ou  ERR:timeout\n / ERR:nogyro\n
R180\n      ->  DONE:182\n
```

Côté Pi, `MotionService.rotate_by_angle(+45)` envoie `R045\n` via
`ArduinoAdapter.send_line` et attend `DONE:<actual>`. Le firmware
intègre la vitesse angulaire du gyroscope à 200 Hz, freine sur les
6 derniers degrés, puis stoppe les moteurs. Précision : ±2°.

Voir [arduino/README.md](arduino/README.md) pour le câblage et le
détail du protocole.

---

## 8. Dispatch des 5 actions

Le backend renvoie un `ActionResult` typé. `ConversationService` fait
le dispatch :

| `type`   | Comportement                                                |
|---|---|
| `text`   | expression `SINGING` + lecture de l'audio TTS reçu          |
| `music`  | annonce vocale + téléchargement backend + lecture `paplay`  |
| `motion` | forward / backward / left / right / stop (via MotionDispatcher) |
| `sleep`  | expression `SLEEPING` + retour en mode PASSIF               |
| `error`  | expression `SAD` brève + log + reprise                      |

---

## 9. Arrêt propre

Sur `SIGINT` / `SIGTERM` :

1. annulation des tâches asyncio (boucle, face recognition refresh) ;
2. `display.set_expression(SLEEPING)` ;
3. `motion.stop()` ;
4. `kill_tracked_subprocesses()` puis `pkill_orphans()` — tue
   tout `sox` / `paplay` / `rpicam` survivant (envoi `SIGTERM` puis
   `SIGKILL` sur le **groupe** de processus via `os.killpg`) ;
5. fermeture du client HTTP, des ports série Nextion et Arduino.

Cette robustesse évite que les `sox` zombies bloquent le périphérique
USB au prochain démarrage.

---

## 10. Configuration `.env` (variables clés)

```bash
ROBOT_ID=koda-01
BACKEND_URL=http://192.168.69.185:8000

# Audio
RESPEAKER_DEVICE=pipewire           # plughw:3,0 buggy avec set_freq 16 kHz
AUDIO_OUTPUT_BLUETOOTH_MAC=XX:XX:XX:XX:XX:XX

# Wake word
VOSK_LANGUAGE=ar                    # ar (mgb2), fr, en
HYBRID_WAKE_WORD_ENABLED=1
HYBRID_WAKE_WORD_AWAITING_TIMEOUT=4.0

# Rotation
ROTATION_ENABLED=1                  # MPU6050 closed-loop si firmware OK

# Touch + face
TOUCH_ENABLED=1
TOUCH_PIN=17
FACE_RECOGNITION_ENABLED=1
```

Tous les défauts sont dans [app/config.py](app/config.py).

---

## 11. Tests

```bash
pytest tests/                                  # suite unitaire
python tests/test_ws_wake_word.py respeaker    # smoke WebSocket
python tests/test_mic_configs.py               # 6 configs micro
python -m app.main                             # bout-en-bout
```

---

## 12. Pour aller plus loin

- Diagrammes UML (classes, séquences, états) :
  voir le chapitre **Cycle 4** du rapport PFE
  (`Raport-pfe/finalyearrapport.tex`, section "Cycle 4").
- Sources Mermaid des diagrammes :
  [`Raport-pfe/mermaid/ch4-cycle4/`](../Raport-pfe/mermaid/ch4-cycle4/).
- Historique des fixes : [CHANGES.md](CHANGES.md).
- Audit Session 1 : [AUDIT_REPORT.md](AUDIT_REPORT.md).
