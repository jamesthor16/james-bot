import datetime
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


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
)
logger = logging.getLogger(__name__)
file_lock = threading.Lock()


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
    tmp_path = f"{path}.tmp"
    with file_lock:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)


def get_admin_id():
    return lire_json(ADMIN_ID_FILE, {}).get("id")


def sauvegarder_admin_id(user_id):
    ecrire_json(ADMIN_ID_FILE, {"id": user_id})


def charger_users():
    return lire_json(DATA_FILE, {})


def sauvegarder_users(data):
    ecrire_json(DATA_FILE, data)


def normaliser_signaux_restants(user):
    try:
        restants = int(user.get("restants", 0))
    except (TypeError, ValueError):
        restants = 0

    restants = max(0, restants)
    user["restants"] = restants
    return restants


def remettre_en_mode_gratuit(user):
    user["vip"] = False
    user["restants"] = SIGNAUX_DEFAUT
    user["gratuits_deja_donnes"] = True
    user["vip_signals"] = 0
    user.pop("vip_debut", None)
    user.pop("vip_fin", None)
    user.pop("dernier_signal", None)


# ===== MODIFIÉ =====
def migrer_si_besoin(data):
    modifie = False

    for user in data.values():
        if "restants" not in user:
            user["restants"] = 0
            modifie = True

        if "vip" not in user:
            user["vip"] = False
            modifie = True

        if "code" not in user:
            user["code"] = generer_code_unique(data)
            modifie = True

        if "gratuits_deja_donnes" not in user:
            user["gratuits_deja_donnes"] = True
            modifie = True

        restants_avant = user.get("restants", 0)
        normaliser_signaux_restants(user)
        if user["restants"] != restants_avant:
            modifie = True

    if modifie:
        sauvegarder_users(data)

    return data


def peut_obtenir_signal(user):
    return normaliser_signaux_restants(user) > 0


def generer_code_unique(data):
    codes_existants = {user.get("code") for user in data.values()}
    while True:
        code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if code not in codes_existants:
            return code


# ===== MODIFIÉ =====
def get_ou_creer_user(user_id):
    data = migrer_si_besoin(charger_users())
    uid = str(user_id)

    if uid not in data:
        data[uid] = {
            "restants": SIGNAUX_DEFAUT,
            "vip": False,
            "code": generer_code_unique(data),
            "gratuits_deja_donnes": True,
        }
        sauvegarder_users(data)

    return data, uid


def sauvegarder_message_id(user_id, message_id):
    data = charger_users()
    uid = str(user_id)
    if uid in data:
        data[uid].setdefault("messages", []).append(message_id)
        sauvegarder_users(data)


def consommer_signal(user_id):
    data = charger_users()
    uid = str(user_id)
    if uid not in data:
        return 0

    restants = normaliser_signaux_restants(data[uid])
    if restants <= 0:
        sauvegarder_users(data)
        return 0

    data[uid]["restants"] = restants - 1
    if data[uid].get("vip"):
        try:
            vip_signals = int(data[uid].get("vip_signals", restants))
        except (TypeError, ValueError):
            vip_signals = restants
        data[uid]["vip_signals"] = max(0, vip_signals - 1)
    data[uid]["dernier_signal"] = datetime.datetime.now().timestamp()
    sauvegarder_users(data)
    return data[uid]["restants"]


def get_secondes_restantes(user):
    dernier = user.get("dernier_signal")
    if not dernier:
        return 0
    ecoule = datetime.datetime.now().timestamp() - dernier
    return max(0, int(COOLDOWN_SECONDS - ecoule))


def formater_date(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%d/%m/%Y")


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

    message = (
        "━━━━━━━━━━━━━━━━━━\n"
        "🚀 *SIGNAL LUCKY JET* 💸\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"⏰ *Heure :*  `{heure_date.hour}:{heure_date.minute:02d}` — {heure_date.second:02d}s\n\n"
        f"🎯 *Côte :*      `{coefficient_number} X+`\n"
        f"🛡 *Assurance :* `{half_number} X+`\n"
        f"✅ *Fiable :*    `{reliability} X`\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "          🐉💰 *by hacker*"
    )
    return message, True


def bouton_signal(restants=None, vip=False):
    label = f"🎰 Obtenir un signal ({restants})" if restants is not None else "🎰 Obtenir un signal"
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
    vip = user.get("vip", False)
    restants = user.get("restants", 0)
    code = user.get("code", "?")

    if vip:
        vip_debut = user.get("vip_debut")
        vip_fin = user.get("vip_fin")
        if vip_debut and vip_fin:
            jours_restants = max(0, (datetime.datetime.fromtimestamp(vip_fin) - datetime.datetime.now()).days)
            texte = (
                "👑 Bienvenue, membre VIP !\n"
                f"Ton code client : `{code}`\n\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"📅 Abonnement depuis : *{formater_date(vip_debut)}*\n"
                f"📆 Expire le : *{formater_date(vip_fin)}*\n"
                f"⏳ Jours restants : *{jours_restants} jour{'s' if jours_restants > 1 else ''}*\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                f"Tu as *{restants}* signal{'s' if restants > 1 else ''} disponible{'s' if restants > 1 else ''}.\n"
                "Appuie sur le bouton pour obtenir un signal."
            )
        else:
            texte = (
                "👑 Bienvenue, membre VIP !\n"
                f"Ton code client : `{code}`\n\n"
                f"Tu as *{restants}* signal{'s' if restants > 1 else ''} disponible{'s' if restants > 1 else ''}.\n"
                "Appuie sur le bouton pour obtenir un signal."
            )
        markup = bouton_signal(restants=restants, vip=vip)
    else:
        texte = (
            "👋 Bienvenue !\n"
            f"Ton code client : `{code}`\n\n"
            f"Tu as *{restants}* signal{'s' if restants > 1 else ''} disponible{'s' if restants > 1 else ''}.\n"
            "Appuie sur le bouton pour obtenir un signal Lucky Jet."
        )
        markup = bouton_signal(restants=restants)

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

    if not context.args:
        await update.message.reply_text(
            "Usage : `/recharge CODE [nombre]`\nExemple : `/recharge ABC123` ou `/recharge ABC123 10`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    code_cible = context.args[0].upper()
    nombre = int(context.args[1]) if len(context.args) > 1 and context.args[1].isdigit() else SIGNAUX_DEFAUT
    data = migrer_si_besoin(charger_users())
    uid_cible = next((uid for uid, u in data.items() if u.get("code") == code_cible), None)

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

    data[uid_cible]["restants"] = nombre
    data[uid_cible]["vip_signals"] = nombre
    sauvegarder_users(data)

    await update.message.reply_text(
        f"✅ Client `{code_cible}` rechargé avec *{nombre}* signal{'s' if nombre > 1 else ''}.\n"
        f"Signaux restants : *{nombre}*",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        await context.bot.send_message(
            chat_id=int(uid_cible),
            text=(
                "🎉 Bonne nouvelle !\n\n"
                "Tes signaux ont été rechargés par l'admin.\n"
                f"Tu as maintenant *{nombre}* signal{'s' if nombre > 1 else ''} disponible{'s' if nombre > 1 else ''}.\n\n"
                "Appuie sur /start pour continuer ! 🚀"
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
        code = user.get("code", "?")
        if user.get("vip"):
            vip_debut = user.get("vip_debut")
            vip_fin = user.get("vip_fin")
            if vip_debut and vip_fin:
                jours_restants = max(0, (datetime.datetime.fromtimestamp(vip_fin) - datetime.datetime.now()).days)
                ligne = (
                    f"💎 `{code}` — Abonnement mensuel\n"
                    f"   💳 Payé le : {formater_date(vip_debut)}\n"
                    f"   📆 Expire le : {formater_date(vip_fin)} ({jours_restants}j restants)"
                )
            else:
                ligne = f"👑 `{code}` — VIP"
        else:
            restants = user.get("restants", 0)
            ligne = f"🆓 `{code}` — Gratuit — {restants} signal{'s' if restants > 1 else ''} restant{'s' if restants > 1 else ''}"
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
    uid_cible = next((uid for uid, u in data.items() if u.get("code") == code_cible), None)

    if uid_cible is None:
        await update.message.reply_text(f"❌ Aucun client trouvé avec le code `{code_cible}`.", parse_mode=ParseMode.MARKDOWN)
        return

    data[uid_cible]["vip"] = True
    sauvegarder_users(data)
    await update.message.reply_text(
        f"✅ Client `{code_cible}` est maintenant VIP 👑\nLe client peut maintenant recevoir des recharges de signaux.",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        await context.bot.send_message(
            chat_id=int(uid_cible),
            text=(
                "🎉 Félicitations !\n\n"
                "👑 Votre compte VIP est maintenant activé.\n"
                "L'administrateur va recharger vos signaux selon le pack acheté.\n\n"
                "Tapez /start pour continuer. 🚀"
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
    uid_cible = next((uid for uid, u in data.items() if u.get("code") == code_cible), None)

    if uid_cible is None:
        await update.message.reply_text(f"❌ Aucun client trouvé avec le code `{code_cible}`.", parse_mode=ParseMode.MARKDOWN)
        return

    maintenant = datetime.datetime.now()
    fin = maintenant + datetime.timedelta(days=30)
    data[uid_cible]["vip"] = True
    data[uid_cible]["vip_debut"] = maintenant.timestamp()
    data[uid_cible]["vip_fin"] = fin.timestamp()
    sauvegarder_users(data)

    await update.message.reply_text(
        f"✅ Abonnement mensuel activé pour `{code_cible}` 👑\n\n"
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
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Tape /start pour voir ton abonnement. 🚀"
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
    uid_cible = next((uid for uid, u in data.items() if u.get("code") == code_cible), None)

    if uid_cible is None:
        await update.message.reply_text(f"❌ Aucun client trouvé avec le code `{code_cible}`.", parse_mode=ParseMode.MARKDOWN)
        return

    remettre_en_mode_gratuit(data[uid_cible])
    sauvegarder_users(data)
    await update.message.reply_text(
        f"✅ Statut VIP retiré au client `{code_cible}`.\nIl repasse en mode gratuit avec {SIGNAUX_DEFAUT} signaux gratuits.",
        parse_mode=ParseMode.MARKDOWN,
    )


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
    uid_cible = next((uid for uid, u in data.items() if u.get("code") == code_cible), None)

    if uid_cible is None:
        await update.message.reply_text(f"❌ Aucun client trouvé avec le code `{code_cible}`.", parse_mode=ParseMode.MARKDOWN)
        return

    remettre_en_mode_gratuit(data[uid_cible])
    sauvegarder_users(data)
    await update.message.reply_text(
        f"✅ Abonnement mensuel coupé pour `{code_cible}`.\nIl repasse en mode gratuit.",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        await context.bot.send_message(
            chat_id=int(uid_cible),
            text="⚠️ Ton abonnement mensuel VIP a été désactivé.\n\nPour renouveler, contacte l'admin : @hacker_ci 💬",
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
    total = len(data)
    vips = sum(1 for u in data.values() if u.get("vip"))
    gratuits = total - vips

    bientot = []
    maintenant = datetime.datetime.now()
    for user in data.values():
        if user.get("vip") and user.get("vip_fin"):
            jours = (datetime.datetime.fromtimestamp(user["vip_fin"]) - maintenant).days
            if 0 <= jours <= 3:
                bientot.append((user.get("code", "?"), jours))

    texte = f"📊 Statistiques du bot :\n\n👥 Total clients : {total}\n👑 Membres VIP : {vips}\n🆓 Membres gratuits : {gratuits}"
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
        "/recharge CODE [N] — Ajoute N signaux (packs 2000/4000/7000 FCFA)\n"
        "/vip CODE — Active le statut VIP (sans abonnement mensuel)\n"
        "/devip CODE — Retire le statut VIP d'un client\n"
        "/abonnement CODE — Abonnement mensuel 30j (12 000 FCFA)\n"
        "/desabo CODE — Coupe l'abonnement mensuel d'un client\n"
        "/admin — Affiche ce menu"
    )


# ===== MODIFIÉ =====
@handler_securise
async def bouton_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data, uid = get_ou_creer_user(user_id)
    user = data[uid]
    vip = user.get("vip", False)
    restants = user.get("restants", 0)

    attente = get_secondes_restantes(user)
    if attente > 0:
        await query.message.reply_text(
            "⏳ *Analyse en cours...*\n\n"
            "Le bot calcule la prochaine côte.\n"
            f"Réessaie dans *{attente} seconde{'s' if attente > 1 else ''}*. 🔍",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if not peut_obtenir_signal(user):
        msg = await query.message.reply_text(
            "🔒 Tu n'as plus de signaux disponibles.\n\n💎 Contacte l'admin pour recharger tes signaux.",
            reply_markup=bouton_vip(),
        )
        sauvegarder_message_id(user_id, msg.message_id)
        return

    signal_txt, signal_genere = generer_signal()
    if not signal_txt:
        msg = await query.message.reply_text(
            "⚠️ Impossible de générer une prédiction pour le moment.\n\nRéessaie dans quelques instants.",
            reply_markup=bouton_signal(restants=restants, vip=vip),
            parse_mode=ParseMode.MARKDOWN,
        )
        sauvegarder_message_id(user_id, msg.message_id)
        return

    if not signal_genere:
        msg = await query.message.reply_text(
            signal_txt,
            reply_markup=bouton_signal(restants=restants, vip=vip),
            parse_mode=ParseMode.MARKDOWN,
        )
        sauvegarder_message_id(user_id, msg.message_id)
        return

    restants_apres = max(0, restants - 1)
    statut_vip = "\n\n👑 Statut : VIP" if vip else ""

    if restants_apres > 0:
        if vip:
            texte_restants = f"👑 Il te reste *{restants_apres}* signaux VIP."
        else:
            texte_restants = f"⚡ Il te reste *{restants_apres}* signaux gratuits."

        texte = f"{signal_txt}{statut_vip}\n\n{texte_restants}"
        markup = bouton_signal(restants=restants_apres, vip=vip)
    else:
        texte = f"{signal_txt}{statut_vip}\n\n⚠️ *Dernier signal utilisé !*\nRecharge tes signaux pour continuer."
        markup = bouton_vip()

    msg = await query.message.reply_text(texte, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
    consommer_signal(user_id)
    sauvegarder_message_id(user_id, msg.message_id)

@handler_securise
async def vip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "💎 Offres VIP — Signaux Lucky Jet\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🥉 Starter   — 100 signaux →  2 000 FCFA\n"
        "🥈 Standard  — 250 signaux →  4 000 FCFA\n"
        "🥇 Pro       — 500 signaux →  7 000 FCFA\n"
        "💎 VIP / mois              → 12 000 FCFA\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "💳 Paiement via Wave / Moov Money\n\n"
        "👉 Contacte l'admin : @hacker_ci"
    )


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
    if not admin_id:
        return

    data = charger_users()
    maintenant = datetime.datetime.now()
    expires = []

    for user in data.values():
        if user.get("vip") and user.get("vip_fin"):
            fin = datetime.datetime.fromtimestamp(user["vip_fin"])
            jours_restants = (fin - maintenant).days
            if jours_restants < 0:
                expires.append((user.get("code", "?"), formater_date(user["vip_fin"])))

    for code, date_fin in expires:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    "⚠️ *Abonnement VIP expiré !*\n\n"
                    f"🔑 Code client : `{code}`\n"
                    f"📆 Date d'expiration : *{date_fin}*\n\n"
                    f"👉 Utilise `/devip {code}` pour couper l'accès."
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
