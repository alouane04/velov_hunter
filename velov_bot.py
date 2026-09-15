import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import time
import threading
from dotenv import load_dotenv
import os
import math
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut

load_dotenv()

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
JCDECAUX_API_KEY = os.environ.get("JCDECAUX_API_KEY")
CONTRACT = os.environ.get("JCDECAUX_CONTRACT", "lyon")

CYCLOCITY_AUTH = "Taknv1 eyJhbGciOiJSUzI1NiIsInppcCI6IkRFRiJ9.eJzNmM2O2jAQx9_F5zTqqqu2m3MfoOq16sFxBjD4I_UHECHevXZIILTxEi_e0FMcT8bm_9PMeMwBaVuiAm2ZzndQ5qyRovj-4xvKELaVM1glCoM34kMlOaaiIA1hsvsC9jUqnr58fXn--Onz80uGtqCcy5Mz1Yqj4oCI5PmaVECw3eetK6GmyQlWlfZ2AwIVPxEBtWycV2VLRoUbGGmZtBrccEuZoFb7EWYgiJ8TWJCmfXKr3LPfoqQbb2ZWVO7BLVt1ixjZYI7doATtfKXfg1HGQBsluXvRsPWvp7UN6HaVPfBSWrX0L2v309ZYtGsoZ_G-HhKnINqvm3ZRjhUtpUK_MqQka7V5rYUCXPlJUrvJw_GYBdFIYRQmJh5PD2WIaS48QyRDVGc8PZcO1ABPr3c6ogX-PUbnQmKA54wsMZ0LkCtOV1DOqG7SCQaPlzodTCWJ5SBej51RTIlTK0DnjbETpHPWWxDmvjWTIHkh8YD-DZ-_wPS87kqu8fC5WXuCydVqnR4_eiXrMTSj5eaVzLoLTdrMCqJptUagMdhQKWAbSK8RLrNHT-r0utI8HZVcLEDFh9E8JSh1hp3Exh3viSr0e2RZ8uank3sClA3OM7cX7CLAKdgFmsb_IaxSc-vUTseDCZH2VmmaoS9Ke7AF-fRy-4M_iydGK-dIDR1tBsZIvVunnbZbCpaqi-CYdqlWkoDWo5RiI2u-luntlM568xobssrdpZcVO0WN32zU2JWzgE27DnUSaF8OH33nmykS4yp_jdWGiuXDA3Cm0t_LjQCkKHEej24pZkvQVm106x7ftT-yI73jYnOSO7j6Zf1cREwFLzqz_kWXtokPplzghnM8_gHMnj_c.cvKI7wcPUP4AhiEbqXYtGoQkh3G0Sz8vBJvZO-d9tmNzMftNW2MbwJ80x1npy7lk0FQ6ArWRo0MLi2NN1JInCoR_LSBJ2PyPcx92a1qhmBOR1AkMl4RjQUFb8JUWuyGTtWqmh1U6az9JS40UWT76XmEaTQSZZSlU3UsUvgvSxyR0GzQ5tn6SGUnAVpm_rR-FfHYWu3ZFImIbEMYlIiK5HpNY6SEoRvzHWUOITBBDjlJWOSpuu0sATycSGDQKrG5aT5lzNljcwqa6qpbYM83lonnaj2UnmCvnBBIB74ztLmwVhlfVWYsM4mB5XcA1I11Ua09XD1JJs8OqqgcqgEM3nQ"

DEFAULT_TARGET_STATIONS = [5030, 5008, 5047, 5016, 5007] 

# Initialize the bot
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
geolocator = Nominatim(user_agent="velov_telegram_bot_v2")

# Keep track of active hunts: chat_id -> dict {'stop_event': Event, 'interval': int}
active_hunts = {}

# Cache for station names and positions so we don't query JCDecaux every time
STATION_CACHE = {}

def update_station_cache():
    url = f"https://api.jcdecaux.com/vls/v3/stations?contract={CONTRACT}&apiKey={JCDECAUX_API_KEY}"
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            for st in res.json():
                STATION_CACHE[st['number']] = st
            print(f"✅ Cached {len(STATION_CACHE)} stations from JCDecaux")
    except Exception as e:
        print("Failed to cache stations:", e)

# Initial cache load
update_station_cache()

def get_distance(lat1, lon1, lat2, lon2):
    R = 6371.0 
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def get_walking_distance(lat1, lon1, lat2, lon2):
    url = f"http://router.project-osrm.org/route/v1/foot/{lon1},{lat1};{lon2},{lat2}?overview=false"
    try:
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if data.get('code') == 'Ok':
                return data['routes'][0]['distance'] / 1000.0 
    except Exception:
        pass
    return get_distance(lat1, lon1, lat2, lon2) * 1.3

def find_closest_stations(lat, lon, num_stations=5):
    if not STATION_CACHE:
        update_station_cache()
    
    stations = list(STATION_CACHE.values())
    for st in stations:
        st_pos = st.get('position', {})
        st_lat = st_pos.get('latitude')
        st_lon = st_pos.get('longitude')
        if st_lat and st_lon:
            st['distance'] = get_distance(lat, lon, st_lat, st_lon)
        else:
            st['distance'] = float('inf')
            
    stations.sort(key=lambda x: x['distance'])
    top_candidates = stations[:10]
    
    for st in top_candidates:
        st_pos = st.get('position', {})
        st['walk_distance'] = get_walking_distance(lat, lon, st_pos.get('latitude'), st_pos.get('longitude'))
        
    top_candidates.sort(key=lambda x: x['walk_distance'])
    for st in top_candidates:
        st['distance'] = st['walk_distance']
    
    filtered_stations = [st for st in top_candidates if st['walk_distance'] <= 1.0]
    if not filtered_stations and top_candidates:
        filtered_stations = [top_candidates[0]]
        
    closest_n = filtered_stations[:num_stations]
    return [st['number'] for st in closest_n], closest_n

def stop_hunt_for_chat(chat_id):
    if chat_id in active_hunts:
        active_hunts[chat_id]['stop_event'].set()
        del active_hunts[chat_id]
        return True
    return False

def fetch_and_check_stations(chat_id, target_stations, hunt_state):
    bot.send_message(chat_id, f"🕵️‍♂️ **Hunt started!** Checking {len(target_stations)} stations for electric bikes. Send /stop to cancel.", parse_mode="Markdown")
    
    end_time = time.time() + (30 * 60)
    notified_stations = set()
    stop_event = hunt_state['stop_event']

    while time.time() < end_time and not stop_event.is_set():
        for station_id in target_stations:
            if stop_event.is_set():
                break
                
            url = f"https://api.jcdecaux.com/vls/v3/stations/{station_id}?contract={CONTRACT}&apiKey={JCDECAUX_API_KEY}&_={int(time.time()*1000)}"
            try:
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    station = response.json()
                    
                    main_stands = station.get("mainStands", {}).get("availabilities", {})
                    electrical = main_stands.get("electricalBikes", 0)
                    mechanical = main_stands.get("mechanicalBikes", 0)
                    total_bikes = station.get("available_bikes", main_stands.get("bikes", 0))
                    
                    cached_st = STATION_CACHE.get(station_id, {})
                    name = cached_st.get("name", f"Station {station_id}")
                    lat = cached_st.get("position", {}).get("latitude")
                    lon = cached_st.get("position", {}).get("longitude")
                    
                    if electrical > 0 and station_id not in notified_stations:
                        msg = (
                            f"🚲 **ELECTRIC BIKE FOUND AT {name}**\n\n"
                            f"⚡ Electric: **{electrical}**\n"
                            f"⚙️ Mechanical: {mechanical}\n"
                            f"Total Available: {total_bikes}\n"
                        )
                        
                        markup = InlineKeyboardMarkup()
                        
                        btn_row1 = []
                        if lat and lon:
                            maps_url = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
                            btn_row1.append(InlineKeyboardButton("🗺️ Map", url=maps_url))
                        btn_row1.append(InlineKeyboardButton("🛑 Stop", callback_data="stop_hunt"))
                        markup.add(*btn_row1)
                        
                        # Snipe button on its own row
                        markup.add(InlineKeyboardButton("🎯 Snipe Mode (Check every 3s)", callback_data="snipe_mode"))
                        
                        bot.send_message(chat_id, msg, parse_mode="Markdown", reply_markup=markup)
                        notified_stations.add(station_id)
                        
                    elif electrical == 0 and station_id in notified_stations:
                        msg = f"Station {name} has no electric bikes anymore."
                        bot.send_message(chat_id, msg, parse_mode="Markdown")
                        notified_stations.remove(station_id)
            except Exception as e:
                print(f"Error fetching data for {station_id}: {e}")
                
        # Wait using the dynamically adjustable interval
        stop_event.wait(hunt_state['interval'])
        
    if not stop_event.is_set():
        bot.send_message(chat_id, "🏁 **30-minute monitoring complete.** Send /findbikes or a location to start again.", parse_mode="Markdown")
        if chat_id in active_hunts:
            del active_hunts[chat_id]

def start_hunt(chat_id, target_stations):
    stop_hunt_for_chat(chat_id)
    stop_event = threading.Event()
    hunt_state = {'stop_event': stop_event, 'interval': 15}
    active_hunts[chat_id] = hunt_state
    
    monitor_thread = threading.Thread(target=fetch_and_check_stations, args=(chat_id, target_stations, hunt_state))
    monitor_thread.start()

# --- BOT MESSAGE HANDLERS ---

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "Hello! I am your Vélo'v tracker.\n- Send /findbikes for default stations.\n- Send a location attachment or type an address to find bikes nearby.\n- Send /stop to cancel a hunt.")

@bot.message_handler(commands=['stop'])
def cmd_stop(message):
    if stop_hunt_for_chat(message.chat.id):
        bot.reply_to(message, "🛑 Hunt stopped. Send /findbikes to start a new one.")
    else:
        bot.reply_to(message, "No active hunt to stop.")

@bot.message_handler(commands=['findbikes'])
def trigger_hunt(message):
    start_hunt(message.chat.id, DEFAULT_TARGET_STATIONS)

@bot.message_handler(content_types=['location'])
def handle_location(message):
    lat = message.location.latitude
    lon = message.location.longitude
    bot.reply_to(message, "📍 Location received! Finding closest stations...")
    
    stations_ids, stations_info = find_closest_stations(lat, lon)
    if stations_ids:
        station_names = [f"- {st['name']} ({st['distance']:.2f}km)" for st in stations_info]
        bot.send_message(message.chat.id, "Targeting these stations:\n" + "\n".join(station_names))
        start_hunt(message.chat.id, stations_ids)
    else:
        bot.reply_to(message, "Could not find stations nearby.")

@bot.message_handler(func=lambda message: True, content_types=['text'])
def handle_text(message):
    text = message.text.strip()
    if text.startswith('/'):
        return
        
    bot.reply_to(message, f"🔍 Searching for address: {text}...")
    try:
        location = geolocator.geocode(f"{text}, Lyon, France", timeout=10)
        if location:
            bot.send_message(message.chat.id, f"📍 Found: {location.address}\nFinding closest stations...")
            stations_ids, stations_info = find_closest_stations(location.latitude, location.longitude)
            if stations_ids:
                station_names = [f"- {st['name']} ({st['distance']:.2f}km)" for st in stations_info]
                bot.send_message(message.chat.id, "Targeting these stations:\n" + "\n".join(station_names))
                start_hunt(message.chat.id, stations_ids)
            else:
                bot.send_message(message.chat.id, "Could not find stations nearby.")
        else:
            bot.send_message(message.chat.id, "❌ Address not found. Try sending a location attachment or a more specific address.")
    except Exception as e:
        bot.send_message(message.chat.id, f"Error geocoding: {e}")

@bot.callback_query_handler(func=lambda call: call.data in ["stop_hunt", "snipe_mode"])
def callback_inline(call):
    chat_id = call.message.chat.id
    if call.data == "stop_hunt":
        if stop_hunt_for_chat(chat_id):
            bot.answer_callback_query(call.id, "Hunt stopped!")
            bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
            bot.send_message(chat_id, "🛑 Hunt stopped successfully. Send /findbikes to start a new one.")
        else:
            bot.answer_callback_query(call.id, "Hunt already stopped.")
            bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
            
    elif call.data == "snipe_mode":
        if chat_id in active_hunts:
            active_hunts[chat_id]['interval'] = 3
            bot.answer_callback_query(call.id, "🎯 Snipe Mode activated! Checking every 3s.")
            # Remove the snipe button so they don't click it again
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🛑 Stop", callback_data="stop_hunt"))
            bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=markup)
            bot.send_message(chat_id, "🎯 **Snipe Mode Activated**\nChecking every 3 seconds! Go go go!", parse_mode="Markdown")
        else:
            bot.answer_callback_query(call.id, "No active hunt to snipe.", show_alert=True)

if __name__ == "__main__":
    print("🤖 Bot is running and waiting for commands...")
    bot.infinity_polling(timeout=60, long_polling_timeout=60)
