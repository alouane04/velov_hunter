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

DEFAULT_TARGET_STATIONS = [5030, 5008, 5047, 5016, 5007] 

# Initialize the bot
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
geolocator = Nominatim(user_agent="velov_telegram_bot_v2")

# Keep track of active hunts: chat_id -> threading.Event
active_hunts = {}

def get_distance(lat1, lon1, lat2, lon2):
    """Calculate the great circle distance between two points on the earth."""
    # Haversine formula
    R = 6371.0 # Earth radius in kilometers
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    distance = R * c
    return distance

def get_walking_distance(lat1, lon1, lat2, lon2):
    """Get actual walking distance in km using OSRM API."""
    url = f"http://router.project-osrm.org/route/v1/foot/{lon1},{lat1};{lon2},{lat2}?overview=false"
    try:
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if data.get('code') == 'Ok':
                return data['routes'][0]['distance'] / 1000.0 # convert to km
    except Exception:
        pass
    # Fallback to haversine * 1.3 (approx road factor) if OSRM fails
    return get_distance(lat1, lon1, lat2, lon2) * 1.3

def find_closest_stations(lat, lon, num_stations=5):
    """Fetch all stations and find the N closest ones to the given coords."""
    url = f"https://api.jcdecaux.com/vls/v3/stations?contract={CONTRACT}&apiKey={JCDECAUX_API_KEY}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            stations = response.json()
            # Calculate distance for each
            for st in stations:
                st_pos = st.get('position', {})
                st_lat = st_pos.get('latitude')
                st_lon = st_pos.get('longitude')
                if st_lat and st_lon:
                    st['distance'] = get_distance(lat, lon, st_lat, st_lon)
                else:
                    st['distance'] = float('inf')
            
            # Sort by straight-line distance first
            stations.sort(key=lambda x: x['distance'])
            
            # Take top 10 to check actual walking distance via OSRM to avoid spamming the API
            top_candidates = stations[:10]
            for st in top_candidates:
                st['walk_distance'] = get_walking_distance(lat, lon, st['position']['latitude'], st['position']['longitude'])
                
            # Re-sort by actual walking distance
            top_candidates.sort(key=lambda x: x['walk_distance'])
            
            # Override 'distance' so it displays the walking distance in the chat
            for st in top_candidates:
                st['distance'] = st['walk_distance']
            
            # Filter out stations more than ~12 mins walk away (e.g. 1.0 km actual walk)
            filtered_stations = [st for st in top_candidates if st['walk_distance'] <= 1.0]
            
            # If there are NO stations within 1km walk, at least give them the 1 closest station
            if not filtered_stations and top_candidates:
                filtered_stations = [top_candidates[0]]
                
            closest_n = filtered_stations[:num_stations]
            return [st['number'] for st in closest_n], closest_n
    except Exception as e:
        print(f"Error fetching stations for distance: {e}")
    return DEFAULT_TARGET_STATIONS, []

def stop_hunt_for_chat(chat_id):
    """Stop the hunt for a given chat_id, returns True if a hunt was stopped."""
    if chat_id in active_hunts:
        active_hunts[chat_id].set()
        del active_hunts[chat_id]
        return True
    return False

def fetch_and_check_stations(chat_id, target_stations, stop_event):
    """The background task that runs for 30 minutes, checking individual stations."""
    bot.send_message(chat_id, f"🕵️‍♂️ **Hunt started!** Checking {len(target_stations)} stations every 15 seconds for 30 minutes. Send /stop to cancel.", parse_mode="Markdown")
    
    end_time = time.time() + (30 * 60)
    notified_stations = set()

    while time.time() < end_time and not stop_event.is_set():
        for station_id in target_stations:
            if stop_event.is_set():
                break
                
            url = f"https://api.jcdecaux.com/vls/v3/stations/{station_id}?contract={CONTRACT}&apiKey={JCDECAUX_API_KEY}"
            try:
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    station = response.json()
                    name = station.get("name", f"Station {station_id}")
                    
                    main_stands = station.get("mainStands", {}).get("availabilities", {})
                    total_bikes = station.get("available_bikes", main_stands.get("bikes", 0))
                    electrical = main_stands.get("electricalBikes", 0)
                    mechanical = main_stands.get("mechanicalBikes", 0)
                    
                    if total_bikes > 0 and station_id not in notified_stations:
                        msg = (
                            f"🚲 **BIKE FOUND AT {name}**\n\n"
                            f"Total Available: **{total_bikes}**\n"
                            f"⚡ Electric: {electrical}\n"
                            f"⚙️ Mechanical: {mechanical}\n"
                        )
                        lat = station.get("position", {}).get("latitude")
                        lon = station.get("position", {}).get("longitude")
                        
                        markup = InlineKeyboardMarkup()
                        if lat and lon:
                            maps_url = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
                            markup.add(InlineKeyboardButton("🗺️ Open in Google Maps", url=maps_url))
                        markup.add(InlineKeyboardButton("🛑 Stop Hunting", callback_data="stop_hunt"))
                        
                        bot.send_message(chat_id, msg, parse_mode="Markdown", reply_markup=markup)
                        notified_stations.add(station_id)
                        
                    elif total_bikes == 0 and station_id in notified_stations:
                        msg = f"Station {name} is empty again"
                        bot.send_message(chat_id, msg, parse_mode="Markdown")
                        notified_stations.remove(station_id)
            except Exception as e:
                print(f"Error fetching data for {station_id}: {e}")
                
        # Wait 15 seconds, but allow quick interruption if stop_event is set
        stop_event.wait(15)
        
    if not stop_event.is_set():
        bot.send_message(chat_id, "🏁 **30-minute monitoring complete.** Send /findbikes or a location to start again.", parse_mode="Markdown")
        if chat_id in active_hunts:
            del active_hunts[chat_id]

def start_hunt(chat_id, target_stations):
    stop_hunt_for_chat(chat_id)
    stop_event = threading.Event()
    active_hunts[chat_id] = stop_event
    
    monitor_thread = threading.Thread(target=fetch_and_check_stations, args=(chat_id, target_stations, stop_event))
    monitor_thread.start()

# --- BOT MESSAGE HANDLERS ---

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "Hello! I am your Vélo'v tracker.\n- Send /findbikes for default stations.\n- Send a location attachment or type an address to find bikes nearby.\n- Send /stop to cancel a hunt.")

@bot.message_handler(commands=['stop'])
def cmd_stop(message):
    if stop_hunt_for_chat(message.chat.id):
        bot.reply_to(message, "🛑 Hunt stopped.")
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
        # Default to Lyon, France to make searches easier
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

@bot.callback_query_handler(func=lambda call: call.data == "stop_hunt")
def callback_stop_hunt(call):
    if stop_hunt_for_chat(call.message.chat.id):
        bot.answer_callback_query(call.id, "Hunt stopped!")
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        bot.send_message(call.message.chat.id, "🛑 Hunt stopped successfully.")
    else:
        bot.answer_callback_query(call.id, "Hunt already stopped.")
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)

if __name__ == "__main__":
    print("🤖 Bot is running and waiting for commands...")
    bot.infinity_polling()
