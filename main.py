import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import requests
import time
import threading
import json
import os
import logging
from logging.handlers import RotatingFileHandler
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
from dotenv import load_dotenv
import psutil
import socket

# Load environment variables
load_dotenv()

# ======================
# CONFIGURATION SECTION (Environment Variables)
# ======================
CONFIG_FILE = "trading_bot_config.json"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
MT5_ACCOUNT = int(os.getenv("MT5_ACCOUNT"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD")
MT5_SERVER = os.getenv("MT5_SERVER")
VPS_REGION = os.getenv("VPS_REGION", "Unknown")
AUTHORIZED_USERS = json.loads(os.getenv("AUTHORIZED_USERS", "[]"))

# Initialize logging with rotation
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_file = "trading_bot.log"

# Rotating log handler (10MB per file, keep 5 backups)
file_handler = RotatingFileHandler(
    log_file, 
    maxBytes=10*1024*1024, 
    backupCount=5
)
file_handler.setFormatter(log_formatter)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)

# Configure root logger
logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, console_handler]
)

logger = logging.getLogger(__name__)

# =====================
# SECURITY FUNCTIONS
# =====================
def is_authorized(user_id):
    """Check if user is authorized to use the bot"""
    return str(user_id) in AUTHORIZED_USERS

def secure_function(func):
    """Decorator to check user authorization"""
    def wrapper(update: Update, context: CallbackContext, *args, **kwargs):
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            update.message.reply_text("⛔ Unauthorized access. Your user ID has been logged.")
            logger.warning(f"Unauthorized access attempt by user ID: {user_id}")
            return
        return func(update, context, *args, **kwargs)
    return wrapper

# =====================
# MT5 CONNECTION MANAGER (With Reconnection)
# =====================
class MT5ConnectionManager:
    _instance = None
    _connection_lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.initialize()
        return cls._instance
    
    def initialize(self):
        self.connected = False
        self.last_connection_attempt = 0
        self.connection_interval = 60  # Seconds between connection attempts
    
    def connect(self):
        with self._connection_lock:
            current_time = time.time()
            
            # Don't attempt too frequently
            if current_time - self.last_connection_attempt < 5:
                return self.connected
                
            self.last_connection_attempt = current_time
            
            try:
                if mt5.initialize():
                    if mt5.login(MT5_ACCOUNT, MT5_PASSWORD, MT5_SERVER):
                        self.connected = True
                        logger.info("MT5 connection established")
                        return True
                
                # Connection failed
                self.connected = False
                logger.error(f"MT5 connection failed: {mt5.last_error()}")
                mt5.shutdown()
                
            except Exception as e:
                logger.exception("MT5 connection exception")
                self.connected = False
            
            return self.connected
    
    def ensure_connection(self):
        if self.connected:
            return True
        return self.connect()
    
    def reconnect(self):
        self.connected = False
        return self.connect()

# =====================
# SYSTEM MONITOR
# =====================
class SystemMonitor:
    @staticmethod
    def get_system_status():
        """Get current system resource usage"""
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        return {
            "cpu": cpu_percent,
            "memory": {
                "total": memory.total,
                "available": memory.available,
                "used": memory.used,
                "percent": memory.percent
            },
            "disk": {
                "total": disk.total,
                "used": disk.used,
                "free": disk.free,
                "percent": disk.percent
            }
        }
    
    @staticmethod
    def get_network_latency(host="www.google.com"):
        """Measure network latency to a host"""
        try:
            start = time.time()
            socket.create_connection((host, 80), timeout=1)
            return (time.time() - start) * 1000  # ms
        except Exception:
            return float('inf')

# =====================
# STRATEGY BASE CLASS (Enhanced)
# =====================
class TradingStrategy:
    def __init__(self, config):
        self.config = config
        self.active = False
        self.mt5_manager = MT5ConnectionManager()
        self.last_execution = 0
    
    def start(self):
        self.active = True
        logger.info(f"Started {self.name} strategy")
    
    def stop(self):
        self.active = False
        logger.info(f"Stopped {self.name} strategy")
    
    def update_config(self, new_config):
        self.config.update(new_config)
        logger.info(f"Updated {self.name} configuration")
    
    def health_check(self):
        """Ensure system is healthy before trading"""
        # Check MT5 connection
        if not self.mt5_manager.ensure_connection():
            logger.error(f"{self.name} strategy aborted - MT5 not connected")
            return False
        
        # Check system resources
        system_status = SystemMonitor.get_system_status()
        if system_status['cpu'] > 90:
            logger.warning(f"High CPU usage: {system_status['cpu']}%")
            return False
            
        if system_status['memory']['percent'] > 90:
            logger.warning(f"High memory usage: {system_status['memory']['percent']}%")
            return False
            
        # Check network latency
        latency = SystemMonitor.get_network_latency()
        if latency > 100:  # 100ms threshold
            logger.warning(f"High network latency: {latency:.2f}ms")
            return False
            
        return True
    
    def execute(self):
        """Main strategy logic (to be implemented by subclasses)"""
        raise NotImplementedError("Subclasses must implement execute method")

# =====================
# TRIANGLE ARBITRAGE STRATEGY (Optimized)
# =====================
class TriangleArbitrageStrategy(TradingStrategy):
    def __init__(self, config):
        super().__init__(config)
        self.name = "Triangle Arbitrage"
        self.symbol_triplet = config.get("symbol_triplet", ["EURUSD", "USDJPY", "EURJPY"])
        self.last_trade_time = 0
        self.cooldown = config.get("cooldown", 10)  # Seconds between arbitrage checks
    
    def execute(self):
        while self.active:
            try:
                current_time = time.time()
                
                # Respect cooldown period
                if current_time - self.last_trade_time < self.cooldown:
                    time.sleep(0.1)
                    continue
                
                # Perform health check
                if not self.health_check():
                    time.sleep(5)
                    continue
                
                # ----- ARBITRAGE LOGIC GOES HERE -----
                # (Implementation from previous solution)
                
                # After trade execution
                self.last_trade_time = time.time()
                
            except Exception as e:
                logger.exception("Arbitrage strategy error")
                time.sleep(5)

# =====================
# SMART MONEY CONCEPT STRATEGY (Optimized)
# =====================
class SMCStrategy(TradingStrategy):
    def __init__(self, config):
        super().__init__(config)
        self.name = "Smart Money Concept"
        self.symbol = config.get("symbol", "EURUSD")
        self.timeframe = config.get("timeframe", mt5.TIMEFRAME_M15)
        self.data_cache = None
        self.last_update = 0
    
    def get_cached_data(self, force_update=False):
        """Cache market data to reduce MT5 calls"""
        current_time = time.time()
        cache_duration = 30  # Cache for 30 seconds
        
        if force_update or self.data_cache is None or (current_time - self.last_update) > cache_duration:
            try:
                rates = mt5.copy_rates_from_pos(
                    self.symbol, 
                    self.timeframe, 
                    0, 
                    self.config.get('lookback_bars', 100)
                )
                self.data_cache = pd.DataFrame(rates)
                self.data_cache['time'] = pd.to_datetime(self.data_cache['time'], unit='s')
                self.last_update = current_time
                logger.debug("Updated market data cache")
            except Exception as e:
                logger.error(f"Failed to update market data: {str(e)}")
        
        return self.data_cache.copy()
    
    def execute(self):
        while self.active:
            try:
                # Perform health check
                if not self.health_check():
                    time.sleep(5)
                    continue
                
                # Use cached data
                df = self.get_cached_data()
                if df is None:
                    time.sleep(5)
                    continue
                
                # ----- SMC LOGIC GOES HERE -----
                # (Implementation from previous solution)
                
                time.sleep(self.config.get('check_interval', 60))
                
            except Exception as e:
                logger.exception("SMC strategy error")
                time.sleep(5)

# =====================
# BREAKOUT STRATEGY (Optimized)
# =====================
class BreakoutStrategy(TradingStrategy):
    def __init__(self, config):
        super().__init__(config)
        self.name = "Breakout"
        self.symbol = config.get("symbol", "EURUSD")
        self.timeframe = config.get("timeframe", mt5.TIMEFRAME_H1)
        self.previous_high = 0
        self.previous_low = float('inf')
    
    def execute(self):
        while self.active:
            try:
                # Perform health check
                if not self.health_check():
                    time.sleep(5)
                    continue
                
                # Get real-time price
                tick = mt5.symbol_info_tick(self.symbol)
                if tick is None:
                    time.sleep(1)
                    continue
                
                # ----- BREAKOUT LOGIC GOES HERE -----
                # (Implementation from previous solution)
                
                time.sleep(self.config.get('check_interval', 5))
                
            except Exception as e:
                logger.exception("Breakout strategy error")
                time.sleep(5)

# =====================
# STRATEGY MANAGER (Enhanced)
# =====================
class StrategyManager:
    def __init__(self):
        self.strategies = {
            "arbitrage": TriangleArbitrageStrategy,
            "smc": SMCStrategy,
            "breakout": BreakoutStrategy
        }
        self.active_strategies = {}
        self.config = self.load_config()
        self.mt5_manager = MT5ConnectionManager()
    
    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading config: {str(e)}")
                return {"strategies": {}}
        return {"strategies": {}}
    
    def save_config(self):
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving config: {str(e)}")

# =====================
# TELEGRAM BOT WITH SECURITY (Enhanced)
# =====================
class TelegramBot:
    def __init__(self, strategy_manager):
        self.strategy_manager = strategy_manager
        self.updater = None
        self.active_strategy_menu = None
        self.mt5_manager = MT5ConnectionManager()
        
        # Define button layouts
        self.main_menu = ReplyKeyboardMarkup([
            [KeyboardButton("💰 Account Balance")],
            [KeyboardButton("📊 Trading Strategies")],
            [KeyboardButton("📜 Terms & Conditions")],
            [KeyboardButton("🖥️ System Status")]
        ], resize_keyboard=True)
        
        self.strategy_menu = ReplyKeyboardMarkup([
            [KeyboardButton("🔺 Triangle Arbitrage")],
            [KeyboardButton("💡 Smart Money Concept")],
            [KeyboardButton("🚀 Breakout Strategy")],
            [KeyboardButton("🔙 Back to Main Menu")]
        ], resize_keyboard=True)
        
        self.strategy_control_menu = ReplyKeyboardMarkup([
            [KeyboardButton("▶️ Start Strategy")],
            [KeyboardButton("⏹️ Stop Strategy")],
            [KeyboardButton("⚙️ Configure Strategy")],
            [KeyboardButton("🔙 Back to Strategies")]
        ], resize_keyboard=True)
    
    def start(self, token):
        self.updater = Updater(token)
        dp = self.updater.dispatcher
        
        # Add handlers with security decorator
        dp.add_handler(CommandHandler("start", self.secure(self.start_command)))
        dp.add_handler(MessageHandler(Filters.regex("^💰 Account Balance$"), self.secure(self.account_balance)))
        dp.add_handler(MessageHandler(Filters.regex("^📊 Trading Strategies$"), self.secure(self.show_strategy_menu)))
        dp.add_handler(MessageHandler(Filters.regex("^📜 Terms & Conditions$"), self.secure(self.show_terms)))
        dp.add_handler(MessageHandler(Filters.regex("^🖥️ System Status$"), self.secure(self.system_status)))
        dp.add_handler(MessageHandler(Filters.regex("^🔙 Back to Main Menu$"), self.secure(self.back_to_main)))
        dp.add_handler(MessageHandler(Filters.regex("^🔙 Back to Strategies$"), self.secure(self.show_strategy_menu)))
        dp.add_handler(MessageHandler(Filters.regex("^(🔺 Triangle Arbitrage|💡 Smart Money Concept|🚀 Breakout Strategy)$"), self.secure(self.select_strategy)))
        dp.add_handler(MessageHandler(Filters.regex("^(▶️ Start Strategy|⏹️ Stop Strategy|⚙️ Configure Strategy)$"), self.secure(self.control_strategy)))
        
        self.updater.start_polling()
        logger.info("Telegram bot started with enhanced security")
    
    def secure(self, func):
        """Create a secure wrapper for handlers"""
        def wrapper(update: Update, context: CallbackContext):
            return secure_function(func)(update, context)
        return wrapper
    
    @secure_function
    def start_command(self, update: Update, context: CallbackContext):
        update.message.reply_text(
            f"🤖 Welcome to the MT5 Trading Bot!\n"
            f"Server Region: {VPS_REGION}\n\n"
            "Use the buttons below to navigate:",
            reply_markup=self.main_menu
        )
    
    @secure_function
    def account_balance(self, update: Update, context: CallbackContext):
        """Get and display account balance with enhanced error handling"""
        if not self.mt5_manager.ensure_connection():
            update.message.reply_text("❌ MT5 connection unavailable. Trying to reconnect...")
            time.sleep(2)
            if not self.mt5_manager.reconnect():
                update.message.reply_text("⚠️ Failed to reconnect to MT5. Please check server status.")
                return
        
        try:
            account_info = mt5.account_info()
            if account_info:
                balance = account_info.balance
                equity = account_info.equity
                margin = account_info.margin
                free_margin = account_info.margin_free
                
                message = (
                    f"💰 <b>Account Balance</b>\n"
                    f"Balance: ${balance:.2f}\n"
                    f"Equity: ${equity:.2f}\n"
                    f"Used Margin: ${margin:.2f}\n"
                    f"Free Margin: ${free_margin:.2f}"
                )
            else:
                message = "❌ Failed to retrieve account information"
        except Exception as e:
            logger.exception("Account balance error")
            message = "❌ Error retrieving account balance"
        
        update.message.reply_text(message, parse_mode='HTML')
    
    @secure_function
    def system_status(self, update: Update, context: CallbackContext):
        """Display system and network status"""
        status = SystemMonitor.get_system_status()
        latency = SystemMonitor.get_network_latency()
        
        mt5_status = "🟢 Connected" if self.mt5_manager.connected else "🔴 Disconnected"
        
        message = (
            f"🖥️ <b>System Status</b>\n"
            f"Region: {VPS_REGION}\n"
            f"MT5 Status: {mt5_status}\n\n"
            f"<b>Resources:</b>\n"
            f"CPU: {status['cpu']}%\n"
            f"Memory: {status['memory']['percent']}%\n"
            f"Disk: {status['disk']['percent']}%\n"
            f"Network Latency: {latency:.2f} ms"
        )
        
        update.message.reply_text(message, parse_mode='HTML')

# =====================
# MAIN APPLICATION (Enhanced)
# =====================
def main():
    logger.info("Starting Trading Bot")
    logger.info(f"Running in region: {VPS_REGION}")
    
    # Initialize MT5 connection
    mt5_manager = MT5ConnectionManager()
    if not mt5_manager.connect():
        logger.critical("Initial MT5 connection failed. Exiting...")
        return
    
    # Create strategy manager
    strategy_manager = StrategyManager()
    
    # Create and start Telegram bot
    telegram_bot = TelegramBot(strategy_manager)
    bot_thread = threading.Thread(
        target=telegram_bot.start, 
        args=(TELEGRAM_TOKEN,),
        daemon=True
    )
    bot_thread.start()
    
    # System monitoring thread
    def monitor_system():
        while True:
            try:
                status = SystemMonitor.get_system_status()
                # Log resource usage periodically
                logger.info(f"System Status - CPU: {status['cpu']}% | Memory: {status['memory']['percent']}%")
                
                # Check critical conditions
                if status['cpu'] > 90 or status['memory']['percent'] > 90:
                    alert = (
                        "⚠️ <b>High Resource Usage</b>\n"
                        f"CPU: {status['cpu']}%\n"
                        f"Memory: {status['memory']['percent']}%\n"
                        "Please check server load."
                    )
                    # Send alert via Telegram (if implemented)
                
                time.sleep(300)  # Check every 5 minutes
            except Exception as e:
                logger.exception("System monitor error")
                time.sleep(60)
    
    monitor_thread = threading.Thread(target=monitor_system, daemon=True)
    monitor_thread.start()
    
    # Keep main thread alive
    try:
        while True:
            # Periodically check MT5 connection
            mt5_manager.ensure_connection()
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    except Exception as e:
        logger.exception("Main thread error")
    finally:
        # Cleanup
        logger.info("Shutting down...")
        # Stop all active strategies
        for strategy in list(strategy_manager.active_strategies.keys()):
            strategy_manager.stop_strategy(strategy)
        mt5.shutdown()
        logger.info("Shutdown complete")

if __name__ == "__main__":
    main()import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import requests
import time
import threading
import json
import os
import logging
from logging.handlers import RotatingFileHandler
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
from dotenv import load_dotenv
import psutil
import socket

# Load environment variables
load_dotenv()

# ======================
# CONFIGURATION SECTION (Environment Variables)
# ======================
CONFIG_FILE = "trading_bot_config.json"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
MT5_ACCOUNT = int(os.getenv("MT5_ACCOUNT"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD")
MT5_SERVER = os.getenv("MT5_SERVER")
VPS_REGION = os.getenv("VPS_REGION", "Unknown")
AUTHORIZED_USERS = json.loads(os.getenv("AUTHORIZED_USERS", "[]"))

# Initialize logging with rotation
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_file = "trading_bot.log"

# Rotating log handler (10MB per file, keep 5 backups)
file_handler = RotatingFileHandler(
    log_file, 
    maxBytes=10*1024*1024, 
    backupCount=5
)
file_handler.setFormatter(log_formatter)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)

# Configure root logger
logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, console_handler]
)

logger = logging.getLogger(__name__)

# =====================
# SECURITY FUNCTIONS
# =====================
def is_authorized(user_id):
    """Check if user is authorized to use the bot"""
    return str(user_id) in AUTHORIZED_USERS

def secure_function(func):
    """Decorator to check user authorization"""
    def wrapper(update: Update, context: CallbackContext, *args, **kwargs):
        user_id = update.effective_user.id
        if not is_authorized(user_id):
            update.message.reply_text("⛔ Unauthorized access. Your user ID has been logged.")
            logger.warning(f"Unauthorized access attempt by user ID: {user_id}")
            return
        return func(update, context, *args, **kwargs)
    return wrapper

# =====================
# MT5 CONNECTION MANAGER (With Reconnection)
# =====================
class MT5ConnectionManager:
    _instance = None
    _connection_lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.initialize()
        return cls._instance
    
    def initialize(self):
        self.connected = False
        self.last_connection_attempt = 0
        self.connection_interval = 60  # Seconds between connection attempts
    
    def connect(self):
        with self._connection_lock:
            current_time = time.time()
            
            # Don't attempt too frequently
            if current_time - self.last_connection_attempt < 5:
                return self.connected
                
            self.last_connection_attempt = current_time
            
            try:
                if mt5.initialize():
                    if mt5.login(MT5_ACCOUNT, MT5_PASSWORD, MT5_SERVER):
                        self.connected = True
                        logger.info("MT5 connection established")
                        return True
                
                # Connection failed
                self.connected = False
                logger.error(f"MT5 connection failed: {mt5.last_error()}")
                mt5.shutdown()
                
            except Exception as e:
                logger.exception("MT5 connection exception")
                self.connected = False
            
            return self.connected
    
    def ensure_connection(self):
        if self.connected:
            return True
        return self.connect()
    
    def reconnect(self):
        self.connected = False
        return self.connect()

# =====================
# SYSTEM MONITOR
# =====================
class SystemMonitor:
    @staticmethod
    def get_system_status():
        """Get current system resource usage"""
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        return {
            "cpu": cpu_percent,
            "memory": {
                "total": memory.total,
                "available": memory.available,
                "used": memory.used,
                "percent": memory.percent
            },
            "disk": {
                "total": disk.total,
                "used": disk.used,
                "free": disk.free,
                "percent": disk.percent
            }
        }
    
    @staticmethod
    def get_network_latency(host="www.google.com"):
        """Measure network latency to a host"""
        try:
            start = time.time()
            socket.create_connection((host, 80), timeout=1)
            return (time.time() - start) * 1000  # ms
        except Exception:
            return float('inf')

# =====================
# STRATEGY BASE CLASS (Enhanced)
# =====================
class TradingStrategy:
    def __init__(self, config):
        self.config = config
        self.active = False
        self.mt5_manager = MT5ConnectionManager()
        self.last_execution = 0
    
    def start(self):
        self.active = True
        logger.info(f"Started {self.name} strategy")
    
    def stop(self):
        self.active = False
        logger.info(f"Stopped {self.name} strategy")
    
    def update_config(self, new_config):
        self.config.update(new_config)
        logger.info(f"Updated {self.name} configuration")
    
    def health_check(self):
        """Ensure system is healthy before trading"""
        # Check MT5 connection
        if not self.mt5_manager.ensure_connection():
            logger.error(f"{self.name} strategy aborted - MT5 not connected")
            return False
        
        # Check system resources
        system_status = SystemMonitor.get_system_status()
        if system_status['cpu'] > 90:
            logger.warning(f"High CPU usage: {system_status['cpu']}%")
            return False
            
        if system_status['memory']['percent'] > 90:
            logger.warning(f"High memory usage: {system_status['memory']['percent']}%")
            return False
            
        # Check network latency
        latency = SystemMonitor.get_network_latency()
        if latency > 100:  # 100ms threshold
            logger.warning(f"High network latency: {latency:.2f}ms")
            return False
            
        return True
    
    def execute(self):
        """Main strategy logic (to be implemented by subclasses)"""
        raise NotImplementedError("Subclasses must implement execute method")

# =====================
# TRIANGLE ARBITRAGE STRATEGY (Optimized)
# =====================
class TriangleArbitrageStrategy(TradingStrategy):
    def __init__(self, config):
        super().__init__(config)
        self.name = "Triangle Arbitrage"
        self.symbol_triplet = config.get("symbol_triplet", ["EURUSD", "USDJPY", "EURJPY"])
        self.last_trade_time = 0
        self.cooldown = config.get("cooldown", 10)  # Seconds between arbitrage checks
    
    def execute(self):
        while self.active:
            try:
                current_time = time.time()
                
                # Respect cooldown period
                if current_time - self.last_trade_time < self.cooldown:
                    time.sleep(0.1)
                    continue
                
                # Perform health check
                if not self.health_check():
                    time.sleep(5)
                    continue
                
                # ----- ARBITRAGE LOGIC GOES HERE -----
                # (Implementation from previous solution)
                
                # After trade execution
                self.last_trade_time = time.time()
                
            except Exception as e:
                logger.exception("Arbitrage strategy error")
                time.sleep(5)

# =====================
# SMART MONEY CONCEPT STRATEGY (Optimized)
# =====================
class SMCStrategy(TradingStrategy):
    def __init__(self, config):
        super().__init__(config)
        self.name = "Smart Money Concept"
        self.symbol = config.get("symbol", "EURUSD")
        self.timeframe = config.get("timeframe", mt5.TIMEFRAME_M15)
        self.data_cache = None
        self.last_update = 0
    
    def get_cached_data(self, force_update=False):
        """Cache market data to reduce MT5 calls"""
        current_time = time.time()
        cache_duration = 30  # Cache for 30 seconds
        
        if force_update or self.data_cache is None or (current_time - self.last_update) > cache_duration:
            try:
                rates = mt5.copy_rates_from_pos(
                    self.symbol, 
                    self.timeframe, 
                    0, 
                    self.config.get('lookback_bars', 100)
                )
                self.data_cache = pd.DataFrame(rates)
                self.data_cache['time'] = pd.to_datetime(self.data_cache['time'], unit='s')
                self.last_update = current_time
                logger.debug("Updated market data cache")
            except Exception as e:
                logger.error(f"Failed to update market data: {str(e)}")
        
        return self.data_cache.copy()
    
    def execute(self):
        while self.active:
            try:
                # Perform health check
                if not self.health_check():
                    time.sleep(5)
                    continue
                
                # Use cached data
                df = self.get_cached_data()
                if df is None:
                    time.sleep(5)
                    continue
                
                # ----- SMC LOGIC GOES HERE -----
                # (Implementation from previous solution)
                
                time.sleep(self.config.get('check_interval', 60))
                
            except Exception as e:
                logger.exception("SMC strategy error")
                time.sleep(5)

# =====================
# BREAKOUT STRATEGY (Optimized)
# =====================
class BreakoutStrategy(TradingStrategy):
    def __init__(self, config):
        super().__init__(config)
        self.name = "Breakout"
        self.symbol = config.get("symbol", "EURUSD")
        self.timeframe = config.get("timeframe", mt5.TIMEFRAME_H1)
        self.previous_high = 0
        self.previous_low = float('inf')
    
    def execute(self):
        while self.active:
            try:
                # Perform health check
                if not self.health_check():
                    time.sleep(5)
                    continue
                
                # Get real-time price
                tick = mt5.symbol_info_tick(self.symbol)
                if tick is None:
                    time.sleep(1)
                    continue
                
                # ----- BREAKOUT LOGIC GOES HERE -----
                # (Implementation from previous solution)
                
                time.sleep(self.config.get('check_interval', 5))
                
            except Exception as e:
                logger.exception("Breakout strategy error")
                time.sleep(5)

# =====================
# STRATEGY MANAGER (Enhanced)
# =====================
class StrategyManager:
    def __init__(self):
        self.strategies = {
            "arbitrage": TriangleArbitrageStrategy,
            "smc": SMCStrategy,
            "breakout": BreakoutStrategy
        }
        self.active_strategies = {}
        self.config = self.load_config()
        self.mt5_manager = MT5ConnectionManager()
    
    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading config: {str(e)}")
                return {"strategies": {}}
        return {"strategies": {}}
    
    def save_config(self):
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving config: {str(e)}")

# =====================
# TELEGRAM BOT WITH SECURITY (Enhanced)
# =====================
class TelegramBot:
    def __init__(self, strategy_manager):
        self.strategy_manager = strategy_manager
        self.updater = None
        self.active_strategy_menu = None
        self.mt5_manager = MT5ConnectionManager()
        
        # Define button layouts
        self.main_menu = ReplyKeyboardMarkup([
            [KeyboardButton("💰 Account Balance")],
            [KeyboardButton("📊 Trading Strategies")],
            [KeyboardButton("📜 Terms & Conditions")],
            [KeyboardButton("🖥️ System Status")]
        ], resize_keyboard=True)
        
        self.strategy_menu = ReplyKeyboardMarkup([
            [KeyboardButton("🔺 Triangle Arbitrage")],
            [KeyboardButton("💡 Smart Money Concept")],
            [KeyboardButton("🚀 Breakout Strategy")],
            [KeyboardButton("🔙 Back to Main Menu")]
        ], resize_keyboard=True)
        
        self.strategy_control_menu = ReplyKeyboardMarkup([
            [KeyboardButton("▶️ Start Strategy")],
            [KeyboardButton("⏹️ Stop Strategy")],
            [KeyboardButton("⚙️ Configure Strategy")],
            [KeyboardButton("🔙 Back to Strategies")]
        ], resize_keyboard=True)
    
    def start(self, token):
        self.updater = Updater(token)
        dp = self.updater.dispatcher
        
        # Add handlers with security decorator
        dp.add_handler(CommandHandler("start", self.secure(self.start_command)))
        dp.add_handler(MessageHandler(Filters.regex("^💰 Account Balance$"), self.secure(self.account_balance)))
        dp.add_handler(MessageHandler(Filters.regex("^📊 Trading Strategies$"), self.secure(self.show_strategy_menu)))
        dp.add_handler(MessageHandler(Filters.regex("^📜 Terms & Conditions$"), self.secure(self.show_terms)))
        dp.add_handler(MessageHandler(Filters.regex("^🖥️ System Status$"), self.secure(self.system_status)))
        dp.add_handler(MessageHandler(Filters.regex("^🔙 Back to Main Menu$"), self.secure(self.back_to_main)))
        dp.add_handler(MessageHandler(Filters.regex("^🔙 Back to Strategies$"), self.secure(self.show_strategy_menu)))
        dp.add_handler(MessageHandler(Filters.regex("^(🔺 Triangle Arbitrage|💡 Smart Money Concept|🚀 Breakout Strategy)$"), self.secure(self.select_strategy)))
        dp.add_handler(MessageHandler(Filters.regex("^(▶️ Start Strategy|⏹️ Stop Strategy|⚙️ Configure Strategy)$"), self.secure(self.control_strategy)))
        
        self.updater.start_polling()
        logger.info("Telegram bot started with enhanced security")
    
    def secure(self, func):
        """Create a secure wrapper for handlers"""
        def wrapper(update: Update, context: CallbackContext):
            return secure_function(func)(update, context)
        return wrapper
    
    @secure_function
    def start_command(self, update: Update, context: CallbackContext):
        update.message.reply_text(
            f"🤖 Welcome to the MT5 Trading Bot!\n"
            f"Server Region: {VPS_REGION}\n\n"
            "Use the buttons below to navigate:",
            reply_markup=self.main_menu
        )
    
    @secure_function
    def account_balance(self, update: Update, context: CallbackContext):
        """Get and display account balance with enhanced error handling"""
        if not self.mt5_manager.ensure_connection():
            update.message.reply_text("❌ MT5 connection unavailable. Trying to reconnect...")
            time.sleep(2)
            if not self.mt5_manager.reconnect():
                update.message.reply_text("⚠️ Failed to reconnect to MT5. Please check server status.")
                return
        
        try:
            account_info = mt5.account_info()
            if account_info:
                balance = account_info.balance
                equity = account_info.equity
                margin = account_info.margin
                free_margin = account_info.margin_free
                
                message = (
                    f"💰 <b>Account Balance</b>\n"
                    f"Balance: ${balance:.2f}\n"
                    f"Equity: ${equity:.2f}\n"
                    f"Used Margin: ${margin:.2f}\n"
                    f"Free Margin: ${free_margin:.2f}"
                )
            else:
                message = "❌ Failed to retrieve account information"
        except Exception as e:
            logger.exception("Account balance error")
            message = "❌ Error retrieving account balance"
        
        update.message.reply_text(message, parse_mode='HTML')
    
    @secure_function
    def system_status(self, update: Update, context: CallbackContext):
        """Display system and network status"""
        status = SystemMonitor.get_system_status()
        latency = SystemMonitor.get_network_latency()
        
        mt5_status = "🟢 Connected" if self.mt5_manager.connected else "🔴 Disconnected"
        
        message = (
            f"🖥️ <b>System Status</b>\n"
            f"Region: {VPS_REGION}\n"
            f"MT5 Status: {mt5_status}\n\n"
            f"<b>Resources:</b>\n"
            f"CPU: {status['cpu']}%\n"
            f"Memory: {status['memory']['percent']}%\n"
            f"Disk: {status['disk']['percent']}%\n"
            f"Network Latency: {latency:.2f} ms"
        )
        
        update.message.reply_text(message, parse_mode='HTML')

# =====================
# MAIN APPLICATION (Enhanced)
# =====================
def main():
    logger.info("Starting Trading Bot")
    logger.info(f"Running in region: {VPS_REGION}")
    
    # Initialize MT5 connection
    mt5_manager = MT5ConnectionManager()
    if not mt5_manager.connect():
        logger.critical("Initial MT5 connection failed. Exiting...")
        return
    
    # Create strategy manager
    strategy_manager = StrategyManager()
    
    # Create and start Telegram bot
    telegram_bot = TelegramBot(strategy_manager)
    bot_thread = threading.Thread(
        target=telegram_bot.start, 
        args=(TELEGRAM_TOKEN,),
        daemon=True
    )
    bot_thread.start()
    
    # System monitoring thread
    def monitor_system():
        while True:
            try:
                status = SystemMonitor.get_system_status()
                # Log resource usage periodically
                logger.info(f"System Status - CPU: {status['cpu']}% | Memory: {status['memory']['percent']}%")
                
                # Check critical conditions
                if status['cpu'] > 90 or status['memory']['percent'] > 90:
                    alert = (
                        "⚠️ <b>High Resource Usage</b>\n"
                        f"CPU: {status['cpu']}%\n"
                        f"Memory: {status['memory']['percent']}%\n"
                        "Please check server load."
                    )
                    # Send alert via Telegram (if implemented)
                
                time.sleep(300)  # Check every 5 minutes
            except Exception as e:
                logger.exception("System monitor error")
                time.sleep(60)
    
    monitor_thread = threading.Thread(target=monitor_system, daemon=True)
    monitor_thread.start()
    
    # Keep main thread alive
    try:
        while True:
            # Periodically check MT5 connection
            mt5_manager.ensure_connection()
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    except Exception as e:
        logger.exception("Main thread error")
    finally:
        # Cleanup
        logger.info("Shutting down...")
        # Stop all active strategies
        for strategy in list(strategy_manager.active_strategies.keys()):
            strategy_manager.stop_strategy(strategy)
        mt5.shutdown()
        logger.info("Shutdown complete")

if __name__ == "__main__":
    main()
