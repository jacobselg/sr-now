"""
Flask routes for SR-Now API
"""

import os
from datetime import datetime, timedelta, timezone
from flask import jsonify, render_template_string

def register_routes(app, CHANNELS, channel_summaries, channel_last_updated, 
                   get_latest_summary_from_redis, load_transcription_history, 
                   parse_timestamp_safely):
    """Register all Flask routes with the app."""
    
    @app.route('/', methods=['GET'])
    def get_all_channels_summary():
        """Get the latest summary and recent transcriptions for all channels."""
        channels_array = []
        
        for channel in CHANNELS:
            channel_name = channel["name"]
            
            # Try to get summary from Redis first
            redis_summary = get_latest_summary_from_redis(channel_name)
            
            # Prepare channel data
            if redis_summary:
                channel_data = redis_summary.copy()
                # Rename 'updated' field to 'summary_updated' for consistency
                if 'updated' in channel_data:
                    channel_data['summary_updated'] = channel_data.pop('updated')
            else:
                # Fallback to global variables if Redis is empty
                channel_data = {
                    'channel': channel_name,
                    'summary': channel_summaries.get(channel_name),
                    'summaryUpdated': channel_last_updated.get(channel_name).isoformat() if channel_last_updated.get(channel_name) else None,
                    'summaryUpdateFrequency': channel.get('recording_interval')
                }
            
            channels_array.append(channel_data)
        
        return jsonify(channels_array)

    @app.route('/transcriptions', methods=['GET'])
    def get_all_channels_transcriptions():
        """Get only the transcriptions for all channels."""
        channels_transcriptions = []
        
        for channel in CHANNELS:
            channel_name = channel["name"]
            
            # Get recent transcriptions
            recent_transcriptions = []
            try:
                # Get transcriptions for this channel
                history = load_transcription_history(channel_name)
                
                if history:
                    # Filter for last hour
                    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=1)
                    recent_transcriptions = [
                        {
                            'text': entry['text'],
                            'time': entry['timestamp']
                        }
                        for entry in history 
                        if parse_timestamp_safely(entry['timestamp']) > cutoff_time
                    ]
                    
                    # Sort by timestamp in descending order (latest first)
                    recent_transcriptions.sort(key=lambda x: x['time'], reverse=True)
            except Exception as e:
                print(f"⚠️ Could not load transcriptions for {channel_name}: {e}")
            
            # Prepare channel transcription data
            channel_data = {
                'channel': channel_name,
                'transcriptions': recent_transcriptions
            }
            
            channels_transcriptions.append(channel_data)
        
        return jsonify(channels_transcriptions)

    @app.route('/transcriptions/<channel_name>', methods=['GET'])
    def get_channel_transcriptions(channel_name):
        """Get transcriptions for a specific channel."""
        # Validate channel exists
        if not any(ch['name'] == channel_name for ch in CHANNELS):
            return jsonify({'error': f'Channel {channel_name} not found'}), 404
            
        try:
            # Get transcriptions for this channel
            history = load_transcription_history(channel_name)
            
            if not history:
                return jsonify({
                    'channel': channel_name,
                    'transcriptions': [],
                    'message': f'No transcriptions found for {channel_name}'
                })
            
            # Filter for last hour
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=1)
            recent_transcriptions = [
                {
                    'text': entry['text'],
                    'time': entry['timestamp']
                }
                for entry in history 
                if parse_timestamp_safely(entry['timestamp']) > cutoff_time
            ]
            
            # Sort by timestamp in descending order (latest first)
            recent_transcriptions.sort(key=lambda x: x['time'], reverse=True)
            
            return jsonify({
                'channel': channel_name,
                'transcriptions': recent_transcriptions
            })
            
        except Exception as e:
            return jsonify({
                'error': f'Failed to retrieve transcriptions for {channel_name}: {str(e)}',
                'channel': channel_name,
                'transcriptions': []
            }), 500

    @app.route('/openapi.json', methods=['GET'])
    def openapi_spec():
        """Return OpenAPI specification for the API."""
        # Determine the base URL based on environment
        base_url = os.environ.get('API_BASE_URL')
        if not base_url:
            # Check if we're running on Railway
            if os.environ.get('RAILWAY_ENVIRONMENT'):
                base_url = "https://sr-now.up.railway.app"
            else:
                # Default to localhost for development
                port = os.environ.get('PORT', 5001)
                base_url = f"http://localhost:{port}"
        
        spec = {
            "openapi": "3.0.0",
            "info": {
                "title": "SR-Now API",
                "version": "1.0.0",
                "description": "Sveriges Radio live transcription and summarization API"
            },
            "servers": [
                {
                    "url": base_url,
                    "description": "Production server" if "railway.app" in base_url else "Development server"
                }
            ],
            "paths": {
                "/": {
                    "get": {
                        "summary": "Get all channels summary",
                        "description": "Returns summary data for all configured radio channels",
                        "responses": {
                            "200": {
                                "description": "Array of channel summaries",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "channel": {"type": "string"},
                                                    "summary": {"type": "string"},
                                                    "summary_updated": {"type": "string", "format": "date-time"},
                                                    "summaryUpdateFrequency": {"type": "integer"}
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                },
                "/transcriptions": {
                    "get": {
                        "summary": "Get all channels transcriptions",
                        "description": "Returns transcription data for all configured radio channels",
                        "responses": {
                            "200": {
                                "description": "Array of channel transcriptions",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "channel": {"type": "string"},
                                                    "transcriptions": {
                                                        "type": "array",
                                                        "items": {
                                                            "type": "object",
                                                            "properties": {
                                                                "text": {"type": "string"},
                                                                "time": {"type": "string", "format": "date-time"}
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                },
                "/transcriptions/{channel_name}": {
                    "get": {
                        "summary": "Get specific channel transcriptions",
                        "description": "Returns transcription data for a specific radio channel",
                        "parameters": [
                            {
                                "name": "channel_name",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                                "description": "Name of the channel (e.g., P1, P3, P4-Gotland)"
                            }
                        ],
                        "responses": {
                            "200": {
                                "description": "Channel transcriptions",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "channel": {"type": "string"},
                                                "transcriptions": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "text": {"type": "string"},
                                                            "time": {"type": "string", "format": "date-time"}
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            },
                            "404": {
                                "description": "Channel not found",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "error": {"type": "string"}
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        return jsonify(spec)

    @app.route('/docs', methods=['GET'])
    def api_docs():
        """Serve Scalar API documentation."""
        html = """
        <!doctype html>
        <html>
          <head>
            <title>SR-Now API Documentation</title>
            <meta charset="utf-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1" />
          </head>
          <body>
            <script id="api-reference" data-url="/openapi.json"></script>
            <script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference"></script>
          </body>
        </html>
        """
        return render_template_string(html)