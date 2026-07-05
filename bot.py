import datetime
import random
import os
import json
import string
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

TOKEN = os.getenv("BOT_TOKEN")
DATA_FILE = "users.json"
SIGNAUX_DEFAUT = 3
ADMIN_USERNAME = "hacker_ci"
ADMIN_ID_FILE = "admin_id.json"

def get_admin_id():
    if os.path.exists(ADMIN_ID_FILE):
        with open(ADMIN_ID_FILE, "r") as f:
            return json.load(f).get("id")
    return None

def sauvegarder_admin_id(user_id):
    with open(ADMIN_ID_FILE, "w") as f:
        json.dump({"id": user_id}, f)

def charger_users():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {}

def sauvegarder_users(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def migrer_si_besoin(data):
    modifie = False
    for uid, user in data.items():
        if "restants" not in user:
            if user.get("vip"):
                user["restants"] = 0
            else:
                user["restants"] = max(0, SIGNAUX_DEFAUT - user.get("signaux", 0))
            modifie = True
    if modifie:
        sauvegarder_users(data)
    return data

def peut_obtenir_signal(user):
    return user["restants"] > 0

def generer_code_unique(data):
    while True:
        code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        codes_existants = [v.get("code") for v in data.values()]
        if code not in codes_existants:
            return code

def get_ou_creer_user(user_id):
    data = charger_users()
    data = migrer_si_besoin(data)
    uid = str(user_id)
    if uid not in data:
        code = generer_code_unique(data)
        data[uid] = {"restants": SIGNAUX_DEFAUT, "vip": False, "code": code}
        sauvegarder_users(data)
    return data, uid

def sauvegarder_message_id(user_id, message_id):
    data = charger_users()
    uid = str(user_id)
    if uid in data:
        if "messages" not in data[uid]:
            data[uid]["messages"] = []
        data[uid]["messages"].append(message_id)
        sauvegarder_users(data)

# ===== MODIFIÉ ICI =====
def consommer_signal(user_id):
    data = charger_users()
    uid = str(user_id)

    # Même un VIP consomme un signal
    data[uid]["restants"] = max(0, data[uid]["restants"] - 1)

    data[uid]["dernier_signal"] = datetime.datetime.now().timestamp()
    sauvegarder_users(data)

def get_secondes_restantes(user):
    dernier = user.get("dernier_signal")
    if not dernier:
        return 0
    ecoule = datetime.datetime.now().timestamp() - dernier
    restant = 30 - ecoule
    return max(0, int(restant))

def formater_date(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%d/%m/%Y")

def generer_signal():
    heureDate = datetime.datetime.now()
    heureMinute = heureDate.minute
    heureHour = heureDate.hour

    minutesAvancees = 7
    heureDate = heureDate + datetime.timedelta(minutes=minutesAvancees)

    if 16 <= heureHour < 17:
        return ("⏳ *Analyse en cours...*\n\nVeuillez réessayer dans une heure.", True)
    elif 13 <= heureMinute < 14:
        return ("🔄 *Intervalle de jeu détecté.*\nPatientez quelques secondes.", True)
    else:
        hack = 7.00
        max_val = 10.00
        coefficientNumber = round(random.uniform(hack, max_val), 2)
        halfNumber = round(coefficientNumber / 2, 2)
        fiabibily = round(halfNumber / 2, 2)

        message = (
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🚀 *SIGNAL LUCKY JET* 💸\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"⏰ *Heure :*  `{heureDate.hour}:{heureDate.minute:02d}` — {heureDate.second:02d}s\n\n"
            f"🎯 *Côte :*      `{coefficientNumber} X+`\n"
            f"🛡 *Assurance :* `{halfNumber} X+`\n"
            f"✅ *Fiable :*    `{fiabibily} X`\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"          🐉💰 *by hacker*"
        )
        return (message, True)

def bouton_signal(restants=None, vip=False):
    if restants is not None:
        label = f"🎰 Obtenir un signal ({restants})"
    else:
        label = "🎰 Obtenir un signal"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data="signal")],
        [InlineKeyboardButton("🗑 Effacer", callback_data="effacer")]
    ])

def bouton_vip():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 Devenir VIP", callback_data="vip")],
        [InlineKeyboardButton("🗑 Effacer", callback_data="effacer")]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    data, uid = get_ou_creer_user(user_id)
    user = data[uid]
    vip = user["vip"]
    restants = user["restants"]
    code = user["code"]

    if vip:
        vip_debut = user.get("vip_debut")
        vip_fin = user.get("vip_fin")
        if vip_debut and vip_fin:
            jours_restants = (datetime.datetime.fromtimestamp(vip_fin) - datetime.datetime.now()).days
            jours_restants = max(0, jours_restants)
            texte = (
                f"👑 Bienvenue, membre VIP !\n"
                f"Ton code client : `{code}`\n\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📅 Abonnement depuis : *{formater_date(vip_debut)}*\n"
                f"📆 Expire le : *{formater_date(vip_fin)}*\n"
                f"⏳ Jours restants : *{jours_restants} jour{'s' if jours_restants > 1 else ''}*\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"Tu as *{restants}* signal{'s' if restants > 1 else ''} disponible{'s' if restants > 1 else ''}.\n"
                f"Appuie sur le bouton pour obtenir un signal."
            )
        else:
            texte = (
                f"👑 Bienvenue, membre VIP !\n"
                f"Ton code client : `{code}`\n\n"
                f"Tu as *{restants}* signal{'s' if restants > 1 else ''} disponible{'s' if restants > 1 else ''}.\n"
                f"Appuie sur le bouton pour obtenir un signal."
            )
        markup = bouton_signal(restants=restants, vip=vip)
    else:
        texte = (
            f"👋 Bienvenue !\n"
            f"Ton code client : `{code}`\n\n"
            f"Tu as *{restants}* signal{'s' if restants > 1 else ''} disponible{'s' if restants > 1 else ''}.\n"
            f"Appuie sur le bouton pour obtenir un signal Lucky Jet."
        )
        markup = bouton_signal(restants=restants)

    msg = await update.message.reply_text(texte, reply_markup=markup, parse_mode="Markdown")
    sauvegarder_message_id(user_id, msg.message_id)

async def clean(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    data, uid = get_ou_creer_user(user_id)
    message_ids = data[uid].get("messages", [])

    supprime = 0
    for mid in message_ids:
        try:
            await context.bot.delete_message(chat_id=update.message.chat_id, message_id=mid)
            supprime += 1
        except Exception:
            pass

    data[uid]["messages"] = []
    sauvegarder_users(data)

    try:
        await update.message.delete()
    except Exception:
        pass

    if supprime > 0:
        msg = await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=f"🧹 {supprime} message{'s' if supprime > 1 else ''} supprimé{'s' if supprime > 1 else ''} !"
        )
        sauvegarder_message_id(user_id, msg.message_id)

async def mon_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    data, uid = get_ou_creer_user(user_id)
    code = data[uid]["code"]
    await update.message.reply_text(
        f"🔑 Ton code client est : `{code}`\n\nDonne ce code à l'admin pour recharger tes signaux.",
        parse_mode="Markdown"
    )

async def recharger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.from_user.username
    if username != ADMIN_USERNAME:
        await update.message.reply_text("❌ Commande réservée à l'administrateur.")
        return

    sauvegarder_admin_id(update.message.from_user.id)

    if not context.args:
        await update.message.reply_text(
            "Usage : `/recharge CODE [nombre]`\nExemple : `/recharge ABC123` ou `/recharge ABC123 10`",
            parse_mode="Markdown"
        )
        return

    code_cible = context.args[0].upper()
    nombre = int(context.args[1]) if len(context.args) > 1 and context.args[1].isdigit() else SIGNAUX_DEFAUT

    data = charger_users()
    data = migrer_si_besoin(data)
    uid_cible = next((uid for uid, u in data.items() if u.get("code") == code_cible), None)

    if uid_cible is None:
        await update.message.reply_text(f"❌ Aucun client trouvé avec le code `{code_cible}`.", parse_mode="Markdown")
        return

    data[uid_cible]["restants"] = nombre
    sauvegarder_users(data)

    await update.message.reply_text(
        f"✅ Client `{code_cible}` rechargé avec *{nombre}* signal{'s' if nombre > 1 else ''}.\n"
        f"Signaux restants : *{nombre}*",
        parse_mode="Markdown"
    )

    try:
        await context.bot.send_message(
            chat_id=int(uid_cible),
            text=(
                f"🎉 Bonne nouvelle !\n\n"
                f"Tes signaux ont été rechargés par l'admin.\n"
                f"Tu as maintenant *{nombre}* signal{'s' if nombre > 1 else ''} disponible{'s' if nombre > 1 else ''}.\n\n"
                f"Appuie sur /start pour continuer ! 🚀"
            ),
            parse_mode="Markdown"
        )
    except Exception:
        pass

async def clients(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.from_user.username
    if username != ADMIN_USERNAME:
        await update.message.reply_text("❌ Commande réservée à l'administrateur.")
        return

    sauvegarder_admin_id(update.message.from_user.id)

    data = charger_users()
    data = migrer_si_besoin(data)
    if not data:
        await update.message.reply_text("Aucun client enregistré.")
        return

    lignes = ["👥 *Liste des clients :*\n"]
    for uid, user in data.items():
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

    await update.message.reply_text("\n".join(lignes), parse_mode="Markdown")

async def activer_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.from_user.username
    if username != ADMIN_USERNAME:
        await update.message.reply_text("❌ Commande réservée à l'administrateur.")
        return

    sauvegarder_admin_id(update.message.from_user.id)

    if not context.args:
        await update.message.reply_text("Usage : `/vip CODE`\nEx: `/vip A3K9F2`", parse_mode="Markdown")
        return

    code_cible = context.args[0].upper()
    data = charger_users()
    data = migrer_si_besoin(data)
    uid_cible = next((uid for uid, u in data.items() if u.get("code") == code_cible), None)

    if uid_cible is None:
        await update.message.reply_text(f"❌ Aucun client trouvé avec le code `{code_cible}`.", parse_mode="Markdown")
        return

    data[uid_cible]["vip"] = True
    sauvegarder_users(data)

    await update.message.reply_text(
    f"✅ Client `{code_cible}` est maintenant VIP 👑\n"
    f"Le client peut maintenant recevoir des recharges de signaux.",
    parse_mode="Markdown"
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
    parse_mode="Markdown"
)
    except Exception:
        pass

async def abonnement_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.from_user.username
    if username != ADMIN_USERNAME:
        await update.message.reply_text("❌ Commande réservée à l'administrateur.")
        return

    sauvegarder_admin_id(update.message.from_user.id)

    if not context.args:
        await update.message.reply_text("Usage : `/abonnement CODE`\nEx: `/abonnement A3K9F2`", parse_mode="Markdown")
        return

    code_cible = context.args[0].upper()
    data = charger_users()
    data = migrer_si_besoin(data)
    uid_cible = next((uid for uid, u in data.items() if u.get("code") == code_cible), None)

    if uid_cible is None:
        await update.message.reply_text(f"❌ Aucun client trouvé avec le code `{code_cible}`.", parse_mode="Markdown")
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
        parse_mode="Markdown"
    )

    try:
        await context.bot.send_message(
            chat_id=int(uid_cible),
            text=(
                f"🎉 Ton abonnement *VIP* est activé ! 👑\n\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📅 Début : *{maintenant.strftime('%d/%m/%Y')}*\n"
                f"📆 Expire le : *{fin.strftime('%d/%m/%Y')}*\n"
                f"⏳ Durée : *30 jours*\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"Tape /start pour voir ton abonnement. 🚀"
            ),
            parse_mode="Markdown"
        )
    except Exception:
        pass

async def desactiver_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Retire le statut VIP (sans abonnement mensuel)."""
    username = update.message.from_user.username
    if username != ADMIN_USERNAME:
        await update.message.reply_text("❌ Commande réservée à l'administrateur.")
        return

    sauvegarder_admin_id(update.message.from_user.id)

    if not context.args:
        await update.message.reply_text("Usage : `/devip CODE`\nEx: `/devip A3K9F2`", parse_mode="Markdown")
        return

    code_cible = context.args[0].upper()
    data = charger_users()
    data = migrer_si_besoin(data)
    uid_cible = next((uid for uid, u in data.items() if u.get("code") == code_cible), None)

    if uid_cible is None:
        await update.message.reply_text(f"❌ Aucun client trouvé avec le code `{code_cible}`.", parse_mode="Markdown")
        return

    data[uid_cible]["vip"] = False
    sauvegarder_users(data)
    await update.message.reply_text(
        f"✅ Statut VIP retiré au client `{code_cible}`.\n"
        f"Il repasse en mode gratuit.",
        parse_mode="Markdown"
    )
    

async def desabonner_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Coupe l'abonnement mensuel (12 000 FCFA/mois)."""
    username = update.message.from_user.username
    if username != ADMIN_USERNAME:
        await update.message.reply_text("❌ Commande réservée à l'administrateur.")
        return

    sauvegarder_admin_id(update.message.from_user.id)

    if not context.args:
        await update.message.reply_text("Usage : `/desabo CODE`\nEx: `/desabo A3K9F2`", parse_mode="Markdown")
        return

    code_cible = context.args[0].upper()
    data = charger_users()
    data = migrer_si_besoin(data)
    uid_cible = next((uid for uid, u in data.items() if u.get("code") == code_cible), None)

    if uid_cible is None:
        await update.message.reply_text(f"❌ Aucun client trouvé avec le code `{code_cible}`.", parse_mode="Markdown")
        return

    data[uid_cible]["vip"] = False
    data[uid_cible].pop("vip_debut", None)
    data[uid_cible].pop("vip_fin", None)
    sauvegarder_users(data)
    await update.message.reply_text(
        f"✅ Abonnement mensuel coupé pour `{code_cible}`.\n"
        f"Il repasse en mode gratuit.",
        parse_mode="Markdown"
    )

    try:
        await context.bot.send_message(
            chat_id=int(uid_cible),
            text=(
                "⚠️ Ton abonnement mensuel VIP a été désactivé.\n\n"
                "Pour renouveler, contacte l'admin : @hacker_ci 💬"
            )
        )
    except Exception:
        pass

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.from_user.username
    if username != ADMIN_USERNAME:
        await update.message.reply_text("❌ Commande réservée à l'administrateur.")
        return

    sauvegarder_admin_id(update.message.from_user.id)

    data = charger_users()
    data = migrer_si_besoin(data)
    total = len(data)
    vips = sum(1 for u in data.values() if u.get("vip"))
    gratuits = total - vips

    # VIP qui expirent dans les 3 prochains jours
    bientot = []
    maintenant = datetime.datetime.now()
    for uid, user in data.items():
        if user.get("vip") and user.get("vip_fin"):
            jours = (datetime.datetime.fromtimestamp(user["vip_fin"]) - maintenant).days
            if 0 <= jours <= 3:
                bientot.append((user.get("code", "?"), jours))

    texte = (
        f"📊 Statistiques du bot :\n\n"
        f"👥 Total clients : {total}\n"
        f"👑 Membres VIP : {vips}\n"
        f"🆓 Membres gratuits : {gratuits}"
    )
    if bientot:
        texte += "\n\n⚠️ *Abonnements qui expirent bientôt :*"
        for code, j in bientot:
            texte += f"\n• `{code}` — expire dans {j} jour{'s' if j > 1 else ''}"

    await update.message.reply_text(texte, parse_mode="Markdown")

async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.from_user.username
    if username != ADMIN_USERNAME:
        await update.message.reply_text("❌ Commande réservée à l'administrateur.")
        return

    sauvegarder_admin_id(update.message.from_user.id)

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

async def bouton_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data, uid = get_ou_creer_user(user_id)
    user = data[uid]
    vip = user["vip"]
    restants = user["restants"]

    attente = get_secondes_restantes(user)
    if attente > 0:
        await query.message.reply_text(
            f"⏳ *Analyse en cours...*\n\n"
            f"Le bot calcule la prochaine côte.\n"
            f"Réessaie dans *{attente} seconde{'s' if attente > 1 else ''}*. 🔍",
            parse_mode="Markdown"
        )
        return

    if peut_obtenir_signal(user):
        consommer_signal(user_id)
        restants -= 1
        signal_txt, _ = generer_signal()
        statut_vip = "\n\n👑 Statut : VIP" if vip else ""

        if restants > 0:
            texte = f"{signal_txt}{statut_vip}\n\n⚡ Il te reste *{restants}* signal{'s' if restants > 1 else ''} gratuit{'s' if restants > 1 else ''}."
            markup = bouton_signal(restants=restants, vip=vip)
        else:
            texte = f"{signal_txt}{statut_vip}\n\n⚠️ *Dernier signal utilisé !*\nRecharge tes signaux pour continuer."
            markup = bouton_vip()

        msg = await query.message.reply_text(texte, reply_markup=markup, parse_mode="Markdown")
        sauvegarder_message_id(user_id, msg.message_id)

    else:
        msg = await query.message.reply_text(
            "🔒 Tu n'as plus de signaux disponibles.\n\n"
            "💎 Contacte l'admin pour recharger tes signaux.",
            reply_markup=bouton_vip()
        )

        sauvegarder_message_id(user_id, msg.message_id)

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

async def effacer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        await query.message.delete()
    except Exception:
        pass

async def verifier_expirations(context):
    """Job quotidien : vérifie les VIP expirés et notifie l'admin."""
    admin_id = get_admin_id()
    if not admin_id:
        return

    data = charger_users()
    maintenant = datetime.datetime.now()
    expires = []

    for uid, user in data.items():
        if user.get("vip") and user.get("vip_fin"):
            fin = datetime.datetime.fromtimestamp(user["vip_fin"])
            jours_restants = (fin - maintenant).days
            if jours_restants < 0:
                expires.append((uid, user.get("code", "?"), formater_date(user["vip_fin"])))

    for uid, code, date_fin in expires:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    f"⚠️ *Abonnement VIP expiré !*\n\n"
                    f"🔑 Code client : `{code}`\n"
                    f"📆 Date d'expiration : *{date_fin}*\n\n"
                    f"👉 Utilise `/devip {code}` pour couper l'accès."
                ),
                parse_mode="Markdown"
            )
        except Exception:
            pass

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot en ligne")
    def log_message(self, format, *args):
        pass

def lancer_serveur():
    server = HTTPServer(("0.0.0.0", 8080), HealthHandler)
    server.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=lancer_serveur, daemon=True).start()
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clean", clean))
    app.add_handler(CommandHandler("moncode", mon_code))
    app.add_handler(CommandHandler("recharge", recharger))
    app.add_handler(CommandHandler("clients", clients))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("vip", activer_vip_cmd))
    app.add_handler(CommandHandler("abonnement", abonnement_cmd))
    app.add_handler(CommandHandler("devip", desactiver_vip_cmd))
    app.add_handler(CommandHandler("admin", admin_help))
    app.add_handler(CallbackQueryHandler(bouton_callback, pattern="^signal$"))
    app.add_handler(CallbackQueryHandler(vip_callback, pattern="^vip$"))
    app.add_handler(CallbackQueryHandler(effacer_callback, pattern="^effacer$"))

    # Vérification quotidienne des abonnements expirés (toutes les 24h)
    app.job_queue.run_repeating(verifier_expirations, interval=86400, first=60)

    print("Bot démarré...")
    app.run_polling()
