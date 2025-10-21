#!/usr/bin/env python3

import os
import tempfile
import subprocess
import time
from pathlib import Path

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

        file_size = os.path.getsize(tmp_path)
        print(f"✅ Extracted audio chunk: {file_size} bytes")
        return tmp_path

    except subprocess.TimeoutExpired:
        raise Exception(f"File processing timed out after {seconds + 30} seconds")
    except subprocess.CalledProcessError as e:
        raise Exception(f"Failed to process file: {e.stderr}")
    except Exception as e:
        raise

def test_mp4_restart_functionality():
    """Test MP4 restart functionality by simulating reaching end of file."""
    
    test_url = "https://lyssna-cdn.sr.se/Autorec/ET2W/P4/Sportextra/2025/10/SRP4RIKS_2025-10-13_190000_15300_a96.m4a"
    
    print(f"🧪 Testing MP4 restart functionality...")
    print(f"📁 Test URL: {test_url}")
    
    file_position = 0
    recording_length = 30
    max_cycles = 15  # Test for 15 cycles to see restart behavior
    
    for cycle in range(max_cycles):
        print(f"\n--- Cycle {cycle + 1}: Position {file_position}s ---")
        
        chunk_path = get_audio_chunk_from_file(test_url, recording_length, file_position)
        
        if chunk_path is None:
            print(f"🔄 Reached end of file at position {file_position}s, restarting from beginning")
            file_position = 0
            # Try extracting from the beginning
            chunk_path = get_audio_chunk_from_file(test_url, recording_length, file_position)
            if chunk_path is None:
                print("❌ Unable to extract audio even from beginning of file")
                break
        
        # Simulate successful processing
        if chunk_path:
            print(f"✅ Successfully processed chunk from position {file_position}s")
            # Clean up the temporary file
            os.unlink(chunk_path)
            # Update position for next cycle
            file_position += recording_length
        
        # Small delay between cycles
        time.sleep(1)
    
    print("\n🎉 MP4 restart test completed!")

if __name__ == "__main__":
    test_mp4_restart_functionality()