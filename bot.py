import datetime
import asyncio
import json
import logging
import os
import random
import string
import threading
from functools import wraps
from http.server import BaseHTTPRequestHandler, HTTPServer
from json import JSONDecodeError

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)


TOKEN = os.environ.get("BOT_TOKEN")
DATA_FILE = "users.json"
ADMIN_ID_FILE = "admin_id.json"
SIGNAUX_DEFAUT = 3
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "hacker_ci").lstrip("@")
COOLDOWN_SECONDS = 30
ANALYSE_MIN_SECONDS = 8
ANALYSE_MAX_SECONDS = 15
HISTORIQUE_LIMIT = 100
OPPORTUNITE_MIN_SCORE = 0.22


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
)
logger = logging.getLogger(__name__)
file_lock = threading.RLock()
analyses_lock = threading.Lock()
analyses_en_cours = set()


def lire_json(path, default):
    with file_lock:
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            return default.copy() if isinstance(default, dict) else default
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (JSONDecodeError, OSError) as exc:
            logger.exception("Impossible de lire %s. Valeur par défaut utilisée.", path, exc_info=exc)
            return default.copy() if isinstance(default, dict) else default


def ecrire_json(path, data):
    dossier = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(dossier, exist_ok=True)
    tmp_path = os.path.join(dossier, f".{os.path.basename(path)}.{os.getpid()}.tmp")
    with file_lock:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)


def get_admin_id():
    return lire_json(ADMIN_ID_FILE, {}).get("id")


def sauvegarder_admin_id(user_id):
    ecrire_json(ADMIN_ID_FILE, {"id": user_id})


def charger_users():
    return lire_json(DATA_FILE, {})


def sauvegarder_users(data):
    ecrire_json(DATA_FILE, data)


def entier_positif(valeur, defaut=0, maximum=None):
    try:
        nombre = int(valeur)
    except (TypeError, ValueError):
        nombre = defaut

    nombre = max(0, nombre)
    if maximum is not None:
        nombre = min(nombre, maximum)
    return nombre


def timestamp_ou_none(valeur):
    try:
        return float(valeur)
    except (TypeError, ValueError):
        return None


def abonnement_actif(user, maintenant=None):
    if not user.get("vip"):
        return False

    fin_ts = timestamp_ou_none(user.get("vip_fin"))
    if not fin_ts:
        return False

    maintenant = maintenant or datetime.datetime.now()
    return fin_ts > maintenant.timestamp()


def abonnement_expire(user, maintenant=None):
    if not user.get("vip") or not user.get("vip_fin"):
        return False

    fin_ts = timestamp_ou_none(user.get("vip_fin"))
    if not fin_ts:
        return False

    maintenant = maintenant or datetime.datetime.now()
    return fin_ts <= maintenant.timestamp()


def normaliser_signaux_gratuits(user):
    restants_actuels = entier_positif(user.get("restants", 0), maximum=SIGNAUX_DEFAUT)

    if "signaux_gratuits_restants" not in user:
        if user.get("vip") and user.get("vip_signals") is not None:
            gratuits = 0
        else:
            gratuits = restants_actuels
        user["signaux_gratuits_restants"] = gratuits

    user["signaux_gratuits_restants"] = entier_positif(
        user.get("signaux_gratuits_restants", 0),
        maximum=SIGNAUX_DEFAUT,
    )
    user["gratuits_deja_donnes"] = True
    return user["signaux_gratuits_restants"]


def normaliser_signaux_vip(user):
    user["vip_signals"] = entier_positif(user.get("vip_signals", 0))
    return user["vip_signals"]


def normaliser_signaux_restants(user):
    gratuits = normaliser_signaux_gratuits(user)
    vip_signals = normaliser_signaux_vip(user)

    if abonnement_actif(user):
        user["vip"] = True
        user["illimite"] = True
        return vip_signals

    user["illimite"] = False
    if user.get("vip"):
        user["restants"] = vip_signals
    else:
        user["restants"] = gratuits

    return user["restants"]


def remettre_en_mode_gratuit(user):
    gratuits = normaliser_signaux_gratuits(user)
    user["vip"] = False
    user["illimite"] = False
    user["restants"] = gratuits
    user["vip_signals"] = 0
    user.pop("vip_debut", None)
    user.pop("vip_fin", None)
    return gratuits


def appliquer_expiration_si_necessaire(user, maintenant=None):
    if not abonnement_expire(user, maintenant=maintenant):
        return False

    remettre_en_mode_gratuit(user)
    return True


def migrer_si_besoin(data):
    modifie = False

    for user in data.values():
        if not isinstance(user, dict):
            continue

        avant = json.dumps(user, sort_keys=True, ensure_ascii=False)

        if "restants" not in user:
            user["restants"] = 0

        if "vip" not in user:
            user["vip"] = False

        if "code" not in user:
            user["code"] = generer_code_unique(data)

        if "messages" in user and not isinstance(user["messages"], list):
            user["messages"] = []

        historique = user.get("historique_signaux", [])
        if not isinstance(historique, list):
            historique = []
        user["historique_signaux"] = historique[-HISTORIQUE_LIMIT:]

        normaliser_signaux_restants(user)

        apres = json.dumps(user, sort_keys=True, ensure_ascii=False)
        if apres != avant:
            modifie = True

    if modifie:
        sauvegarder_users(data)

    return data


def peut_obtenir_signal(user):
    if abonnement_actif(user):
        return True
    return normaliser_signaux_restants(user) > 0


def generer_code_unique(data):
    codes_existants = {user.get("code") for user in data.values() if isinstance(user, dict)}
    while True:
        code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if code not in codes_existants:
            return code


# ===== MODIFIÉ =====
def trouver_uid_par_code(data, code_cible):
    return next(
        (
            uid
            for uid, user in data.items()
            if isinstance(user, dict) and user.get("code") == code_cible
        ),
        None,
    )


def get_ou_creer_user(user_id):
    data = migrer_si_besoin(charger_users())
    uid = str(user_id)

    if uid not in data:
        data[uid] = {
            "restants": SIGNAUX_DEFAUT,
            "vip": False,
            "code": generer_code_unique(data),
            "gratuits_deja_donnes": True,
            "signaux_gratuits_restants": SIGNAUX_DEFAUT,
            "vip_signals": 0,
            "illimite": False,
            "historique_signaux": [],
        }
        sauvegarder_users(data)

    return data, uid


def sauvegarder_message_id(user_id, message_id):
    data = charger_users()
    uid = str(user_id)
    if uid in data:
        data[uid].setdefault("messages", []).append(message_id)
        sauvegarder_users(data)


def consommer_signal(user_id, signal_txt=None):
    data = charger_users()
    uid = str(user_id)
    if uid not in data:
        return {"restants": 0, "mode": "inconnu", "illimite": False}

    user = data[uid]
    appliquer_expiration_si_necessaire(user)

    if abonnement_actif(user):
        user["dernier_signal"] = datetime.datetime.now().timestamp()
        user["illimite"] = True
        if signal_txt:
            ajouter_historique_signal(user, signal_txt, "abonnement")
        sauvegarder_users(data)
        return {"restants": None, "mode": "abonnement", "illimite": True}

    restants = normaliser_signaux_restants(user)
    if restants <= 0:
        sauvegarder_users(data)
        return {"restants": 0, "mode": "vip" if user.get("vip") else "gratuit", "illimite": False}

    if user.get("vip"):
        vip_signals = max(0, normaliser_signaux_vip(user) - 1)
        user["vip_signals"] = vip_signals
        user["restants"] = vip_signals
        mode = "vip"
        restants_apres = vip_signals
    else:
        gratuits = max(0, normaliser_signaux_gratuits(user) - 1)
        user["signaux_gratuits_restants"] = gratuits
        user["restants"] = gratuits
        mode = "gratuit"
        restants_apres = gratuits

    user["dernier_signal"] = datetime.datetime.now().timestamp()
    if signal_txt:
        ajouter_historique_signal(user, signal_txt, mode)
    sauvegarder_users(data)
    return {"restants": restants_apres, "mode": mode, "illimite": False}


def get_secondes_restantes(user):
    dernier = timestamp_ou_none(user.get("dernier_signal"))
    if not dernier:
        return 0
    ecoule = datetime.datetime.now().timestamp() - dernier
    return max(0, int(COOLDOWN_SECONDS - ecoule))


def formater_date(ts):
    ts = timestamp_ou_none(ts) or datetime.datetime.now().timestamp()
    return datetime.datetime.fromtimestamp(ts).strftime("%d/%m/%Y")


def jours_restants_jusqua(ts, maintenant=None):
    ts = timestamp_ou_none(ts)
    if not ts:
        return 0
    maintenant = maintenant or datetime.datetime.now()
    return max(0, (datetime.datetime.fromtimestamp(ts) - maintenant).days)


def formater_heure_signal(dt):
    return dt.strftime("%H:%M")


def niveau_depuis_coefficient(coefficient):
    if coefficient >= 9:
        return "Très élevé"
    if coefficient >= 8:
        return "Élevé"
    return "Premium"


def texte_compteur_compte(user):
    if abonnement_actif(user):
        return "👑 *Abonnement VIP actif*\n♾️ Signaux illimités"

    restants = normaliser_signaux_restants(user)
    if user.get("vip"):
        return f"👑 Il vous reste *{restants}* signaux VIP."

    if restants > 0:
        return f"⚡ Il vous reste *{restants}* signaux gratuits."

    return (
        "❌ Vous avez épuisé vos signaux gratuits.\n\n"
        "💎 Contactez l'administrateur pour recharger votre compte."
    )


def texte_expiration(user):
    if normaliser_signaux_gratuits(user) <= 0:
        return (
            "❌ Votre abonnement VIP a expiré.\n\n"
            "Vous avez épuisé vos signaux gratuits.\n\n"
            "💎 Contactez l'administrateur."
        )

    return (
        "❌ Votre abonnement VIP a expiré.\n\n"
        f"⚡ Il vous reste *{normaliser_signaux_gratuits(user)}* signaux gratuits."
    )


def ajouter_historique_signal(user, signal_txt, statut):
    historique = user.setdefault("historique_signaux", [])
    historique.append(
        {
            "date": datetime.datetime.now().isoformat(timespec="seconds"),
            "statut": statut,
            "signal": signal_txt,
        }
    )
    user["historique_signaux"] = historique[-HISTORIQUE_LIMIT:]


def opportunite_marche_disponible(user):
    historique = user.get("historique_signaux", [])
    dernieres_minutes = 0
    maintenant = datetime.datetime.now()

    for entree in historique[-10:]:
        try:
            date_signal = datetime.datetime.fromisoformat(entree.get("date", ""))
        except (TypeError, ValueError):
            continue
        if (maintenant - date_signal).total_seconds() <= 180:
            dernieres_minutes += 1

    score = random.random()
    if dernieres_minutes >= 2:
        score -= 0.18
    if maintenant.minute in {0, 1, 29, 30, 31, 58, 59}:
        score -= 0.08

    return score >= OPPORTUNITE_MIN_SCORE


def barre_progression(pourcentage):
    blocs = max(0, min(10, round(pourcentage / 10)))
    return f"{'█' * blocs}{'░' * (10 - blocs)} {pourcentage}%"


def texte_analyse(etape, pourcentage):
    titres = [
        "🔍 Vérification des données...",
        "📊 Analyse des tendances...",
        "🧠 Vérification des probabilités...",
        "⚙️ Validation finale...",
        "✅ Analyse terminée.",
    ]
    return (
        "━━━━━━━━━━━━━━━━━━\n"
        "🎰 *ANALYSE LUCKY JET*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"{titres[etape]}\n\n"
        f"`{barre_progression(pourcentage)}`"
    )


async def lancer_animation_analyse(query, user_id):
    total = random.uniform(ANALYSE_MIN_SECONDS, ANALYSE_MAX_SECONDS)
    etapes = [10, 30, 60, 85, 100]
    poids = [0.22, 0.27, 0.25, 0.26]
    variations = [random.uniform(0.85, 1.15) for _ in poids]
    delais = [poids[i] * variations[i] for i in range(len(poids))]
    facteur = total / sum(delais)

    message = await query.message.reply_text(
        texte_analyse(0, etapes[0]),
        parse_mode=ParseMode.MARKDOWN,
    )
    sauvegarder_message_id(user_id, message.message_id)

    for index in range(1, len(etapes)):
        await asyncio.sleep(delais[index - 1] * facteur)
        try:
            await message.edit_text(
                texte_analyse(index, etapes[index]),
                parse_mode=ParseMode.MARKDOWN,
            )
        except TelegramError:
            logger.info("Impossible de mettre à jour l'animation d'analyse.")

    return message


def generer_signal():
    heure_date = datetime.datetime.now()
    heure_minute = heure_date.minute
    heure_hour = heure_date.hour
    heure_date = heure_date + datetime.timedelta(minutes=7)

    if 16 <= heure_hour < 17:
        return "⏳ *Analyse en cours...*\n\nVeuillez réessayer dans une heure.", False
    if 13 <= heure_minute < 14:
        return "🔄 *Intervalle de jeu détecté.*\nPatientez quelques secondes.", False

    coefficient_number = round(random.uniform(7.00, 10.00), 2)
    half_number = round(coefficient_number / 2, 2)
    reliability = round(half_number / 2, 2)
    niveau = niveau_depuis_coefficient(coefficient_number)

    message = (
        "━━━━━━━━━━━━━━━━━━\n"
        "🚀 *LUCKY JET SIGNAL*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "🎯 *Multiplicateur*\n"
        f"`{coefficient_number}x`\n\n"
        "🛡 *Assurance*\n"
        f"`{half_number}x`\n\n"
        "📊 *Niveau*\n"
        f"`{niveau}`\n\n"
        "✅ *Indice fiable*\n"
        f"`{reliability}x`\n\n"
        "🕒 *Heure*\n"
        f"`{formater_heure_signal(heure_date)}`\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "✅ *Analyse terminée*\n"
        "━━━━━━━━━━━━━━━━━━"
    )
    return message, True


def bouton_signal(restants=None, vip=False, illimite=False):
    if illimite:
        label = "🎰 Obtenir un signal (∞)"
    elif restants is not None:
        label = f"🎰 Obtenir un signal ({restants})"
    else:
        label = "🎰 Obtenir un signal"

    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(label, callback_data="signal")],
            [InlineKeyboardButton("🗑 Effacer", callback_data="effacer")],
        ]
    )


def bouton_vip():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💎 Devenir VIP", callback_data="vip")],
            [InlineKeyboardButton("🗑 Effacer", callback_data="effacer")],
        ]
    )


def handler_securise(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            return await func(update, context)
        except Exception as exc:
            logger.exception("Erreur dans %s", func.__name__, exc_info=exc)
            try:
                if update.callback_query:
                    await update.callback_query.answer("Une erreur est survenue. Réessaie dans quelques secondes.", show_alert=True)
                elif update.effective_message:
                    await update.effective_message.reply_text("⚠️ Une erreur est survenue. Réessaie dans quelques secondes.")
            except TelegramError:
                logger.exception("Impossible d'envoyer le message d'erreur à l'utilisateur.")
            return None

    return wrapper


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Erreur Telegram non interceptée.", exc_info=context.error)


def est_admin(update: Update):
    username = update.effective_user.username if update.effective_user else None
    return username == ADMIN_USERNAME


async def refuser_non_admin(update: Update):
    if update.effective_message:
        await update.effective_message.reply_text("❌ Commande réservée à l'administrateur.")


@handler_securise
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data, uid = get_ou_creer_user(user_id)
    user = data[uid]
    expire = appliquer_expiration_si_necessaire(user)
    normaliser_signaux_restants(user)
    if expire:
        sauvegarder_users(data)

    code = user.get("code", "?")
    vip = user.get("vip", False)
    restants = user.get("restants", 0)
    illimite = abonnement_actif(user)

    if expire:
        texte = f"{texte_expiration(user)}\n\n🔑 Code client : `{code}`"
        markup = bouton_vip() if restants <= 0 else bouton_signal(restants=restants)
    elif illimite:
        vip_debut = user.get("vip_debut")
        vip_fin = user.get("vip_fin")
        jours_restants = jours_restants_jusqua(vip_fin)
        texte = (
            "━━━━━━━━━━━━━━━━━━\n"
            "👑 *ESPACE VIP PREMIUM*\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"🔑 Code client : `{code}`\n\n"
            f"📅 Début : *{formater_date(vip_debut)}*\n"
            f"📆 Expire le : *{formater_date(vip_fin)}*\n"
            f"⏳ Jours restants : *{jours_restants} jour{'s' if jours_restants > 1 else ''}*\n\n"
            f"{texte_compteur_compte(user)}\n\n"
            "🎰 Lance une analyse pour obtenir le prochain signal."
        )
        markup = bouton_signal(vip=vip, illimite=True)
    else:
        texte = (
            "━━━━━━━━━━━━━━━━━━\n"
            "🎰 *LUCKY JET PREMIUM*\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"🔑 Code client : `{code}`\n\n"
            f"{texte_compteur_compte(user)}\n\n"
            "🎯 Appuie sur le bouton pour lancer une analyse."
        )
        markup = bouton_signal(restants=restants, vip=vip) if restants > 0 else bouton_vip()

    msg = await update.message.reply_text(texte, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
    sauvegarder_message_id(user_id, msg.message_id)


@handler_securise
async def clean(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data, uid = get_ou_creer_user(user_id)
    message_ids = data[uid].get("messages", [])

    supprime = 0
    for mid in message_ids:
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=mid)
            supprime += 1
        except TelegramError:
            logger.info("Message déjà supprimé ou inaccessible: %s", mid)

    data[uid]["messages"] = []
    sauvegarder_users(data)

    try:
        await update.message.delete()
    except TelegramError:
        logger.info("Impossible de supprimer la commande /clean.")

    if supprime > 0:
        msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"🧹 {supprime} message{'s' if supprime > 1 else ''} supprimé{'s' if supprime > 1 else ''} !",
        )
        sauvegarder_message_id(user_id, msg.message_id)


@handler_securise
async def mon_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data, uid = get_ou_creer_user(user_id)
    await update.message.reply_text(
        f"🔑 Ton code client est : `{data[uid]['code']}`\n\nDonne ce code à l'admin pour recharger tes signaux.",
        parse_mode=ParseMode.MARKDOWN,
    )


@handler_securise
async def recharger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not est_admin(update):
        await refuser_non_admin(update)
        return

    sauvegarder_admin_id(update.effective_user.id)

    if len(context.args) < 2 or not context.args[1].isdigit():
        await update.message.reply_text(
            "Usage : `/recharge CODE NOMBRE`\nExemple : `/recharge ABC123 250`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    code_cible = context.args[0].upper()
    nombre = int(context.args[1])
    if nombre <= 0 or nombre > 100000:
        await update.message.reply_text("❌ Nombre de signaux invalide.")
        return

    data = migrer_si_besoin(charger_users())
    uid_cible = trouver_uid_par_code(data, code_cible)

    if uid_cible is None:
        await update.message.reply_text(f"❌ Aucun client trouvé avec le code `{code_cible}`.", parse_mode=ParseMode.MARKDOWN)
        return

    if not data[uid_cible].get("vip", False):
        await update.message.reply_text(
            "❌ Ce client n'est pas VIP.\n\n"
            "Activez d'abord le VIP avec :\n\n"
            "/vip CODE"
        )
        return

    user_cible = data[uid_cible]
    if abonnement_actif(user_cible):
        await update.message.reply_text(
            "ℹ️ Ce client possède déjà un abonnement VIP illimité actif.\n\n"
            "La commande /recharge est réservée aux packs VIP classiques."
        )
        return

    user_cible["restants"] = nombre
    user_cible["vip_signals"] = nombre
    user_cible["illimite"] = False
    sauvegarder_users(data)

    await update.message.reply_text(
        f"✅ Client `{code_cible}` rechargé avec *{nombre}* signal{'s' if nombre > 1 else ''} VIP.\n"
        f"👑 Il lui reste maintenant *{nombre}* signaux VIP.",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        await context.bot.send_message(
            chat_id=int(uid_cible),
            text=(
                "🎉 Bonne nouvelle !\n\n"
                "Tes signaux VIP ont été rechargés par l'administrateur.\n"
                f"👑 Il vous reste *{nombre}* signaux VIP.\n\n"
                "Appuie sur /start pour continuer."
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except TelegramError:
        logger.exception("Impossible de notifier le client %s.", uid_cible)


@handler_securise
async def clients(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not est_admin(update):
        await refuser_non_admin(update)
        return

    sauvegarder_admin_id(update.effective_user.id)
    data = migrer_si_besoin(charger_users())
    if not data:
        await update.message.reply_text("Aucun client enregistré.")
        return

    lignes = ["👥 *Liste des clients :*\n"]
    for user in data.values():
        if not isinstance(user, dict):
            continue

        normaliser_signaux_restants(user)
        code = user.get("code", "?")
        if abonnement_actif(user):
            vip_debut = user.get("vip_debut")
            vip_fin = user.get("vip_fin")
            jours_restants = jours_restants_jusqua(vip_fin)
            ligne = (
                f"💎 `{code}` — Abonnement VIP illimité\n"
                f"   💳 Début : {formater_date(vip_debut)}\n"
                f"   📆 Expire le : {formater_date(vip_fin)} ({jours_restants}j restants)"
            )
        elif user.get("vip"):
            restants = normaliser_signaux_vip(user)
            ligne = f"👑 `{code}` — VIP classique — {restants} signal{'s' if restants > 1 else ''} VIP"
        else:
            restants = normaliser_signaux_gratuits(user)
            ligne = f"🆓 `{code}` — Gratuit — {restants} signal{'s' if restants > 1 else ''} gratuit{'s' if restants > 1 else ''}"
        lignes.append(ligne)

    await update.message.reply_text("\n".join(lignes), parse_mode=ParseMode.MARKDOWN)


@handler_securise
async def activer_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not est_admin(update):
        await refuser_non_admin(update)
        return

    sauvegarder_admin_id(update.effective_user.id)
    if not context.args:
        await update.message.reply_text("Usage : `/vip CODE`\nEx: `/vip A3K9F2`", parse_mode=ParseMode.MARKDOWN)
        return

    code_cible = context.args[0].upper()
    data = migrer_si_besoin(charger_users())
    uid_cible = trouver_uid_par_code(data, code_cible)

    if uid_cible is None:
        await update.message.reply_text(f"❌ Aucun client trouvé avec le code `{code_cible}`.", parse_mode=ParseMode.MARKDOWN)
        return

    user_cible = data[uid_cible]
    normaliser_signaux_gratuits(user_cible)
    user_cible["vip"] = True
    user_cible["illimite"] = False
    user_cible["vip_signals"] = normaliser_signaux_vip(user_cible)
    user_cible["restants"] = user_cible["vip_signals"]
    user_cible.pop("vip_debut", None)
    user_cible.pop("vip_fin", None)
    sauvegarder_users(data)
    await update.message.reply_text(
        f"✅ Client `{code_cible}` est maintenant VIP classique 👑\nLe client peut maintenant recevoir des recharges de signaux.",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        await context.bot.send_message(
            chat_id=int(uid_cible),
            text=(
                "🎉 Félicitations !\n\n"
                "👑 Votre compte VIP est maintenant activé.\n"
                "L'administrateur peut maintenant recharger votre compte selon le pack acheté.\n\n"
                "Tapez /start pour continuer."
            ),
        )
    except TelegramError:
        logger.exception("Impossible de notifier le client %s.", uid_cible)


@handler_securise
async def abonnement_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not est_admin(update):
        await refuser_non_admin(update)
        return

    sauvegarder_admin_id(update.effective_user.id)
    if not context.args:
        await update.message.reply_text("Usage : `/abonnement CODE`\nEx: `/abonnement A3K9F2`", parse_mode=ParseMode.MARKDOWN)
        return

    code_cible = context.args[0].upper()
    data = migrer_si_besoin(charger_users())
    uid_cible = trouver_uid_par_code(data, code_cible)

    if uid_cible is None:
        await update.message.reply_text(f"❌ Aucun client trouvé avec le code `{code_cible}`.", parse_mode=ParseMode.MARKDOWN)
        return

    maintenant = datetime.datetime.now()
    fin = maintenant + datetime.timedelta(days=30)
    user_cible = data[uid_cible]
    normaliser_signaux_gratuits(user_cible)
    user_cible["vip"] = True
    user_cible["illimite"] = True
    user_cible["vip_debut"] = maintenant.timestamp()
    user_cible["vip_fin"] = fin.timestamp()
    user_cible["restants"] = normaliser_signaux_vip(user_cible)
    sauvegarder_users(data)

    await update.message.reply_text(
        f"✅ Abonnement mensuel activé pour `{code_cible}` 👑\n\n"
        "♾️ Signaux illimités activés immédiatement.\n\n"
        f"📅 Début : *{maintenant.strftime('%d/%m/%Y')}*\n"
        f"📆 Fin : *{fin.strftime('%d/%m/%Y')}*",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        await context.bot.send_message(
            chat_id=int(uid_cible),
            text=(
                "🎉 Ton abonnement *VIP* est activé ! 👑\n\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"📅 Début : *{maintenant.strftime('%d/%m/%Y')}*\n"
                f"📆 Expire le : *{fin.strftime('%d/%m/%Y')}*\n"
                "⏳ Durée : *30 jours*\n"
                "♾️ Signaux illimités\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Tape /start pour voir ton abonnement."
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except TelegramError:
        logger.exception("Impossible de notifier le client %s.", uid_cible)


@handler_securise
async def desactiver_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not est_admin(update):
        await refuser_non_admin(update)
        return

    sauvegarder_admin_id(update.effective_user.id)
    if not context.args:
        await update.message.reply_text("Usage : `/devip CODE`\nEx: `/devip A3K9F2`", parse_mode=ParseMode.MARKDOWN)
        return

    code_cible = context.args[0].upper()
    data = migrer_si_besoin(charger_users())
    uid_cible = trouver_uid_par_code(data, code_cible)

    if uid_cible is None:
        await update.message.reply_text(f"❌ Aucun client trouvé avec le code `{code_cible}`.", parse_mode=ParseMode.MARKDOWN)
        return

    gratuits = remettre_en_mode_gratuit(data[uid_cible])
    sauvegarder_users(data)
    statut = (
        f"⚡ Il reste *{gratuits}* signal{'s' if gratuits > 1 else ''} gratuit{'s' if gratuits > 1 else ''}."
        if gratuits > 0
        else "❌ Vous avez épuisé vos signaux gratuits.\n\n💎 Contactez l'administrateur pour recharger votre compte."
    )
    await update.message.reply_text(
        f"✅ Statut VIP retiré au client `{code_cible}`.\n\n{statut}",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        await context.bot.send_message(
            chat_id=int(uid_cible),
            text=f"⚠️ Votre statut VIP a été retiré.\n\n{statut}",
            parse_mode=ParseMode.MARKDOWN,
        )
    except TelegramError:
        logger.exception("Impossible de notifier le client %s.", uid_cible)


@handler_securise
async def desabonner_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not est_admin(update):
        await refuser_non_admin(update)
        return

    sauvegarder_admin_id(update.effective_user.id)
    if not context.args:
        await update.message.reply_text("Usage : `/desabo CODE`\nEx: `/desabo A3K9F2`", parse_mode=ParseMode.MARKDOWN)
        return

    code_cible = context.args[0].upper()
    data = migrer_si_besoin(charger_users())
    uid_cible = trouver_uid_par_code(data, code_cible)

    if uid_cible is None:
        await update.message.reply_text(f"❌ Aucun client trouvé avec le code `{code_cible}`.", parse_mode=ParseMode.MARKDOWN)
        return

    gratuits = remettre_en_mode_gratuit(data[uid_cible])
    sauvegarder_users(data)
    statut = (
        f"⚡ Il reste *{gratuits}* signal{'s' if gratuits > 1 else ''} gratuit{'s' if gratuits > 1 else ''}."
        if gratuits > 0
        else "❌ Vous avez épuisé vos signaux gratuits.\n\n💎 Contactez l'administrateur."
    )
    await update.message.reply_text(
        f"✅ Abonnement mensuel coupé pour `{code_cible}`.\n\n{statut}",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        await context.bot.send_message(
            chat_id=int(uid_cible),
            text=(
                "⚠️ Ton abonnement mensuel VIP a été désactivé.\n\n"
                f"{statut}\n\n"
                f"Pour renouveler, contacte l'admin : @{ADMIN_USERNAME}"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except TelegramError:
        logger.exception("Impossible de notifier le client %s.", uid_cible)


@handler_securise
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not est_admin(update):
        await refuser_non_admin(update)
        return

    sauvegarder_admin_id(update.effective_user.id)
    data = migrer_si_besoin(charger_users())
    utilisateurs = [u for u in data.values() if isinstance(u, dict)]
    total = len(utilisateurs)
    abonnements = sum(1 for u in utilisateurs if abonnement_actif(u))
    vip_classiques = sum(1 for u in utilisateurs if u.get("vip") and not abonnement_actif(u))
    gratuits = total - abonnements - vip_classiques

    bientot = []
    maintenant = datetime.datetime.now()
    for user in utilisateurs:
        if abonnement_actif(user) and user.get("vip_fin"):
            jours = jours_restants_jusqua(user["vip_fin"], maintenant=maintenant)
            if 0 <= jours <= 3:
                bientot.append((user.get("code", "?"), jours))

    texte = (
        "📊 Statistiques du bot :\n\n"
        f"👥 Total clients : {total}\n"
        f"💎 Abonnements VIP illimités : {abonnements}\n"
        f"👑 VIP classiques : {vip_classiques}\n"
        f"🆓 Membres gratuits : {gratuits}"
    )
    if bientot:
        texte += "\n\n⚠️ *Abonnements qui expirent bientôt :*"
        for code, jours in bientot:
            texte += f"\n• `{code}` — expire dans {jours} jour{'s' if jours > 1 else ''}"

    await update.message.reply_text(texte, parse_mode=ParseMode.MARKDOWN)


@handler_securise
async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not est_admin(update):
        await refuser_non_admin(update)
        return

    sauvegarder_admin_id(update.effective_user.id)
    await update.message.reply_text(
        "🛠 Commandes admin disponibles :\n\n"
        "/clients — Liste tous les clients et leur statut\n"
        "/stats — Statistiques générales du bot\n"
        "/recharge CODE NOMBRE — Recharge un VIP classique\n"
        "/vip CODE — Active le VIP classique, sans signaux automatiques\n"
        "/devip CODE — Retire le statut VIP d'un client\n"
        "/abonnement CODE — Abonnement VIP illimité 30j\n"
        "/desabo CODE — Coupe l'abonnement mensuel d'un client\n"
        "/admin — Affiche ce menu"
    )


@handler_securise
async def bouton_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    uid = str(user_id)

    with analyses_lock:
        if uid in analyses_en_cours:
            deja_en_cours = True
        else:
            analyses_en_cours.add(uid)
            deja_en_cours = False

    if deja_en_cours:
        await query.answer("⏳ Une analyse est déjà en cours.", show_alert=False)
        msg = await query.message.reply_text(
            "⏳ Une analyse est déjà en cours.\n\nPatiente quelques secondes..."
        )
        sauvegarder_message_id(user_id, msg.message_id)
        return

    try:
        await query.answer()
        data, uid = get_ou_creer_user(user_id)
        user = data[uid]

        expire = appliquer_expiration_si_necessaire(user)
        normaliser_signaux_restants(user)
        if expire:
            sauvegarder_users(data)
            msg = await query.message.reply_text(
                texte_expiration(user),
                reply_markup=bouton_vip() if user.get("restants", 0) <= 0 else bouton_signal(restants=user.get("restants", 0)),
                parse_mode=ParseMode.MARKDOWN,
            )
            sauvegarder_message_id(user_id, msg.message_id)
            return

        attente = get_secondes_restantes(user)
        if attente > 0:
            msg = await query.message.reply_text(
                "⏳ *Le robot termine l'analyse précédente.*\n\n"
                "Temps restant :\n\n"
                f"*{attente} seconde{'s' if attente > 1 else ''}.*",
                parse_mode=ParseMode.MARKDOWN,
            )
            sauvegarder_message_id(user_id, msg.message_id)
            return

        if not peut_obtenir_signal(user):
            msg = await query.message.reply_text(
                texte_compteur_compte(user),
                reply_markup=bouton_vip(),
                parse_mode=ParseMode.MARKDOWN,
            )
            sauvegarder_message_id(user_id, msg.message_id)
            return

        await lancer_animation_analyse(query, user_id)

        data, uid = get_ou_creer_user(user_id)
        user = data[uid]
        expire = appliquer_expiration_si_necessaire(user)
        normaliser_signaux_restants(user)
        if expire:
            sauvegarder_users(data)
            msg = await query.message.reply_text(
                texte_expiration(user),
                reply_markup=bouton_vip() if user.get("restants", 0) <= 0 else bouton_signal(restants=user.get("restants", 0)),
                parse_mode=ParseMode.MARKDOWN,
            )
            sauvegarder_message_id(user_id, msg.message_id)
            return

        if not peut_obtenir_signal(user):
            msg = await query.message.reply_text(
                texte_compteur_compte(user),
                reply_markup=bouton_vip(),
                parse_mode=ParseMode.MARKDOWN,
            )
            sauvegarder_message_id(user_id, msg.message_id)
            return

        if not opportunite_marche_disponible(user):
            msg = await query.message.reply_text(
                "⚠️ *Analyse du marché en cours.*\n\n"
                "Aucune opportunité fiable détectée.\n\n"
                "Réessaie dans quelques minutes.",
                reply_markup=bouton_signal(
                    restants=user.get("restants"),
                    vip=user.get("vip", False),
                    illimite=abonnement_actif(user),
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
            sauvegarder_message_id(user_id, msg.message_id)
            return

        signal_txt, signal_genere = generer_signal()
        if not signal_txt:
            msg = await query.message.reply_text(
                "⚠️ Impossible de générer une prédiction pour le moment.\n\nRéessaie dans quelques instants.",
                reply_markup=bouton_signal(
                    restants=user.get("restants"),
                    vip=user.get("vip", False),
                    illimite=abonnement_actif(user),
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
            sauvegarder_message_id(user_id, msg.message_id)
            return

        if not signal_genere:
            msg = await query.message.reply_text(
                signal_txt,
                reply_markup=bouton_signal(
                    restants=user.get("restants"),
                    vip=user.get("vip", False),
                    illimite=abonnement_actif(user),
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
            sauvegarder_message_id(user_id, msg.message_id)
            return

        etat = consommer_signal(user_id, signal_txt=signal_txt)
        if etat["illimite"]:
            texte = f"{signal_txt}\n\n👑 *Abonnement VIP actif*\n♾️ Signaux illimités"
            markup = bouton_signal(vip=True, illimite=True)
        elif etat["mode"] == "vip":
            restants_apres = etat["restants"]
            if restants_apres > 0:
                texte = f"{signal_txt}\n\n👑 Il vous reste *{restants_apres}* signaux VIP."
                markup = bouton_signal(restants=restants_apres, vip=True)
            else:
                texte = (
                    f"{signal_txt}\n\n"
                    "⚠️ *Dernier signal VIP utilisé.*\n"
                    "💎 Contactez l'administrateur pour recharger votre compte."
                )
                markup = bouton_vip()
        else:
            restants_apres = etat["restants"]
            if restants_apres > 0:
                texte = f"{signal_txt}\n\n⚡ Il vous reste *{restants_apres}* signaux gratuits."
                markup = bouton_signal(restants=restants_apres)
            else:
                texte = (
                    f"{signal_txt}\n\n"
                    "❌ Vous avez épuisé vos signaux gratuits.\n\n"
                    "💎 Contactez l'administrateur pour recharger votre compte."
                )
                markup = bouton_vip()

        msg = await query.message.reply_text(texte, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
        sauvegarder_message_id(user_id, msg.message_id)
    finally:
        with analyses_lock:
            analyses_en_cours.discard(uid)

@handler_securise
async def vip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    msg = await query.message.reply_text(
        "💎 *Offres VIP — Signaux Lucky Jet*\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🥉 Starter — 100 signaux\n"
        "🥈 Standard — 250 signaux\n"
        "🥇 Pro — 500 signaux\n"
        "💼 Elite — 1000 signaux\n"
        "💎 Abonnement VIP — ♾️ signaux illimités / 30 jours\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "💳 Paiement via Wave / Moov Money\n\n"
        f"👉 Contacte l'admin : @{ADMIN_USERNAME}",
        parse_mode=ParseMode.MARKDOWN,
    )
    sauvegarder_message_id(query.from_user.id, msg.message_id)


@handler_securise
async def effacer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        await query.message.delete()
    except TelegramError:
        logger.info("Message déjà supprimé ou inaccessible.")


async def verifier_expirations(context: ContextTypes.DEFAULT_TYPE):
    admin_id = get_admin_id()
    data = migrer_si_besoin(charger_users())
    maintenant = datetime.datetime.now()
    expires = []

    for uid, user in data.items():
        if not isinstance(user, dict):
            continue
        if abonnement_expire(user, maintenant=maintenant):
            code = user.get("code", "?")
            date_fin = formater_date(user["vip_fin"])
            remettre_en_mode_gratuit(user)
            expires.append((uid, code, date_fin, texte_expiration(user)))

    if expires:
        sauvegarder_users(data)

    for uid, code, date_fin, message_client in expires:
        try:
            await context.bot.send_message(
                chat_id=int(uid),
                text=message_client,
                parse_mode=ParseMode.MARKDOWN,
            )
        except TelegramError:
            logger.exception("Impossible de notifier le client %s pour l'expiration.", uid)

        if admin_id:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        "⚠️ *Abonnement VIP expiré automatiquement !*\n\n"
                        f"🔑 Code client : `{code}`\n"
                        f"📆 Date d'expiration : *{date_fin}*\n\n"
                        "Le statut VIP et l'accès illimité ont été retirés."
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                )
            except TelegramError:
                logger.exception("Impossible de notifier l'admin pour l'expiration %s.", code)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot en ligne")

    def log_message(self, format, *args):
        return


def lancer_serveur():
    port = int(os.environ.get("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logger.info("Serveur de santé démarré sur le port %s.", port)
    server.serve_forever()


async def supprimer_webhook(application: Application):
    await application.bot.delete_webhook(drop_pending_updates=True)
    logger.info("Webhook supprimé. Démarrage du polling.")


def creer_application():
    if not TOKEN:
        raise RuntimeError("La variable d'environnement BOT_TOKEN est obligatoire.")
    
    app = ApplicationBuilder().token(TOKEN).post_init(supprimer_webhook).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clean", clean))
    app.add_handler(CommandHandler("moncode", mon_code))
    app.add_handler(CommandHandler("recharge", recharger))
    app.add_handler(CommandHandler("clients", clients))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("vip", activer_vip_cmd))
    app.add_handler(CommandHandler("abonnement", abonnement_cmd))
    app.add_handler(CommandHandler("devip", desactiver_vip_cmd))
    app.add_handler(CommandHandler("desabo", desabonner_cmd))
    app.add_handler(CommandHandler("admin", admin_help))
    app.add_handler(CallbackQueryHandler(bouton_callback, pattern="^signal$"))
    app.add_handler(CallbackQueryHandler(vip_callback, pattern="^vip$"))
    app.add_handler(CallbackQueryHandler(effacer_callback, pattern="^effacer$"))
    app.add_error_handler(error_handler)

    if app.job_queue:
        app.job_queue.run_repeating(verifier_expirations, interval=86400, first=60)
    else:
        logger.warning("Job queue indisponible. Vérifie python-telegram-bot[job-queue] dans requirements.txt.")

    return app


def main():
    threading.Thread(target=lancer_serveur, daemon=True).start()
    app = creer_application()
    logger.info("Bot démarré.")
    app.run_polling(
        poll_interval=1.0,
        timeout=30,
        bootstrap_retries=-1,
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
