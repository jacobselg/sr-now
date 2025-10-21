#!/usr/bin/env python3

import os
import json
import redis
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize Redis connection
redis_url = os.environ.get('REDIS_URL')
redis_client = None

if redis_url:
    try:
        redis_client = redis.from_url(
            redis_url, 
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True
        )
        print("✅ Redis client created successfully")
    except Exception as e:
        print(f"⚠️ Error setting up Redis client: {e}")
        redis_client = None
else:
    print("⚠️ No REDIS_URL found in environment variables")

REDIS_FILE_POSITION_PREFIX = "sr_now:file_position"

def test_file_position_storage():
    """Test storing and retrieving file positions from Redis."""
    if not redis_client:
        print("❌ No Redis client available")
        return
    
    try:
        # Test ping
        redis_client.ping()
        print("✅ Redis connection working")
        
        # Test storing file position
        channel_name = "Sportextra"
        test_position = 150  # 2.5 minutes into the file
        
        print(f"\n🧪 Testing file position storage for {channel_name}...")
        
        # Save position
        position_data = {
            "position": test_position,
            "updated": "2025-10-21T08:30:00Z",
            "channel": channel_name
        }
        
        redis_key = f"{REDIS_FILE_POSITION_PREFIX}:{channel_name}"
        redis_client.set(redis_key, json.dumps(position_data))
        print(f"💾 Saved position {test_position}s to Redis")
        
        # Retrieve position
        stored_data = redis_client.get(redis_key)
        if stored_data:
            data = json.loads(stored_data)
            stored_position = data.get('position', 0)
            print(f"📂 Retrieved position: {stored_position}s")
            
            if stored_position == test_position:
                print("✅ File position storage test PASSED")
            else:
                print(f"❌ File position storage test FAILED - expected {test_position}, got {stored_position}")
        else:
            print("❌ No data retrieved from Redis")
            
        # Test updating position
        new_position = 180  # 3 minutes
        position_data["position"] = new_position
        redis_client.set(redis_key, json.dumps(position_data))
        print(f"💾 Updated position to {new_position}s")
        
        # Retrieve updated position
        stored_data = redis_client.get(redis_key)
        if stored_data:
            data = json.loads(stored_data)
            stored_position = data.get('position', 0)
            print(f"📂 Retrieved updated position: {stored_position}s")
            
            if stored_position == new_position:
                print("✅ File position update test PASSED")
            else:
                print(f"❌ File position update test FAILED - expected {new_position}, got {stored_position}")
        
        # Show all file position keys
        print(f"\n📋 All file position keys in Redis:")
        keys = redis_client.keys(f"{REDIS_FILE_POSITION_PREFIX}:*")
        for key in keys:
            data = redis_client.get(key)
            if data:
                parsed = json.loads(data)
                print(f"  {key}: {parsed['position']}s (updated: {parsed.get('updated', 'unknown')})")
        
        print("\n🎉 File position Redis test completed!")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")

if __name__ == "__main__":
    test_file_position_storage()