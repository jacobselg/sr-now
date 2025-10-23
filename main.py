from openai import OpenAI
import tempfile
import subprocess
import os
import json
import sys
import time
import signal
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from flask import Flask
from dotenv import load_dotenv
import redis
from urllib.parse import urlparse

# Load environment variables from .env file
load_dotenv()

# Initialize Redis connection
redis_url = os.environ.get('REDIS_URL')
redis_client = None

if redis_url:
    try:
        print(f"🔄 Setting up Redis client for: {redis_url.split('@')[1] if '@' in redis_url else redis_url}")
        print(f"🔍 Full Redis URL format: redis://[user]:[pass]@[host]:[port]")
        
        # Create Redis client with shorter timeouts - don't test connection yet
        redis_client = redis.from_url(
            redis_url, 
            decode_responses=True,
            socket_connect_timeout=5,  # Shorter timeout
            socket_timeout=5,          # Shorter timeout
            retry_on_timeout=True,
            health_check_interval=30
        )
        print("✅ Redis client created successfully")
        
    except Exception as e:
        print(f"⚠️ Error setting up Redis client: {e}")
        print(f"🔍 Redis URL was: {redis_url[:20]}...")
        redis_client = None
else:
    print("⚠️ No REDIS_URL found in environment variables")

# Initialize OpenAI client - API key should be set in OPENAI_API_KEY environment variable
client = OpenAI()

# Initialize Flask app
app = Flask(__name__)

# Import and register routes
from routes import register_routes

# Global variables to store latest summaries for all channels
channel_summaries = {}
channel_last_updated = {}
processing_status = {}

# Channel configuration - can be moved to environment variables later
CHANNELS = [
    {
        "name": "Sportextra",
        "stream_url": "https://lyssna-cdn.sr.se/Autorec/ET2W/P4/Sportextra/2025/10/SRP4RIKS_2025-10-13_190000_15300_a96.m4a",
        "recording_length": 30,
        "recording_interval": 60,
        "summary_interval": 60,  
        "prompt_description": "",
        "temperature": 0.8,
    }
] if (os.environ['ENV'] == 'local') else [
    {
        "name": "P1",
        "stream_url": "https://edge2.sr.se/p1-mp3-96",
        "recording_length": 45,
        "recording_interval": 15,
        "summary_interval": 300, 
        "prompt_description": "Tonen på texten bör fokusera på fördjupning, granskning, korrekthet och nyheter.",
        "temperature": 0.2,
    },
    {
        "name": "P3",
        "stream_url": "https://edge2.sr.se/p3-mp3-96",
        "recording_length": 45,
        "recording_interval": 15,  
        "summary_interval": 300, 
        "prompt_description": "Tonen på texten bör vara avslappnad, rolig och busig för den musikintresserade publiken. Gör texten lockande och energisk, nästan lite busig.",
        "temperature": 1,

    },
    {
        "name": "P4-Gotland",
        "stream_url": "https://edge1.sr.se/p4gotl-mp3-96",
        "recording_length": 45,
        "recording_interval": 15,  
        "summary_interval": 300,  
        "prompt_description": "Tonen på texten bör fokusera på lokal fördjupning, granskning, korrekthet och lokala nyheter, men kan ha en avslappnad ton för lättsammare nyheter där det känns lämpligt.",
        "temperature": 1,
    },
    {
        "name": "Sportextra",
        "stream_url": "https://lyssna-cdn.sr.se/Autorec/ET2W/P4/Sportextra/2025/10/SRP4RIKS_2025-10-13_190000_15300_a96.m4a",
        "recording_length": 45,
        "recording_interval": 15,
        "summary_interval": 300,  
        "prompt_description": "Tonen på texten bör efterlikna att du är en sportkommentator som summerar de senaste sporthändelserna under livesändningen i fotboll mellan Sveriges och Kosovos landslag från Sveriges Radio Sportextra. Lyft ut de mest spännande ögonblick som hände nyss tex 'Sveriges kvitterar i 65:e minuter' eller 'Rött kort till Kosovo i 70:e minuten' för att göra det mer levande.",
        "temperature": 0.8,
    }
]

REDIS_KEY_PREFIX = "sr_now:transcriptions"
REDIS_SUMMARY_KEY_PREFIX = "sr_now:summary"
REDIS_FILE_POSITION_PREFIX = "sr_now:file_position"

def parse_timestamp_safely(timestamp_str):
    """Parse timestamp string and ensure it's timezone-aware (UTC if none specified)."""
    try:
        dt = datetime.fromisoformat(timestamp_str)
        # If the datetime is naive (no timezone), assume it's UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)

def load_channel_settings():
    """Load and apply environment variable overrides to channel settings."""
    for channel in CHANNELS:
        channel_name = channel["name"]
        
        # Check for channel-specific environment variables
        length_env_key = f"{channel_name}_RECORDING_LENGTH"
        interval_env_key = f"{channel_name}_RECORDING_INTERVAL"
        summary_interval_env_key = f"{channel_name}_SUMMARY_INTERVAL"
        
        # Override with environment variables if they exist
        if length_env_key in os.environ:
            try:
                channel["recording_length"] = int(os.environ[length_env_key])
                print(f"🔧 Override {channel_name} recording length: {channel['recording_length']}s")
            except ValueError:
                print(f"⚠️ Invalid {length_env_key} value, using default")
        
        if interval_env_key in os.environ:
            try:
                channel["recording_interval"] = int(os.environ[interval_env_key])
                print(f"🔧 Override {channel_name} recording interval: {channel['recording_interval']}s")
            except ValueError:
                print(f"⚠️ Invalid {interval_env_key} value, using default")
        
        if summary_interval_env_key in os.environ:
            try:
                channel["summary_interval"] = int(os.environ[summary_interval_env_key])
                print(f"🔧 Override {channel_name} summary interval: {channel['summary_interval']}s")
            except ValueError:
                print(f"⚠️ Invalid {summary_interval_env_key} value, using default")
        
        # Also check for global fallbacks (for backward compatibility)
        if "RECORDING_LENGTH" in os.environ and "recording_length" not in channel:
            try:
                channel["recording_length"] = int(os.environ["RECORDING_LENGTH"])
            except ValueError:
                pass
                
        if "RECORDING_INTERVAL" in os.environ and "recording_interval" not in channel:
            try:
                channel["recording_interval"] = int(os.environ["RECORDING_INTERVAL"])
            except ValueError:
                pass
        
        if "SUMMARY_INTERVAL" in os.environ and "summary_interval" not in channel:
            try:
                channel["summary_interval"] = int(os.environ["SUMMARY_INTERVAL"])
            except ValueError:
                pass

def get_latest_summary_from_redis(channel_name):
    """Get the latest summary from Redis for a specific channel."""
    if not redis_client:
        return None
        
    try:
        redis_key = f"{REDIS_SUMMARY_KEY_PREFIX}:{channel_name}"
        summary_data = redis_client.get(redis_key)
        if summary_data:
            return json.loads(summary_data)
        return None
    except Exception as e:
        print(f"⚠️ Could not load latest summary for {channel_name} from Redis: {e}")
        return None

def save_latest_summary_to_redis(channel_name, summary, timestamp=None):
    """Save the latest summary to Redis for a specific channel."""
    if not redis_client:
        return
        
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)
    
    # Ensure timestamp is a timezone-aware datetime object
    if isinstance(timestamp, str):
        timestamp = parse_timestamp_safely(timestamp)
    elif not isinstance(timestamp, datetime):
        timestamp = datetime.now(timezone.utc)
    elif timestamp.tzinfo is None:
        # If it's a naive datetime, assume UTC
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    
    try:
        summary_data = {
            "summary": summary,
            "updated": timestamp.isoformat(),
            "channel": channel_name
        }
        
        redis_key = f"{REDIS_SUMMARY_KEY_PREFIX}:{channel_name}"
        # Save to Redis with no expiration (persist until overwritten)
        redis_client.set(redis_key, json.dumps(summary_data))
        
    except Exception as e:
        print(f"⚠️ Could not save latest summary for {channel_name} to Redis: {e}")

def get_file_position_from_redis(channel_name):
    """Get the current file position from Redis for a specific channel."""
    if not redis_client:
        return 0
        
    try:
        redis_key = f"{REDIS_FILE_POSITION_PREFIX}:{channel_name}"
        position_data = redis_client.get(redis_key)
        if position_data:
            data = json.loads(position_data)
            return data.get('position', 0)
        return 0
    except Exception as e:
        print(f"⚠️ Could not load file position for {channel_name} from Redis: {e}")
        return 0

def save_file_position_to_redis(channel_name, position):
    """Save the current file position to Redis for a specific channel."""
    if not redis_client:
        return
        
    try:
        position_data = {
            "position": position,
            "updated": datetime.now(timezone.utc).isoformat(),
            "channel": channel_name
        }
        
        redis_key = f"{REDIS_FILE_POSITION_PREFIX}:{channel_name}"
        # Save to Redis with no expiration (persist until overwritten)
        redis_client.set(redis_key, json.dumps(position_data))
        
    except Exception as e:
        print(f"⚠️ Could not save file position for {channel_name} to Redis: {e}")

def load_transcription_history(channel_name=None):
    """Load transcription history from Redis for a specific channel or all channels."""
    if not redis_client:
        return []
        
    try:
        # Get transcription entries for specific channel or all channels
        if channel_name:
            pattern = f"{REDIS_KEY_PREFIX}:{channel_name}:*"
        else:
            pattern = f"{REDIS_KEY_PREFIX}:*"
            
        keys = redis_client.keys(pattern)
        if not keys:
            return []
        
        history = []
        for key in keys:
            entry_data = redis_client.get(key)
            if entry_data:
                entry = json.loads(entry_data)
                history.append(entry)
        
        # Sort by timestamp
        history.sort(key=lambda x: x['timestamp'])
        return history
        
    except Exception as e:
        print(f"⚠️ Could not load transcription history from Redis: {e}")
        return []

def save_transcription(channel_name, text, timestamp=None):
    """Save transcription to Redis with automatic cleanup for a specific channel."""
    if not redis_client:
        return
        
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)
    
    # Ensure timestamp is a timezone-aware datetime object
    if isinstance(timestamp, str):
        timestamp = parse_timestamp_safely(timestamp)
    elif not isinstance(timestamp, datetime):
        timestamp = datetime.now(timezone.utc)
    elif timestamp.tzinfo is None:
        # If it's a naive datetime, assume UTC
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    
    try:
        # Create entry
        new_entry = {
            "timestamp": timestamp.isoformat(),
            "text": text.strip(),
            "channel": channel_name
        }
        
        # Generate unique key with channel and timestamp
        key = f"{REDIS_KEY_PREFIX}:{channel_name}:{int(timestamp.timestamp())}"
        
        # Save to Redis with 24-hour expiration
        redis_client.setex(key, 86400, json.dumps(new_entry))
        
        # Clean up old entries (older than 24 hours)
        cleanup_old_transcriptions(channel_name)
        
    except Exception as e:
        print(f"⚠️ Could not save transcription for {channel_name} to Redis: {e}")

def cleanup_old_transcriptions(channel_name=None):
    """Remove transcriptions older than 60 minutes from Redis for a specific channel or all channels."""
    if not redis_client:
        return
        
    try:
        cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=60)
        cutoff_timestamp = int(cutoff_time.timestamp())
        
        if channel_name:
            pattern = f"{REDIS_KEY_PREFIX}:{channel_name}:*"
        else:
            pattern = f"{REDIS_KEY_PREFIX}:*"
            
        keys = redis_client.keys(pattern)
        for key in keys:
            # Extract timestamp from key (last part after final colon)
            try:
                key_timestamp = int(key.split(':')[-1])
                if key_timestamp < cutoff_timestamp:
                    redis_client.delete(key)
            except (ValueError, IndexError):
                continue
                
    except Exception as e:
        print(f"⚠️ Could not cleanup old transcriptions: {e}")

def get_recent_context(channel_name, minutes=15):
    history = load_transcription_history(channel_name)
    
    if not history:
        return ""

    cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    recent_entries = [
        entry for entry in history 
        if parse_timestamp_safely(entry["timestamp"]) > cutoff_time
    ]
    
    if not recent_entries:
        return ""
    
    # Format context
    context_parts = []
    for entry in recent_entries[-5:]:  # Last 5 entries max
        time_str = datetime.fromisoformat(entry["timestamp"]).strftime("%H:%M")
        context_parts.append(f"[{time_str}] {entry['text'][:200]}...")
    
    return "\n".join(context_parts)

def get_audio_chunk_from_file(file_url, seconds=30, start_position=0):
    """Extract audio chunk from a file starting at a specific position."""
    try:
        # Create temporary file that persists after the context manager
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_path = tmp.name
        tmp.close()

        print(f"🎬 Extracting {seconds}s from file starting at {start_position}s")
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_position),   # Start position
            "-i", file_url,
            "-t", str(seconds),           # Duration
            "-ac", "1",                   # Mono audio
            "-ar", "16000",               # 16kHz sample rate (optimal for Whisper)
            "-f", "wav",                  # WAV format for better compatibility
            tmp_path
        ]

        result = subprocess.run(
            cmd, 
            capture_output=True, 
            text=True, 
            timeout=seconds + 30  # Longer timeout for file processing
        )

        if result.returncode != 0:
            # Check if error indicates we've reached end of file
            stderr_lower = result.stderr.lower()
            if any(phrase in stderr_lower for phrase in [
                'invalid seek position',
                'seek past end of file', 
                'end of file',
                'no frames to encode',
                'duration is 0'
            ]):
                print(f"📄 Reached end of file at position {start_position}s")
                # Clean up temp file
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                return None  # Signal end of file
            else:
                raise subprocess.CalledProcessError(
                    result.returncode, 
                    cmd, 
                    output=result.stdout, 
                    stderr=result.stderr
                )

        # Check if the output file was created and has meaningful content
        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) < 1000:  # Less than 1KB suggests empty/minimal audio
            print(f"📄 Empty or minimal audio extracted at position {start_position}s - likely end of file")
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            return None  # Signal end of file

        return tmp_path

    except subprocess.TimeoutExpired:
        raise Exception(f"File processing timed out after {seconds + 30} seconds")
    except subprocess.CalledProcessError as e:
        raise Exception(f"Failed to process file: {e.stderr}")
    except Exception as e:
        raise

def get_audio_chunk(stream_url, seconds=30):
    try:
        # Create temporary file that persists after the context manager
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_path = tmp.name
        tmp.close()

        # Detect if it's a file (MP4, etc.) or live stream
        is_file = stream_url.lower().endswith(('.mp4', '.m4a', '.mov', '.avi', '.mkv', '.webm', '.3gp'))
        
        if is_file:
            # Handle MP4 and other video/audio files
            print(f"🎬 Processing file: {stream_url}")
            cmd = [
                "ffmpeg", "-y",
                "-i", stream_url,
                "-t", str(seconds),
                "-ac", "1",           # Mono audio
                "-ar", "16000",       # 16kHz sample rate (optimal for Whisper)
                "-f", "wav",          # WAV format for better compatibility
                tmp_path
            ]
        else:
            # Handle live streams with reconnection parameters
            print(f"📡 Processing live stream: {stream_url}")
            cmd = [
                "ffmpeg", "-y",
                "-i", stream_url,
                "-t", str(seconds),
                "-ac", "1",           # Mono audio
                "-ar", "16000",       # 16kHz sample rate (optimal for Whisper)
                "-f", "wav",          # WAV format for better compatibility
                "-reconnect", "1",    # Reconnect on connection loss
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "5",
                tmp_path
            ]

        result = subprocess.run(
            cmd, 
            capture_output=True, 
            text=True, 
            timeout=seconds + 10  # Add buffer time for connection/processing
        )

        if result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, 
                cmd, 
                output=result.stdout, 
                stderr=result.stderr
            )

        return tmp_path

    except subprocess.TimeoutExpired:
        raise Exception(f"Recording timed out after {seconds + 10} seconds")
    except subprocess.CalledProcessError as e:
        raise Exception(f"Failed to process audio: {e.stderr}")
    except Exception as e:
        raise

def transcribe(file_path):
    with open(file_path, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(
            model="whisper-1", 
            file=audio_file,
            language="sv",
        )
    return transcript.text

def summarize(channel_name, prompt_description, channel_temperature, latest=None):
    messages = [
        {
            "role": "system", 
            "content": f"Du är en journalist på Sveriges Radios kanal {channel_name} som vill få fler att lyssna på livesändningen via vår webbplats. Du kan med hjälp av transkriberingar från pågående livesändning ge korta, korrekta, nyfikna och intressanta summeringar av vad som pågår exakt just nu i livesändningen. Undvik att inkludera information om musik som spelas samt deras texter. Fokusera på gäster, artister, ämnen och händelser som diskuteras. Håll sammanfattningen under 100 tecken."
        }
    ]
    
    # Add context if available and requested
    context = get_recent_context(channel_name, minutes=10)

    messages.append({
        "role": "user",
        "content": f"Sammanfatta i en indragande text under 100 tecken vad som händer just nu i Sveriges Radios livesändning baserat på följande transkriberingar, undvik att använda ord som 'lyssna nu' och 'diskuteras'. {prompt_description} Transkriberingar: \n\n{latest}\n{context}\n\n"
    })
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=100,
            temperature=channel_temperature
        )
        return response.choices[0].message.content.strip()
    
    except Exception as e:
        return f"Kunde inte genomföra transkribering..."


def signal_handler(signum, frame):
    """Handle graceful shutdown on Ctrl+C."""
    exit(0)

def process_channel(channel):
    """Process a single channel continuously with separate recording and summary intervals."""
    channel_name = channel["name"]
    channel_prompt_description = channel["prompt_description"]
    channel_temperature = channel["temperature"]
    stream_url = channel["stream_url"]
    recording_length = channel.get("recording_length", 30)  # Default to 30 seconds
    recording_interval = channel.get("recording_interval", 900)  # Default to 15 minutes
    summary_interval = channel.get("summary_interval", 900)  # Default to 30 minutes
    
    # Detect if it's a file or live stream
    is_file = stream_url.lower().endswith(('.mp4', '.m4a', '.mov', '.avi', '.mkv', '.webm', '.3gp'))
    
    # Load file position from Redis for files, or start at 0
    if is_file:
        file_position = get_file_position_from_redis(channel_name)
        if file_position > 0:
            print(f"📂 {channel_name}: Resuming from position {file_position}s (loaded from Redis)")
        else:
            print(f"📂 {channel_name}: Starting from beginning of file")
    else:
        file_position = 0
    
    print(f"🔄 Background processing thread started for {channel_name}")
    print(f"⚙️ {channel_name} settings: {recording_length}s recording every {recording_interval}s, summary every {summary_interval}s")
    
    if is_file:
        print(f"🎬 {channel_name} is processing file: {stream_url}")
    else:
        print(f"📡 {channel_name} is processing live stream: {stream_url}")
    
    # Track when the next summary should be generated
    next_summary_time = datetime.now(timezone.utc) + timedelta(seconds=summary_interval)
    
    while True:
        chunk_path = None
        current_time = datetime.now(timezone.utc)
        should_generate_summary = current_time >= next_summary_time
        
        try:
            print(f"🎙️ Starting audio capture for {channel_name}...")
            
            # Record and transcribe new audio using channel-specific length
            if is_file:
                chunk_path = get_audio_chunk_from_file(stream_url, recording_length, file_position)
                
                # Check if we reached the end of the file
                if chunk_path is None:
                    print(f"🔄 {channel_name}: Reached end of file, restarting from beginning")
                    file_position = 0
                    # Save reset position to Redis
                    save_file_position_to_redis(channel_name, file_position)
                    # Try extracting from the beginning
                    chunk_path = get_audio_chunk_from_file(stream_url, recording_length, file_position)
                    if chunk_path is None:
                        raise Exception("Unable to extract audio even from beginning of file")
                
                # Update file position for next iteration and save to Redis
                file_position += recording_length
                save_file_position_to_redis(channel_name, file_position)
            else:
                chunk_path = get_audio_chunk(stream_url, recording_length)
                
            print(f"✅ Audio captured for {channel_name}, transcribing...")
            
            text = transcribe(chunk_path)
            print(f"✅ Transcription complete for {channel_name}")
            
            # Check if we got valid transcription (not empty or too short)
            if not text or len(text.strip()) < 10:
                if is_file:
                    print(f"⚠️ {channel_name}: Short/empty transcription, trying to restart from beginning")
                    # Reset to beginning of file if we've reached the end
                    file_position = 0
                    save_file_position_to_redis(channel_name, file_position)
                    print(f"🔄 {channel_name}: Resetting to beginning of file")
                    # Wait a bit longer before restarting to avoid rapid looping
                    time.sleep(5)
                else:
                    print(f"⚠️ {channel_name}: Short/empty transcription from live stream")
            
            # Always save the transcription if we have text
            if text and text.strip():
                save_transcription(channel_name, text)
            
            # Only generate summary if it's time to do so
            if should_generate_summary:
                print(f"📝 Generating summary for {channel_name} (summary interval reached)...")
                
                # Create summary with context
                summary = summarize(channel_name, channel_prompt_description, channel_temperature, text)
                print(f"✅ Summary generated for {channel_name}")
                
                # Use consistent timezone-aware timestamp for both global variables and Redis
                update_time = datetime.now(timezone.utc)
                
                # Update global variables
                channel_summaries[channel_name] = summary
                channel_last_updated[channel_name] = update_time
                processing_status[channel_name] = "Running"
                
                # Save summary to Redis for persistence with same timestamp
                save_latest_summary_to_redis(channel_name, summary, update_time)
                
                # Display the summary
                print(f"📻 {channel_name}: {summary}")
                
                # Schedule next summary
                next_summary_time = update_time + timedelta(seconds=summary_interval)
                print(f"⏰ Next summary for {channel_name} scheduled for {next_summary_time.strftime('%H:%M:%S')}")
            else:
                # Just log the transcription without generating summary
                print(f"� {channel_name} transcription saved (next summary at {next_summary_time.strftime('%H:%M:%S')})")
                processing_status[channel_name] = "Recording"
            
        except Exception as e:
            # Log errors for debugging but continue processing
            print(f"❌ Processing error for {channel_name}: {str(e)}")
            
            # Use consistent timezone-aware timestamp for error handling
            error_time = datetime.now(timezone.utc)
            
            # Set fallback summary only if we were supposed to generate one
            if should_generate_summary:
                error_message = f"Processing error occurred: {str(e)[:100]}"
                channel_summaries[channel_name] = error_message
                channel_last_updated[channel_name] = error_time
                
                # Save error summary to Redis for persistence with same timestamp
                save_latest_summary_to_redis(channel_name, error_message, error_time)
                
                # Still schedule next summary attempt
                next_summary_time = error_time + timedelta(seconds=summary_interval)
            
            processing_status[channel_name] = f"Error: {str(e)[:50]}"
            
        finally:
            # Clean up temporary file
            if chunk_path and os.path.exists(chunk_path):
                os.unlink(chunk_path)
        
        # Wait for the recording interval before next iteration
        print(f"⏳ {channel_name}: Waiting {recording_interval} seconds for next recording...")
        time.sleep(recording_interval)

def start_all_channels():
    """Start processing threads for all channels."""
    threads = []
    
    for channel in CHANNELS:
        channel_name = channel["name"]
        # Initialize channel state
        channel_summaries[channel_name] = None
        channel_last_updated[channel_name] = None
        processing_status[channel_name] = "Starting..."
        
        # Start processing thread for this channel
        thread = threading.Thread(target=process_channel, args=(channel,), daemon=True)
        thread.start()
        threads.append(thread)
        
        print(f"🚀 Started processing thread for {channel_name}")
        
        # Small delay between starting threads to avoid overwhelming the system
        time.sleep(2)
    
    return threads

if __name__ == "__main__":
    # Set up signal handler for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)

    print("Hello, SR-Now here! 👋")
    
    # Load channel settings (apply any environment variable overrides)
    load_channel_settings()
    
    print(f"📻 Configured channels: {', '.join([ch['name'] for ch in CHANNELS])}")
    
    # Display channel configurations
    for channel in CHANNELS:
        recording_length = channel.get('recording_length', 30)
        recording_interval = channel.get('recording_interval', 900)
        summary_interval = channel.get('summary_interval', 1800)
        print(f"⚙️ {channel['name']}: {recording_length}s recording every {recording_interval}s, summary every {summary_interval}s")
    
    # Test Redis connection
    if redis_client:
        try:
            print("🔄 Testing Redis connection...")
            
            # Simple ping test with the client's built-in timeout
            redis_client.ping()
            print("✅ Redis connection successful")
            
            # Initialize global variables from Redis if available for all channels
            for channel in CHANNELS:
                channel_name = channel["name"]
                redis_summary = get_latest_summary_from_redis(channel_name)
                if redis_summary:
                    channel_summaries[channel_name] = redis_summary.get('summary')
                    channel_last_updated[channel_name] = parse_timestamp_safely(redis_summary.get('updated')) if redis_summary.get('updated') else None
                    print(f"📻 Loaded previous summary for {channel_name}: {channel_summaries[channel_name]}")
                
        except Exception as e:
            print(f"❌ Redis connection test failed: {e}")
            print("⚠️ Continuing without Redis - summaries will not persist across restarts")
            redis_client = None
    else:
        print("⚠️ No Redis connection available - summaries will not persist across restarts")
    
    # Register Flask routes
    register_routes(app, CHANNELS, channel_summaries, channel_last_updated,
                   get_latest_summary_from_redis, load_transcription_history,
                   parse_timestamp_safely)
    
    # Get port from environment variable (Railway sets this)
    port = int(os.environ.get('PORT', 5001))
    
    print("🚀 Starting SR-Now with API endpoint...")
    print(f"📡 API available at: http://localhost:{port}/")
    print("🎧 Starting continuous processing for all channels...")
    
    # Start processing threads for all channels
    processing_threads = start_all_channels()
    
    # Give the processing threads a moment to start
    time.sleep(3)
    
    print(f"✅ All {len(CHANNELS)} channels started successfully")
    print("📡 Available endpoints:")
    print("  GET / - All channels summary")
    print("  GET /transcriptions - All channels transcriptions only")
    print("  GET /transcriptions/<channel_name> - Specific channel transcriptions")
    print("  GET /docs - API documentation (Scalar)")
    print("  GET /openapi.json - OpenAPI specification")
    
    # Run Flask app
    app.run(host='0.0.0.0', port=port, debug=False)
