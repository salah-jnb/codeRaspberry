"""Journal d'état de KODA — une ligne CLAIRE et SIMPLE par état du robot.

Chaque ``state(tag)`` affiche une phrase en français facile à lire (« Je dors »,
« Je t'écoute », « Je réfléchis »…) au lieu d'un jargon technique. Le but : voir
d'un coup d'œil ce que fait le robot, sans connaître le code.

Astuce : lance avec ``LOG_SIMPLE=1 python -m app.main`` pour ne garder QUE ce
journal (les logs techniques des modules passent en silencieux). Voir
``utils/logger.py``.

Usage :
    from utils.states import state, error, warn
    state("PASSIVE")            -> 😴 Je dors — appelle-moi pour me réveiller
    state("WAKE", "mohsen")     -> 🎯 On m'a appelé !  (mohsen)
    state("HEARD", "salut")     -> 📝 J'ai entendu : salut
"""
from __future__ import annotations

from typing import Any

from utils.logger import get_logger

logger = get_logger("koda")

# tag -> (emoji, phrase simple en français)
_PHRASES: dict[str, str] = {
    "BOOT":          "🤖 Je démarre…",
    "HW":            "🔌 Je vérifie mon corps (micro, caméra, moteurs)…",
    "READY":         "✅ Je suis prêt !",
    "PASSIVE":       "😴 Je dors — appelle-moi « Mohsen » pour me réveiller",
    "WAKE":          "🎯 On m'a appelé !",
    "GREET":         "👋 Je dis bonjour",
    "ACTIVE":        "👂 Je t'écoute…",
    "THINK":         "🤔 Je réfléchis…",
    "HEARD":         "📝 J'ai entendu :",
    "REPLY":         "💬 J'ai une réponse",
    "SPEAK":         "🗣️  Je parle…",
    "SILENCE":       "🤫 Silence… je vais bientôt me rendormir",
    "SLEEP":         "😴 Je me rendors",
    "PASSIVE-GREET": "👋 Personne ne parle — je lance la conversation tout seul",
    "TOUCH":         "✋ On m'a touché — j'arrête tout",
    "LISTEN":        "🎙️  Quelqu'un parle — j'ouvre grand les oreilles",
    "DOZE":          "💤 Plus rien à entendre — je remets les oreilles en veille",
    "MUSIC":         "🎵 Je mets de la musique",
    "MOVE":          "🦿 Je bouge",
    "SHUTDOWN":      "🛑 Je m'éteins",
}

# Tags pour lesquels le petit complément (nom, texte entendu…) est utile et
# reste lisible — on l'ajoute « : … ». Pour tous les autres on ignore le détail
# technique afin de garder le journal propre.
_KEEP_DETAIL = {"WAKE", "GREET", "HEARD", "REPLY", "PASSIVE-GREET", "MUSIC"}


def state(tag: str, message: str = "", **extras: Any) -> None:
    phrase = _PHRASES.get(tag)
    if phrase is None:
        # Tag inconnu : on garde un affichage minimal (compat).
        body = f" {message}" if message else ""
        logger.info("• %s%s", tag, body)
        return
    detail = f" {message.strip()}" if (message and tag in _KEEP_DETAIL) else ""
    logger.info("%s%s", phrase, detail)


def warn(message: str, **extras: Any) -> None:
    logger.warning("%s", message)


def error(message: str, **extras: Any) -> None:
    logger.error("%s", message)
