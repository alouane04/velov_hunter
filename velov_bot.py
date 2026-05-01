import telebot
import requests
import time
import threading
from dotenv import load_dotenv
import os

load_dotenv()

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
JCDECAUX_API_KEY = os.environ.get("JCDECAUX_API_KEY")
CONTRACT = os.environ.get("JCDECAUX_CONTRACT", "lyon")


# Add the station IDs you want to monitor here
TARGET_STATIONS = [5030, 5008, 5047, 5016, 5007] 

# Initialize the bot
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

def fetch_and_check_stations(chat_id):
    """The background task that runs for 30 minutes."""
    bot.send_message(chat_id, f"🕵️‍♂️ **Hunt started!** I'll check your stations every minute for the next 30 minutes.", parse_mode="Markdown")
    
    # Run for 30 minutes (30 * 60 seconds)
    end_time = time.time() + (30 * 60)
    
    # Keep track of stations we already notified you about to avoid spamming you every minute
    notified_stations = set()

    while time.time() < end_time:
        # Using the v3 API to get electrical vs mechanical breakdown
        url = f"https://api.jcdecaux.com/vls/v3/stations?contract={CONTRACT}&apiKey={JCDECAUX_API_KEY}"
        
        try:
            response = requests.get(url)
            if response.status_code == 200:
                stations = response.json()
                
                for station in stations:
                    station_id = station.get("number")
                    
                    if station_id in TARGET_STATIONS:
                        name = station.get("name", "Unknown Station")
                        
                        # Extract v3 bike data (if available) or fallback to v1 format
                        main_stands = station.get("mainStands", {}).get("availabilities", {})
                        total_bikes = station.get("available_bikes", main_stands.get("bikes", 0))
                        electrical = main_stands.get("electricalBikes", 0)
                        mechanical = main_stands.get("mechanicalBikes", 0)
                        
                        # Logic: If bikes are available and we haven't alerted you yet
                        if total_bikes > 0 and station_id not in notified_stations:
                            msg = (
                                f"🚲 **BIKE FOUND AT {name}**\n\n"
                                f"Total Available: **{total_bikes}**\n"
                                f"⚡ Electric: {electrical}\n"
                                f"⚙️ Mechanical: {mechanical}\n"
                            )
                            bot.send_message(chat_id, msg, parse_mode="Markdown")
                            notified_stations.add(station_id) # Mark as notified
                            
                        # Logic: If the station goes empty again, reset the notification lock
                        elif total_bikes == 0 and station_id in notified_stations:
                            msg = (f"Station {station_id} is empty again")
                            bot.send_message(chat_id, msg, parse_mode="Markdown")
                            notified_stations.remove(station_id)
                            
        except Exception as e:
            print(f"Error fetching data: {e}")

        # Wait 60 seconds before polling again
        time.sleep(60)
        
    bot.send_message(chat_id, "🏁 **30-minute monitoring complete.** Send /findbikes to start again.", parse_mode="Markdown")


# --- BOT MESSAGE HANDLERS ---

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "Hello! I am your Vélo'v tracker. Send /findbikes when you are ready to leave the house!")

@bot.message_handler(commands=['findbikes'])
def trigger_hunt(message):
    chat_id = message.chat.id
    # Start the monitoring loop in a separate thread so the bot doesn't freeze
    monitor_thread = threading.Thread(target=fetch_and_check_stations, args=(chat_id,))
    monitor_thread.start()

if __name__ == "__main__":
    print("🤖 Bot is running and waiting for commands...")
    # infinity_polling keeps the bot listening to Telegram 24/7
    bot.infinity_polling()
