import requests
from openai import OpenAI
import tempfile
import subprocess
import os
import json
import sys
import time
import signal
import threading
from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, jsonify
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

# Global variables to store latest summaries for all channels
channel_summaries = {}
channel_last_updated = {}
processing_status = {}

# Channel configuration - can be moved to environment variables later
CHANNELS = [
    {
        "name": "P1",
        "stream_url": "https://edge2.sr.se/p1-mp3-96"
    },
    {
        "name": "P3",
        "stream_url": "https://edge2.sr.se/p3-mp3-96"
    }
]

REDIS_KEY_PREFIX = "sr_now:transcriptions"
REDIS_SUMMARY_KEY_PREFIX = "sr_now:summary"

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
        timestamp = datetime.now()
    
    # Ensure timestamp is a datetime object
    if isinstance(timestamp, str):
        try:
            timestamp = datetime.fromisoformat(timestamp)
        except ValueError:
            timestamp = datetime.now()
    elif not isinstance(timestamp, datetime):
        timestamp = datetime.now()
    
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
        timestamp = datetime.now()
    
    # Ensure timestamp is a datetime object
    if isinstance(timestamp, str):
        try:
            timestamp = datetime.fromisoformat(timestamp)
        except ValueError:
            timestamp = datetime.now()
    elif not isinstance(timestamp, datetime):
        timestamp = datetime.now()
    
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
    """Remove transcriptions older than 24 hours from Redis for a specific channel or all channels."""
    if not redis_client:
        return
        
    try:
        cutoff_time = datetime.now() - timedelta(hours=24)
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

def get_recent_context(channel_name, hours=2):
    history = load_transcription_history(channel_name)
    
    if not history:
        return ""
    
    cutoff_time = datetime.now() - timedelta(hours=hours)
    recent_entries = [
        entry for entry in history 
        if datetime.fromisoformat(entry["timestamp"]) > cutoff_time
    ]
    
    if not recent_entries:
        return ""
    
    # Format context
    context_parts = []
    for entry in recent_entries[-5:]:  # Last 5 entries max
        time_str = datetime.fromisoformat(entry["timestamp"]).strftime("%H:%M")
        context_parts.append(f"[{time_str}] {entry['text'][:200]}...")
    
    return "\n".join(context_parts)

def get_audio_chunk(stream_url, seconds=30):
    try:
        # Create temporary file that persists after the context manager
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_path = tmp.name
        tmp.close()

        # Improved ffmpeg command for better live stream handling
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
        raise Exception(f"Recording timed out after {seconds + 30} seconds")
    except subprocess.CalledProcessError as e:
        raise Exception(f"Failed to record audio: {e.stderr}")
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

@app.route('/', methods=['GET'])
def get_all_channels_summary():
    """Get the latest summary and recent transcriptions for all channels."""
    channels_data = {}
    
    for channel in CHANNELS:
        channel_name = channel["name"]
        
        # Try to get summary from Redis first
        redis_summary = get_latest_summary_from_redis(channel_name)
        
        # Get recent transcriptions
        recent_transcriptions = []
        try:
            # Get transcriptions for this channel
            history = load_transcription_history(channel_name)
            
            if history:
                # Filter for last hour
                cutoff_time = datetime.now() - timedelta(hours=1)
                recent_transcriptions = [
                    {
                        'text': entry['text'],
                        'time': entry['timestamp']
                    }
                    for entry in history 
                    if datetime.fromisoformat(entry['timestamp']) > cutoff_time
                ]
                
                # Sort by timestamp in descending order (latest first)
                recent_transcriptions.sort(key=lambda x: x['time'], reverse=True)
        except Exception as e:
            print(f"⚠️ Could not load transcriptions for {channel_name}: {e}")
        
        # Prepare channel data
        if redis_summary:
            channel_data = redis_summary.copy()
            # Rename 'updated' field to 'summary_updated' for consistency
            if 'updated' in channel_data:
                channel_data['summary_updated'] = channel_data.pop('updated')
            channel_data['transcriptions'] = recent_transcriptions
        else:
            # Fallback to global variables if Redis is empty
            channel_data = {
                'channel': channel_name,
                'summary': channel_summaries.get(channel_name),
                'summary_updated': channel_last_updated.get(channel_name).isoformat() if channel_last_updated.get(channel_name) else None,
                'transcriptions': recent_transcriptions
            }
        
        channels_data[channel_name] = channel_data
    
    return jsonify(channels_data)

@app.route('/channels/<channel_name>', methods=['GET'])
def get_channel_summary(channel_name):
    """Get the latest summary and recent transcriptions for a specific channel."""
    # Validate channel exists
    if not any(ch['name'] == channel_name for ch in CHANNELS):
        return jsonify({'error': f'Channel {channel_name} not found'}), 404
    
    # Try to get summary from Redis first
    redis_summary = get_latest_summary_from_redis(channel_name)
    
    # Get recent transcriptions
    recent_transcriptions = []
    try:
        # Get transcriptions for this channel
        history = load_transcription_history(channel_name)
        
        if history:
            # Filter for last hour
            cutoff_time = datetime.now() - timedelta(hours=1)
            recent_transcriptions = [
                {
                    'text': entry['text'],
                    'time': entry['timestamp']
                }
                for entry in history 
                if datetime.fromisoformat(entry['timestamp']) > cutoff_time
            ]
            
            # Sort by timestamp in descending order (latest first)
            recent_transcriptions.sort(key=lambda x: x['time'], reverse=True)
    except Exception as e:
        print(f"⚠️ Could not load transcriptions for {channel_name}: {e}")
    
    # Prepare response
    if redis_summary:
        response_data = redis_summary.copy()
        # Rename 'updated' field to 'summary_updated' for consistency
        if 'updated' in response_data:
            response_data['summary_updated'] = response_data.pop('updated')
        response_data['transcriptions'] = recent_transcriptions
        return jsonify(response_data)
    
    # Fallback to global variables if Redis is empty
    return jsonify({
        'channel': channel_name,
        'summary': channel_summaries.get(channel_name),
        'summary_updated': channel_last_updated.get(channel_name).isoformat() if channel_last_updated.get(channel_name) else None,
        'transcriptions': recent_transcriptions
    })

@app.route('/transcriptions/<channel_name>', methods=['GET'])
def get_channel_transcriptions(channel_name):
    """Get all transcriptions from the last hour for a specific channel."""
    # Validate channel exists
    if not any(ch['name'] == channel_name for ch in CHANNELS):
        return jsonify({'error': f'Channel {channel_name} not found'}), 404
        
    try:
        # Get transcriptions for this channel
        history = load_transcription_history(channel_name)
        
        if not history:
            return jsonify({
                'transcriptions': [],
                'message': f'No transcriptions found for {channel_name}'
            })
        
        # Filter for last hour
        cutoff_time = datetime.now() - timedelta(hours=1)
        recent_transcriptions = [
            {
                'text': entry['text'],
                'time_formatted': datetime.fromisoformat(entry['timestamp']).strftime('%H:%M:%S')
            }
            for entry in history 
            if datetime.fromisoformat(entry['timestamp']) > cutoff_time
        ]
        
        # Sort by timestamp in descending order (latest first)
        recent_transcriptions.sort(key=lambda x: x['time_formatted'], reverse=True)
        
        return jsonify({
            'transcriptions': recent_transcriptions,
            'channel': channel_name
        })
        
    except Exception as e:
        return jsonify({
            'error': f'Failed to retrieve transcriptions for {channel_name}: {str(e)}',
            'transcriptions': [],
            'count': 0
        }), 500

def summarize(channel_name, text, use_context=True):
    messages = [
        {
            "role": "system", 
            "content": f"Du är en hjälpsam assistent som sammanfattar vad som sägs i Sveriges Radios kanal {channel_name} kortfattat och tydligt. Du får kontext från tidigare transkriptioner för att ge bättre sammanhang. Undvik att summera låttexter, försök att summera vad programledare säger, gäster som kommer in i studion eller vad som händer i studion."
        }
    ]
    
    # Add context if available and requested
    if use_context:
        context = get_recent_context(channel_name, hours=2)
        if context:
            messages.append({
                "role": "user",
                "content": f"Här är vad som sagts tidigare i kanalen för kontext:\n\n{context}\n\n---"
            })
    
    # Add the main request
    messages.append({
        "role": "user", 
        "content": f"Summera följande transkribering från Sveriges Radios kanal {channel_name} till en kort sammanfattning på max 94 tecken som beskriver vad som händer just nu i direksändning: \n\n{text}"
    })
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=100,
            temperature=0.3
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Transkribering: {text[:100]}..."


def signal_handler(signum, frame):
    """Handle graceful shutdown on Ctrl+C."""
    exit(0)

def process_channel(channel):
    """Process a single channel continuously."""
    channel_name = channel["name"]
    stream_url = channel["stream_url"]
    
    print(f"🔄 Background processing thread started for {channel_name}")
    
    while True:
        chunk_path = None
        try:
            print(f"🎙️ Starting audio capture for {channel_name}...")
            
            # Record and transcribe new audio
            chunk_path = get_audio_chunk(stream_url, int(os.environ.get("RECORDING_LENGTH", 30)))
            print(f"✅ Audio captured for {channel_name}, transcribing...")
            
            text = transcribe(chunk_path)
            print(f"✅ Transcription complete for {channel_name}")
            
            # Save the transcription
            save_transcription(channel_name, text)
            
            # Create summary with context
            summary = summarize(channel_name, text, use_context=True)
            print(f"✅ Summary generated for {channel_name}")
            
            # Update global variables
            channel_summaries[channel_name] = summary
            channel_last_updated[channel_name] = datetime.now()
            processing_status[channel_name] = "Running"
            
            # Save summary to Redis for persistence
            save_latest_summary_to_redis(channel_name, summary)
            
            # Display only the summary
            print(f"📻 {channel_name}: {summary}")
            
        except Exception as e:
            # Log errors for debugging but continue processing
            print(f"❌ Processing error for {channel_name}: {str(e)}")
            # Set fallback summary
            error_message = f"Processing error occurred: {str(e)[:100]}"
            channel_summaries[channel_name] = error_message
            channel_last_updated[channel_name] = datetime.now()
            processing_status[channel_name] = f"Error: {str(e)[:50]}"
            
            # Save error summary to Redis for persistence
            save_latest_summary_to_redis(channel_name, error_message)
            
        finally:
            # Clean up temporary file
            if chunk_path and os.path.exists(chunk_path):
                os.unlink(chunk_path)
        
        # Wait for the update interval before next iteration
        wait_time = int(os.environ.get("RECORDING_INTERVAL", 900))
        print(f"⏳ {channel_name}: Waiting {wait_time} seconds for next capture...")
        time.sleep(wait_time)

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
    print(f"📻 Configured channels: {', '.join([ch['name'] for ch in CHANNELS])}")
    
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
                    channel_last_updated[channel_name] = datetime.fromisoformat(redis_summary.get('updated')) if redis_summary.get('updated') else None
                    print(f"📻 Loaded previous summary for {channel_name}: {channel_summaries[channel_name]}")
                
        except Exception as e:
            print(f"❌ Redis connection test failed: {e}")
            print("⚠️ Continuing without Redis - summaries will not persist across restarts")
            redis_client = None
    else:
        print("⚠️ No Redis connection available - summaries will not persist across restarts")
    
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
    print("  GET /channels/<channel_name> - Specific channel summary")
    print("  GET /transcriptions/<channel_name> - Channel transcriptions")
    
    # Run Flask app
    app.run(host='0.0.0.0', port=port, debug=False)
