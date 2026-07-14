import os
import time
import json
import urllib.parse
import threading
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, ReplyKeyboardRemove
import cv2
import base64
import requests
import gspread
from flask import Flask, send_from_directory

# 🔑 API KEYS & IDs
TELEGRAM_TOKEN = "8746190544:AAFWhqcV7Pv453JYYb8BZAu1g4fYkqr8DEc"
GEMINI_API_KEY = "AQ.Ab8RN6KlCOPMSMBTol4rf26ikMimfoQt5p34vlGl-6kZ1fvhRQ"
ADMIN_CHANNEL_ID = -1004335119150

# ⚠️ यहाँ अपने RENDER APP का नाम डालेंगे (स्टेप 3 के बाद)
# उदाहरण: "https://ldc-bot-xyz.onrender.com"
RENDER_APP_URL = "https://your-app-name.onrender.com" 

# 📍 RELATIVE PATHS (Render के लिए बेस्ट)
USER_DIR = "./"
JSON_FILE_NAME = USER_DIR + "ldc-bot-2026-d5331fea6f74.json"
DB_FILE = USER_DIR + 'users_db.json'
KEYS_FILE = USER_DIR + 'answer_keys.json'

bot = telebot.TeleBot(TELEGRAM_TOKEN)
app = Flask(__name__)

# --- 🌐 FLASK WEB SERVER (Render और OMR HTML के लिए) ---
@app.route('/')
def index():
    return "<h1>✅ LDC Bot is Live on Render!</h1>"

@app.route('/omr.html')
def serve_omr():
    # यह आपकी omr.html फाइल को इंटरनेट पर दिखाएगा
    return send_from_directory(USER_DIR, 'omr.html')

# --- 💾 DATABASE FUNCTIONS ---
def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r') as f: return json.load(f)
    return {}

def save_db(data):
    with open(DB_FILE, 'w') as f: json.dump(data, f, indent=4)

def load_answer_keys():
    if os.path.exists(KEYS_FILE):
        with open(KEYS_FILE, 'r') as f: return json.load(f)
    return {
        "Paper 1": {"Set_A": {str(i): "A" for i in range(1, 151)}, "Set_B": {str(i): "B" for i in range(1, 151)}},
        "Paper 2": {"Set_A": {str(i): "C" for i in range(1, 151)}, "Set_B": {str(i): "D" for i in range(1, 151)}}
    }

user_states = {}

def get_area_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("🟢 TSP (अनुसूचित)", callback_data="area_TSP"), InlineKeyboardButton("🔵 NTSP (गैर-अनुसूचित)", callback_data="area_NTSP"))
    return markup

def get_dashboard_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("📝 पेपर 1 OMR चेक करें", callback_data="action_paper1"))
    markup.row(InlineKeyboardButton("📝 पेपर 2 OMR चेक करें", callback_data="action_paper2"))
    markup.row(InlineKeyboardButton("📊 मेरी रैंकिंग (Report Card)", callback_data="action_ranking"))
    markup.row(InlineKeyboardButton("👤 मेरा प्रोफाइल", callback_data="action_profile"))
    return markup

def calculate_marks(student_answers, paper_no):
    all_keys = load_answer_keys()
    paper_keys = all_keys.get(paper_no, {})
    best_set = None; highest_correct = -1; final_marks = 0
    stats = {"correct": 0, "wrong": 0, "blank": 0, "e_opt": 0}
    if not paper_keys: return "Unknown Set", 0, stats

    for set_name, key_answers in paper_keys.items():
        marks = 0.0; c = 0; w = 0; b = 0; e = 0
        for i in range(1, 151):
            q_str = str(i)
            s_ans = student_answers.get(q_str, "BLANK").upper()
            c_ans = key_answers.get(q_str, "A")
            if s_ans == c_ans: marks += (2/3); c += 1
            elif s_ans == 'E': e += 1
            elif s_ans in ['BLANK', '']: marks -= (1/3); b += 1
            else: marks -= (1/3); w += 1
        
        if c > highest_correct:
            highest_correct = c; best_set = set_name; final_marks = marks
            stats = {"correct": c, "wrong": w, "blank": b, "e_opt": e}
    return best_set, final_marks, stats

def ask_gemini(image_path, prompt):
    with open(image_path, "rb") as image_file:
        img_data = base64.b64encode(image_file.read()).decode("utf-8")
    payload = {"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "image/jpeg", "data": img_data}}]}]}
    headers = {"Content-Type": "application/json"}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    response = requests.post(url, json=payload, headers=headers)
    if response.status_code == 200: return response.json()['candidates'][0]['content']['parts'][0]['text'].strip()
    return "Error"

# --- 🚀 BOT HANDLERS ---
@bot.message_handler(commands=['update_keys'])
def update_keys_command(message):
    chat_id = str(message.chat.id)
    bot.send_message(chat_id, "⏳ Google Sheet से नई Answer Keys सिंक की जा रही हैं...")
    try:
        client = gspread.service_account(filename=JSON_FILE_NAME)
        sheet = client.open("LDC Final Result").worksheet("Answer Keys")
        all_rows = sheet.get_all_values()
        if len(all_rows) <= 1:
            bot.send_message(chat_id, "⚠️ 'Answer Keys' शीट खाली है।")
            return
        headers = all_rows[0]
        new_keys = {}
        for row in all_rows[1:]:
            if len(row) < 3 or not row[0].strip() or not row[1].strip(): continue
            paper_name, q_num = row[0].strip(), row[1].strip()
            if paper_name not in new_keys: new_keys[paper_name] = {}
            for idx, ans in enumerate(row[2:]):
                set_name = headers[idx+2].strip()
                if set_name not in new_keys[paper_name]: new_keys[paper_name][set_name] = {}
                new_keys[paper_name][set_name][q_num] = ans.strip().upper()
        with open(KEYS_FILE, 'w') as f: json.dump(new_keys, f, indent=4)
        bot.send_message(chat_id, f"✅ **Answer Keys अपडेट हो गईं!**")
    except Exception as e:
        bot.send_message(chat_id, f"❌ सिंक फेल: {str(e)}")

@bot.message_handler(commands=['start'])
def send_welcome(message):
    chat_id = str(message.chat.id)
    db = load_db()
    if chat_id in db and "name" in db[chat_id]:
        user_states[chat_id] = "DASHBOARD"
        bot.send_message(message.chat.id, f"🌟 **वापसी पर स्वागत है, {db[chat_id]['name']}!**", reply_markup=get_dashboard_keyboard())
    else:
        user_states[chat_id] = "WAITING_FOR_ADMIT_CARD"
        bot.send_message(message.chat.id, "नमस्ते! LDC सिस्टम में आपका स्वागत है। 🎓\nकृपया अपना **एडमिट कार्ड** भेजें।")

@bot.message_handler(content_types=['photo'])
def handle_photos(message):
    chat_id = str(message.chat.id)
    state = user_states.get(chat_id, "")
    try: bot.forward_message(ADMIN_CHANNEL_ID, message.chat.id, message.message_id)
    except: pass

    image_path = USER_DIR + f"img_{chat_id}.jpg"
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        with open(image_path, 'wb') as new_file: new_file.write(downloaded_file)

        if state == "WAITING_FOR_ADMIT_CARD":
            bot.send_message(chat_id, "⏳ एडमिट कार्ड स्कैन हो रहा है...")
            img = cv2.imread(image_path)
            detector = cv2.QRCodeDetector()
            qr_data, _, _ = detector.detectAndDecode(img)
            if not qr_data:
                bot.send_message(chat_id, "❌ QR कोड नहीं मिला। साफ़ फोटो भेजें।")
                return
            roll_no, name, fname = "N/A", "N/A", "N/A"
            for line in qr_data.split('\n'):
                if "Roll No:" in line: roll_no = line.split("Roll No:")[1].strip()
                if "Candidate Name:" in line: name = line.split("Candidate Name:")[1].strip()
                if "Father Name:" in line: fname = line.split("Father Name:")[1].strip()
            prompt = 'Is admit card se Category (GEN, OBC, SC, ST, EWS) aur Gender (MALE/FEMALE) JSON format me do: {"category": "...", "gender": "..."}'
            ai_data = ask_gemini(image_path, prompt)
            try:
                j_data = json.loads(ai_data.replace("```json", "").replace("```", "").strip())
                category, gender = j_data.get("category", "N/A"), j_data.get("gender", "N/A")
            except: category, gender = "N/A", "N/A"
            db = load_db()
            db[chat_id] = {"roll_no": roll_no, "name": name, "fname": fname, "category": category, "gender": gender}
            save_db(db)
            user_states[chat_id] = "WAITING_FOR_AREA"
            bot.send_message(chat_id, f"✅ डिटेल्स मिल गईं!\n👤 नाम: {name}\nकृपया अपना एरिया चुनें:", reply_markup=get_area_keyboard())

        elif state in ["WAITING_OMR_PHOTO_PAPER1", "WAITING_OMR_PHOTO_PAPER2"]:
            paper_no = "Paper 1" if "PAPER1" in state else "Paper 2"
            bot.send_message(chat_id, f"⏳ {paper_no} की OMR स्कैन की जा रही है...")
            prompt = """इस OMR शीट को पढ़ो। 1 से 150 तक के भरे हुए ऑप्शंस (A, B, C, D, E) JSON में दो: {"1": "A", "2": "C"...}. खाली को 'BLANK' लिखो। फालतू टेक्स्ट मत देना।"""
            ai_response = ask_gemini(image_path, prompt)
            clean_json = ai_response.replace("```json", "").replace("```", "").strip()
            try:
                student_answers = json.loads(clean_json)
                encoded_data = urllib.parse.quote(json.dumps(student_answers))
                
                # 🟢 RENDER URL का उपयोग करके Web App सेट करना
                web_app_url = f"{RENDER_APP_URL}/omr.html?data={encoded_data}"
                
                markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
                btn = KeyboardButton(text="📝 OMR वेरिफाई करें", web_app=WebAppInfo(url=web_app_url))
                markup.add(btn)
                bot.send_message(chat_id, f"✅ **{paper_no} स्कैन हो गई है!**\n👇 नीचे बटन दबाकर अपनी OMR मिला लें और सबमिट करें।", reply_markup=markup)
            except:
                bot.send_message(chat_id, "⚠️ AI OMR को नहीं पढ़ पाया। साफ़ फोटो भेजें।")
    except: pass
    finally:
        if os.path.exists(image_path): os.remove(image_path)

@bot.message_handler(content_types=['web_app_data'])
def handle_web_app_data(message):
    chat_id = str(message.chat.id)
    try:
        student_answers = json.loads(message.web_app_data.data)
        state = user_states.get(chat_id, "")
        paper_no = "Paper 1" if "PAPER1" in state else "Paper 2"
        bot.send_message(chat_id, "⏳ रिज़ल्ट कैलकुलेट किया जा रहा है...", reply_markup=ReplyKeyboardRemove())
        best_set, final_marks, stats = calculate_marks(student_answers, paper_no)
        f_marks_str = str(round(final_marks, 2))
        msg = (f"🎉 **{paper_no} का रिज़ल्ट**\n━━━━━━━━━━━━━━━━━━\n📌 **सेट:** {best_set}\n✅ **सही:** {stats['correct']} | ❌ **गलत:** {stats['wrong']}\n⚠️ **खाली:** {stats['blank']} | 🔵 **E:** {stats['e_opt']}\n━━━━━━━━━━━━━━━━━━\n🏆 **अंक:** {f_marks_str} / 100\n")
        bot.send_message(chat_id, msg)
        try:
            bot.send_message(chat_id, "⏳ डेटाबेस में आपके अंक सुरक्षित किए जा रहे हैं...")
            client = gspread.service_account(filename=JSON_FILE_NAME)
            sheet = client.open("LDC Final Result").sheet1
            all_vals = sheet.get_all_values()
            row_idx = None
            for i, row in enumerate(all_vals):
                if row and row[0] == chat_id:
                    row_idx = i + 1
                    break
            db = load_db()
            u = db.get(chat_id, {})
            p1_set, p1_marks, p2_set, p2_marks, total_marks = "", "", "", "", ""
            if row_idx:
                if paper_no == "Paper 1":
                    p1_set, p1_marks = best_set, f_marks_str
                    p2_set = all_vals[row_idx-1][9] if len(all_vals[row_idx-1]) > 9 else ""
                    p2_marks = all_vals[row_idx-1][10] if len(all_vals[row_idx-1]) > 10 else ""
                else:
                    p1_set = all_vals[row_idx-1][7] if len(all_vals[row_idx-1]) > 7 else ""
                    p1_marks = all_vals[row_idx-1][8] if len(all_vals[row_idx-1]) > 8 else ""
                    p2_set, p2_marks = best_set, f_marks_str
                if p1_marks and p2_marks: total_marks = str(round(float(p1_marks) + float(p2_marks), 2))
                sheet.update_cell(row_idx, 8, p1_set); sheet.update_cell(row_idx, 9, p1_marks); sheet.update_cell(row_idx, 10, p2_set); sheet.update_cell(row_idx, 11, p2_marks); sheet.update_cell(row_idx, 12, total_marks)
            else:
                if paper_no == "Paper 1": p1_set, p1_marks = best_set, f_marks_str
                else: p2_set, p2_marks = best_set, f_marks_str
                row_data = [chat_id, u.get("roll_no"), u.get("name"), u.get("fname"), u.get("gender"), u.get("category"), u.get("area"), p1_set, p1_marks, p2_set, p2_marks, total_marks]
                sheet.append_row(row_data)
            bot.send_message(chat_id, "💾 **आपके अंक डेटाबेस में सुरक्षित सेव हो गए हैं!**")
        except Exception: pass
        user_states[chat_id] = "DASHBOARD"
        bot.send_message(chat_id, "मुख्य मेनू:", reply_markup=get_dashboard_keyboard())
    except Exception as e:
        bot.send_message(chat_id, f"⚠️ एरर: {str(e)}", reply_markup=ReplyKeyboardRemove())

@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    chat_id = str(call.message.chat.id)
    data = call.data
    if data.startswith("area_"):
        area = data.split("_")[1]
        db = load_db()
        db[chat_id]["area"] = area
        save_db(db)
        bot.edit_message_text(f"✅ एरिया **{area}** सेट कर दिया गया है。", chat_id, call.message.message_id)
        user_states[chat_id] = "DASHBOARD"
        bot.send_message(chat_id, "🎉 **अकाउंट बन गया!** डैशबोर्ड:", reply_markup=get_dashboard_keyboard())
    elif data == "action_profile":
        db = load_db()
        u = db.get(chat_id, {})
        bot.send_message(chat_id, f"👤 **आपका प्रोफाइल**\n━━━━━━━━━━━━━━\n**नाम:** {u.get('name')}\n**पिता का नाम:** {u.get('fname')}\n**जेंडर:** {u.get('gender')}\n**कैटेगरी:** {u.get('category')}\n**क्षेत्र:** {u.get('area')}")
    elif data in ["action_paper1", "action_paper2"]:
        p_name = "पेपर 1" if data == "action_paper1" else "पेपर 2"
        user_states[chat_id] = "WAITING_OMR_PHOTO_PAPER1" if data == "action_paper1" else "WAITING_OMR_PHOTO_PAPER2"
        bot.send_message(chat_id, f"📸 कृपया अपनी **{p_name} की OMR शीट** की बिल्कुल साफ़ फोटो भेजें।")
    elif data == "action_ranking":
        bot.send_message(chat_id, "⏳ रैंकिंग कैलकुलेट की जा रही है...")
        try:
            client = gspread.service_account(filename=JSON_FILE_NAME)
            sheet = client.open("LDC Final Result").sheet1
            all_vals = sheet.get_all_values()
            if len(all_vals) <= 1:
                bot.send_message(chat_id, "⚠️ अभी तक डेटाबेस में कोई रिज़ल्ट नहीं है।")
                return
            valid_students = []
            for s in all_vals[1:]:
                while len(s) < 12: s.append("") 
                try: valid_students.append({"chat_id": s[0], "roll_no": s[1], "name": s[2], "gender": s[4], "category": s[5], "area": s[6], "p1": s[8], "p2": s[10], "total": float(s[11])})
                except: pass
            if not valid_students:
                bot.send_message(chat_id, "⚠️ रैंकिंग के लिए दोनों पेपर पूरे नहीं हुए हैं।")
                return
            valid_students.sort(key=lambda x: x["total"], reverse=True)
            user_data = next((item for item in valid_students if item["chat_id"] == chat_id), None)
            if not user_data:
                bot.send_message(chat_id, "⚠️ **रैंकिंग उपलब्ध नहीं है!**\nरैंकिंग के लिए **पेपर 1 और 2** दोनों की OMR स्कैन करें।")
                return
            overall_rank = valid_students.index(user_data) + 1
            cat_list = [s for s in valid_students if s["category"] == user_data["category"]]
            cat_rank = cat_list.index(user_data) + 1
            gender_cat_list = [s for s in cat_list if s["gender"] == user_data["gender"]]
            gender_cat_rank = gender_cat_list.index(user_data) + 1
            area_list = [s for s in valid_students if s["area"] == user_data["area"]]
            area_rank = area_list.index(user_data) + 1
            bot.send_message(chat_id, f"🏆 **LDC रिपोर्ट कार्ड** 🏆\n━━━━━━━━━━━━━━━━━━\n👤 **नाम:** {user_data['name']}\n🆔 **रोल नंबर:** {user_data['roll_no']}\n━━━━━━━━━━━━━━━━━━\n📝 **पेपर 1:** {user_data['p1']} / 100\n📝 **पेपर 2:** {user_data['p2']} / 100\n🔥 **कुल अंक:** {user_data['total']} / 200\n━━━━━━━━━━━━━━━━━━\n📊 **आपकी लाइव रैंकिंग**\n🥇 **ओवरऑल:** {overall_rank} / {len(valid_students)}\n🎯 **कैटेगरी ({user_data['category']}):** {cat_rank} / {len(cat_list)}\n⚧️ **जेंडर ({user_data['gender']}-{user_data['category']}):** {gender_cat_rank} / {len(gender_cat_list)}\n🌍 **क्षेत्र ({user_data['area']}):** {area_rank} / {len(area_list)}\n━━━━━━━━━━━━━━━━━━")
        except: bot.send_message(chat_id, "⚠️ रैंकिंग सिस्टम में तकनीकी समस्या आ गई है।")

# 🟢 BOT को बैकग्राउंड में चलाने का फंक्शन (बिना वेबहुक के)
def run_bot():
    print("🤖 Telegram Bot Started...")
    bot.remove_webhook()
    time.sleep(2)
    bot.polling(none_stop=True, timeout=60, long_polling_timeout=60)

if __name__ == "__main__":
    # 1. बोट को एक अलग थ्रेड में चालू करें
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.start()
    
    # 2. Flask सर्वर चालू करें (Render को यह चाहिए होता है)
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
