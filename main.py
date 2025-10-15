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

# Global variables to store latest summary
latest_summary = None
last_updated = None
processing_status = "Starting..."
processing_error = None

STREAM_URL = "https://www.sverigesradio.se/topsy/direkt/srapi/132.mp3"
REDIS_KEY_PREFIX = "sr_now:transcriptions"
REDIS_SUMMARY_KEY = "sr_now:latest_summary"

def get_latest_summary_from_redis():
    """Get the latest summary from Redis."""
    if not redis_client:
        return None
        
    try:
        summary_data = redis_client.get(REDIS_SUMMARY_KEY)
        if summary_data:
            return json.loads(summary_data)
        return None
    except Exception as e:
        print(f"⚠️ Could not load latest summary from Redis: {e}")
        return None

def save_latest_summary_to_redis(summary, timestamp=None):
    """Save the latest summary to Redis."""
    if not redis_client:
        return
        
    if timestamp is None:
        timestamp = datetime.now()
    
    try:
        summary_data = {
            "summary": summary,
            "updated": timestamp.isoformat(),
            "channel": "P1"
        }
        
        # Save to Redis with no expiration (persist until overwritten)
        redis_client.set(REDIS_SUMMARY_KEY, json.dumps(summary_data))
        
    except Exception as e:
        print(f"⚠️ Could not save latest summary to Redis: {e}")

def load_transcription_history():
    """Load transcription history from Redis."""
    if not redis_client:
        return []
        
    try:
        # Get all transcription entries from Redis
        keys = redis_client.keys(f"{REDIS_KEY_PREFIX}:*")
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

def save_transcription(text, timestamp=None):
    """Save transcription to Redis with automatic cleanup."""
    if not redis_client:
        return
        
    if timestamp is None:
        timestamp = datetime.now()
    
    try:
        # Create entry
        new_entry = {
            "timestamp": timestamp.isoformat(),
            "text": text.strip()
        }
        
        # Generate unique key with timestamp
        key = f"{REDIS_KEY_PREFIX}:{int(timestamp.timestamp())}"
        
        # Save to Redis with 24-hour expiration
        redis_client.setex(key, 86400, json.dumps(new_entry))
        
        # Clean up old entries (older than 24 hours)
        cleanup_old_transcriptions()
        
    except Exception as e:
        print(f"⚠️ Could not save transcription to Redis: {e}")

def cleanup_old_transcriptions():
    """Remove transcriptions older than 24 hours from Redis."""
    if not redis_client:
        return
        
    try:
        cutoff_time = datetime.now() - timedelta(hours=24)
        cutoff_timestamp = int(cutoff_time.timestamp())
        
        keys = redis_client.keys(f"{REDIS_KEY_PREFIX}:*")
        for key in keys:
            # Extract timestamp from key
            try:
                key_timestamp = int(key.split(':')[-1])
                if key_timestamp < cutoff_timestamp:
                    redis_client.delete(key)
            except (ValueError, IndexError):
                continue
                
    except Exception as e:
        print(f"⚠️ Could not cleanup old transcriptions: {e}")

def get_recent_context(hours=2):
    history = load_transcription_history()
    
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

def get_audio_chunk(seconds=30):
    try:
        # Create temporary file that persists after the context manager
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_path = tmp.name
        tmp.close()

        # Improved ffmpeg command for better live stream handling
        cmd = [
            "ffmpeg", "-y",
            "-i", STREAM_URL,
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
def get_latest_summary():
    """Get the latest summary from Redis."""
    # Try to get from Redis first
    redis_summary = get_latest_summary_from_redis()
    if redis_summary:
        return jsonify(redis_summary)
    
    # Fallback to global variables if Redis is empty
    global latest_summary, last_updated
    return jsonify({
        'channel': 'P1',
        'summary': latest_summary,
        'updated': last_updated.isoformat() if last_updated else None,
    })

@app.route('/transcriptions', methods=['GET'])
def get_recent_transcriptions():
    """Get all transcriptions from the last hour."""
    try:
        # Get all transcriptions
        history = load_transcription_history()
        
        if not history:
            return jsonify({
                'transcriptions': [],
                'count': 0,
                'period': 'last hour',
                'message': 'No transcriptions found'
            })
        
        # Filter for last hour
        cutoff_time = datetime.now() - timedelta(hours=1)
        recent_transcriptions = [
            {
                'timestamp': entry['timestamp'],
                'text': entry['text'],
                'time_formatted': datetime.fromisoformat(entry['timestamp']).strftime('%H:%M:%S')
            }
            for entry in history 
            if datetime.fromisoformat(entry['timestamp']) > cutoff_time
        ]
        
        # Sort by timestamp in descending order (latest first)
        recent_transcriptions.sort(key=lambda x: x['timestamp'], reverse=True)
        
        return jsonify({
            'transcriptions': recent_transcriptions,
            'count': len(recent_transcriptions),
            'period': 'last hour',
            'channel': 'P1'
        })
        
    except Exception as e:
        return jsonify({
            'error': f'Failed to retrieve transcriptions: {str(e)}',
            'transcriptions': [],
            'count': 0
        }), 500

def summarize(text, use_context=True):
    messages = [
        {
            "role": "system", 
            "content": "Du är en hjälpsam assistent som sammanfattar vad som sägs i Sveriges Radios kanaler kortfattat och tydligt. Du får kontext från tidigare transkriptioner för att ge bättre sammanhang. Undvik att summera låttexter, försök att summera vad programledare säger, gäster som kommer in i studion eller vad som händer i studion."
        }
    ]
    
    # Add context if available and requested
    if use_context:
        context = get_recent_context(hours=2)
        if context:
            messages.append({
                "role": "user",
                "content": f"Här är vad som sagts tidigare i kanalen för kontext:\n\n{context}\n\n---"
            })
    
    # Add the main request
    messages.append({
        "role": "user", 
        "content": f"Summera följande transkribering från Sveriges Radios kanal P1 till en kort sammanfattning på max 94 tecken som beskriver vad som händer just nu i direksändning: \n\n{text}"
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

def continuous_processing():
    """Run the continuous audio processing in a separate thread."""
    global latest_summary, last_updated
    
    print("🔄 Background processing thread started")
    
    while True:
        chunk_path = None
        try:
            print("🎙️ Starting audio capture...")
            
            # Record and transcribe new audio
            chunk_path = get_audio_chunk(int(os.environ.get("RECORDING_LENGTH", 30)))
            print("✅ Audio captured, transcribing...")
            
            text = transcribe(chunk_path)
            print("✅ Transcription complete")
            
            # Save the transcription
            save_transcription(text)
            
            # Create summary with context
            summary = summarize(text, use_context=True)
            print("✅ Summary generated")
            
            # Update global variables
            latest_summary = summary
            last_updated = datetime.now()
            
            # Save summary to Redis for persistence
            save_latest_summary_to_redis(summary, last_updated)
            
            # Display only the summary
            print(f"📻 {summary}")
            
        except Exception as e:
            # Log errors for debugging but continue processing
            print(f"❌ Processing error: {str(e)}")
            # Set fallback summary
            error_message = f"Processing error occurred: {str(e)[:100]}"
            latest_summary = error_message
            last_updated = datetime.now()
            
            # Save error summary to Redis for persistence
            save_latest_summary_to_redis(error_message, last_updated)
            
        finally:
            # Clean up temporary file
            if chunk_path and os.path.exists(chunk_path):
                os.unlink(chunk_path)
        
        # Wait for the update interval before next iteration
        print(f"⏳ Waiting {os.environ.get('RECORDING_INTERVAL', 900)} seconds for next capture...")
        time.sleep(int(os.environ.get("RECORDING_INTERVAL", 900)))

if __name__ == "__main__":
    # Set up signal handler for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)

    print("Hello, SR-Now here! 👋")
    
    # Test Redis connection
    if redis_client:
        try:
            print("🔄 Testing Redis connection...")
            
            # Simple ping test with the client's built-in timeout
            redis_client.ping()
            print("✅ Redis connection successful")
            
            # Initialize global variables from Redis if available
            redis_summary = get_latest_summary_from_redis()
            if redis_summary:
                latest_summary = redis_summary.get('summary')
                last_updated = datetime.fromisoformat(redis_summary.get('updated')) if redis_summary.get('updated') else None
                print(f"📻 Loaded previous summary from Redis: {latest_summary}")
                
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
    print("🎧 Continuous processing starting...")
    
    # Start continuous processing in a background thread
    processing_thread = threading.Thread(target=continuous_processing, daemon=True)
    processing_thread.start()
    
    # Give the processing thread a moment to start
    time.sleep(1)
    
    # Run Flask app
    app.run(host='0.0.0.0', port=port, debug=False)
