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
TRANSCRIPTIONS_FILE = "history.json"

def load_transcription_history():
    if not os.path.exists(TRANSCRIPTIONS_FILE):
        return []
    
    try:
        with open(TRANSCRIPTIONS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"⚠️ Could not load transcription history: {e}")
        return []

def save_transcription(text, timestamp=None):
    if timestamp is None:
        timestamp = datetime.now()
    
    # Load existing history
    history = load_transcription_history()
    
    # Add new entry
    new_entry = {
        "timestamp": timestamp.isoformat(),
        "text": text.strip()
    }
    
    history.append(new_entry)
    
    # Keep only last 24 hours of transcriptions to avoid file bloat
    cutoff_time = datetime.now() - timedelta(hours=24)
    history = [
        entry for entry in history 
        if datetime.fromisoformat(entry["timestamp"]) > cutoff_time
    ]
    
    # Save back to file
    try:
        with open(TRANSCRIPTIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except IOError as e:
        pass  # Silently handle save errors

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

def get_audio_chunk(seconds=30, dir="/tmp"):
    try:
        # Create temporary file that persists after the context manager
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", dir=dir, delete=False)
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

@app.route('/', methods=['GET'])
def get_latest_summary():
    """Get the latest summary from the continuous processing."""
    global latest_summary, last_updated
    
    return jsonify({
        'channel': 'P1',
        'summary': latest_summary,
        'updated': last_updated.isoformat() if last_updated else None,
    })

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
            chunk_path = get_audio_chunk(int(os.environ.get("LISTENING_LENGTH", 30)))
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
            
            # Display only the summary
            print(f"📻 {summary}")
            
        except Exception as e:
            # Log errors for debugging but continue processing
            print(f"❌ Processing error: {str(e)}")
            # Set fallback summary
            latest_summary = f"Processing error occurred: {str(e)[:100]}"
            last_updated = datetime.now()
            
        finally:
            # Clean up temporary file
            if chunk_path and os.path.exists(chunk_path):
                os.unlink(chunk_path)
        
        # Wait for the update interval before next iteration
        print(f"⏳ Waiting {os.environ.get('SLEEP_LENGTH', 900)} seconds for next capture...")
        time.sleep(int(os.environ.get("SLEEP_LENGTH", 900)))

if __name__ == "__main__":
    # Set up signal handler for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    
    # Get port from environment variable (Railway sets this)
    port = int(os.environ.get('PORT', 5001))
    
    print("🚀 Starting SR-Now with API endpoint...")
    print(f"📡 API available at: http://localhost:{port}/api/latest")
    print("🎧 Continuous processing starting...")
    
    # Start continuous processing in a background thread
    processing_thread = threading.Thread(target=continuous_processing, daemon=True)
    processing_thread.start()
    
    # Give the processing thread a moment to start
    time.sleep(1)
    
    # Run Flask app
    app.run(host='0.0.0.0', port=port, debug=False)
