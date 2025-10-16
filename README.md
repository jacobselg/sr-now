# SR-Now 🎧

**Real-time AI-powered summarization of multiple Sveriges Radio channels**

SR-Now continuously monitors multiple Sveriges Radio live streams (P1, P3, P4-Gotland, and special events), transcribes the audio using OpenAI's Whisper API, and generates contextual summaries using GPT. Perfect for staying updated on Swedish radio content across multiple channels with minimal effort.

## ✨ Features

- **🎙️ Multi-Channel Monitoring**: Supports P1, P3, P4-Gotland, and special event streams
- **🤖 AI-Powered Transcription**: Uses OpenAI Whisper for accurate Swedish speech recognition  
- **📝 Context-Aware Summaries**: GPT generates concise, channel-specific summaries with awareness of recent program context
- **🌐 REST API**: Complete API with multiple endpoints for summaries and transcriptions
- **📚 Interactive Documentation**: Built-in Scalar API documentation at `/docs`
- **💾 Redis Persistence**: Maintains transcription history and summaries across restarts
- **🔄 Real-time Processing**: Background threading for continuous operation across all channels
- **📊 Status Monitoring**: Track processing status and health via API
- **⚙️ Flexible Configuration**: Environment variable overrides for all channel settings
- **🌍 Production Ready**: Deployed on Railway with automatic environment detection

## 🚀 Quick Start

### Prerequisites

- Python 3.8+
- FFmpeg (for audio processing)
- OpenAI API key
- Redis (optional, for persistence)

### Installation

1. **Clone and setup**:
   ```bash
   git clone <repository-url>
   cd sr-now
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Set your OpenAI API key**:
   ```bash
   export OPENAI_API_KEY="your-api-key-here"
   ```

3. **Optional: Configure Redis for persistence**:
   ```bash
   export REDIS_URL="redis://localhost:6379"
   ```

4. **Run the application**:
   ```bash
   python main.py
   ```

## 📡 API Endpoints

Once running, the following endpoints are available:

### Base URL
- **Development**: `http://localhost:5001`
- **Production**: `https://sr-now.up.railway.app`

### Available Endpoints

#### `GET /`
Get the latest AI-generated summaries for all channels.

**Response:**
```json
[
  {
    "channel": "P1",
    "summary": "Diskussion om tränaren Jonas Thomassons beslut och spelaransvar i laget.",
    "summary_updated": "2025-10-16T10:45:30.123456+00:00",
    "summaryUpdateFrequency": 120
  },
  {
    "channel": "P3",
    "summary": "Musikmix med populärkultur och nyhetsuppdateringar.",
    "summary_updated": "2025-10-16T10:44:15.789012+00:00", 
    "summaryUpdateFrequency": 120
  }
]
```

#### `GET /transcriptions`
Get recent transcriptions for all channels (last hour).

**Response:**
```json
[
  {
    "channel": "P1",
    "transcriptions": [
      {
        "text": "Vi får nu höra mer om utvecklingen...",
        "time": "2025-10-16T10:45:30.123456+00:00"
      }
    ]
  }
]
```

#### `GET /transcriptions/<channel_name>`
Get transcriptions for a specific channel.

**Example**: `GET /transcriptions/P1`

#### `GET /docs`
Interactive API documentation powered by Scalar.

#### `GET /openapi.json`
OpenAPI 3.0 specification for the API.

## 🔧 Configuration

### Supported Channels

| Channel | Stream | Description |
|---------|--------|-------------|
| **P1** | Sveriges Radio P1 | News, current affairs, in-depth coverage |
| **P3** | Sveriges Radio P3 | Music, entertainment, pop culture |
| **P4-Gotland** | Sveriges Radio P4 Gotland | Local Gotland programming |
| **Extra03** | Special events | Sports and special broadcasts |

### Environment Variables

#### Required
- `OPENAI_API_KEY` - Your OpenAI API key for Whisper and GPT access

#### Optional - Global Settings
- `REDIS_URL` - Redis connection URL for persistence (e.g., `redis://localhost:6379`)
- `PORT` - Server port (defaults to 5001, automatically set by Railway)
- `ENV` - Set to `local` for local development (reduces channels to P1 only)
- `API_BASE_URL` - Override base URL for API documentation

#### Optional - Recording Configuration

**Global Defaults:**
- `RECORDING_LENGTH` - Seconds to record per cycle (default: 30)
- `RECORDING_INTERVAL` - Seconds between recordings (default: 120 for production, 60 for local)

**Channel-Specific Overrides:**
- `{CHANNEL}_RECORDING_LENGTH` - Per-channel recording length (e.g., `P1_RECORDING_LENGTH=45`)
- `{CHANNEL}_RECORDING_INTERVAL` - Per-channel interval (e.g., `P3_RECORDING_INTERVAL=180`)

**Examples:**
```bash
# Global settings
export RECORDING_LENGTH=45
export RECORDING_INTERVAL=180

# Channel-specific settings
export P1_RECORDING_LENGTH=30
export P1_RECORDING_INTERVAL=120
export P3_RECORDING_LENGTH=45
export P3_RECORDING_INTERVAL=300
```

### Local vs Production Configuration

The application automatically adjusts based on the `ENV` variable:

- **Local** (`ENV=local`): Monitors P1 only with 60-second intervals
- **Production** (default): Monitors all channels with 120-second intervals

## 🚀 Deployment

### Railway (Production)

The application is configured for Railway deployment:

1. Connect your GitHub repository to Railway
2. Set environment variables in Railway dashboard:
   - `OPENAI_API_KEY`
   - `REDIS_URL` (if using Railway Redis addon)
3. Deploy automatically triggers on git push

Railway automatically provides:
- `PORT` - Server port
- `RAILWAY_ENVIRONMENT` - Environment detection
- Redis addon integration

### Docker (Alternative)

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

RUN apt-get update && apt-get install -y ffmpeg

COPY . .
EXPOSE 5001

CMD ["python", "main.py"]
```

## 🔄 Architecture

### Core Components

1. **Channel Processing Threads**: Each channel runs in its own background thread
2. **Audio Recording**: FFmpeg-based stream recording in 30-second chunks  
3. **Transcription Pipeline**: OpenAI Whisper for speech-to-text
4. **Summarization Engine**: GPT-4 with channel-specific prompts and context
5. **Redis Persistence**: Stores transcriptions and summaries for recovery
6. **Flask API**: RESTful endpoints for data access
7. **Real-time Updates**: Continuous monitoring with configurable intervals

### Data Flow

```
Radio Stream → FFmpeg Recording → Whisper Transcription → 
Context Building → GPT Summarization → Redis Storage → API Response
```

## 🛠️ Development

### Local Development Setup

```bash
# Set environment for local development
export ENV=local
export OPENAI_API_KEY="your-key"
export REDIS_URL="redis://localhost:6379"  # optional

# Run application
python main.py
```

### Adding New Channels

Edit the `CHANNELS` configuration in `main.py`:

```python
{
    "name": "NewChannel",
    "stream_url": "https://example.com/stream.m3u8",
    "recording_length": 30,
    "recording_interval": 120,
    "prompt_description": "Channel-specific context for summarization",
    "temperature": 0.5,  # GPT creativity level (0.0-2.0)
}
```

## 📊 Monitoring

### Health Check

Monitor application health via the API endpoints:
- Channel status in summary responses
- Processing errors logged to console
- Redis connectivity automatically tested

### Logs

Application provides detailed logging:
- 🔄 Thread startup notifications
- ⚙️ Configuration overrides
- 🎙️ Recording progress
- ✅ Processing completion
- ❌ Error handling
- 📻 Summary outputs

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test with local configuration
5. Submit a pull request

## 📄 License

MIT License - see LICENSE file for details.

**Made with ❤️ for the Swedish radio community**
