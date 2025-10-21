#!/usr/bin/env python3

import os
import tempfile
import subprocess
import time
from pathlib import Path

def get_audio_chunk_from_file(file_url, length_seconds, start_position_seconds=0):
    """Extract an audio chunk from an MP4 file starting at a specific position."""
    try:
        # Create temporary file for audio chunk
        temp_fd, temp_path = tempfile.mkstemp(suffix='.wav')
        os.close(temp_fd)
        
        # Build FFmpeg command to extract audio chunk from file
        cmd = [
            'ffmpeg',
            '-y',  # Overwrite output file
            '-i', file_url,  # Input file
            '-ss', str(start_position_seconds),  # Start position in seconds
            '-t', str(length_seconds),  # Duration to capture
            '-acodec', 'pcm_s16le',  # Audio codec
            '-ar', '16000',  # Sample rate
            '-ac', '1',  # Mono audio
            '-f', 'wav',  # Output format
            temp_path
        ]
        
        print(f"🎬 Extracting {length_seconds}s from position {start_position_seconds}s...")
        print(f"   Command: {' '.join(cmd)}")
        
        # Run FFmpeg command
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        
        if result.returncode != 0:
            print(f"❌ FFmpeg error: {result.stderr}")
            os.unlink(temp_path)
            return None
        
        # Check if file was created and has content
        if not os.path.exists(temp_path) or os.path.getsize(temp_path) == 0:
            print(f"❌ No audio extracted - likely reached end of file")
            os.unlink(temp_path)
            return None
        
        print(f"✅ Audio chunk extracted to {temp_path} (size: {os.path.getsize(temp_path)} bytes)")
        return temp_path
        
    except subprocess.TimeoutExpired:
        print(f"❌ FFmpeg timeout while processing file")
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        return None
    except Exception as e:
        print(f"❌ Error extracting audio from file: {e}")
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        return None

def test_mp4_processing():
    """Test MP4 processing with a sample file."""
    
    # Test with a sample URL (this is just a test - replace with actual MP4 URL if available)
    test_url = "https://sample-videos.com/zip/10/mp4/SampleVideo_1280x720_1mb.mp4"
    
    print(f"🧪 Testing MP4 processing...")
    print(f"📁 Test URL: {test_url}")
    
    # Test extracting first 10 seconds
    print("\n--- Test 1: Extract first 10 seconds ---")
    chunk_path = get_audio_chunk_from_file(test_url, 10, 0)
    
    if chunk_path:
        print(f"✅ Successfully extracted chunk: {chunk_path}")
        
        # Test extracting from 10 seconds in
        print("\n--- Test 2: Extract 10 seconds starting from position 10s ---")
        chunk_path2 = get_audio_chunk_from_file(test_url, 10, 10)
        
        if chunk_path2:
            print(f"✅ Successfully extracted second chunk: {chunk_path2}")
            
            # Cleanup
            os.unlink(chunk_path)
            os.unlink(chunk_path2)
            print("🧹 Cleanup complete")
        else:
            print("❌ Failed to extract second chunk")
            os.unlink(chunk_path)
    else:
        print("❌ Failed to extract first chunk")

if __name__ == "__main__":
    test_mp4_processing()