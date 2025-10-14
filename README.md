# SR-Now 🎧

**Real-time AI-powered summarization of Sveriges Radio P1 live stream**

SR-Now continuously monitors Sveriges Radio P1's live stream, transcribes the audio using OpenAI's Whisper API, and generates contextual summaries using GPT. Perfect for staying updated on Swedish radio content with minimal effort.

## ✨ Features

- **🎙️ Continuous Audio Monitoring**: Records 30-second chunks every 2 minutes from SR P1 live stream
- **🤖 AI-Powered Transcription**: Uses OpenAI Whisper for accurate Swedish speech recognition
- **📝 Context-Aware Summaries**: GPT generates concise summaries with awareness of recent program context
- **🌐 REST API**: Access latest summaries programmatically via HTTP endpoints
- **💾 Persistent History**: Maintains 24-hour transcription history for context building
- **🔄 Real-time Processing**: Background threading for continuous operation
- **📊 Status Monitoring**: Track processing status and health via API

## 🚀 Quick Start

### Prerequisites

- Python 3.8+
- FFmpeg (for audio processing)
- OpenAI API key

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

3. **Run the application**:
   ```bash
   python sr-now.py
   ```

## 📡 API Endpoints

Once running, the following endpoints are available at `http://localhost:5001`:

### `GET /api/latest-summary`
Get the most recent AI-generated summary.

**Response:**
```json
{
  "success": true,
  "summary": "Diskussion om tränaren Jonas Thomassons beslut och spelaransvar i laget.",
  "last_updated": "2025-10-14T10:45:30.123456",
  "timestamp": "2025-10-14T10:50:15.789012"
}
```

## 🔧 Configuration

### Environment Variables

- `OPENAI_API_KEY` - Required: Your OpenAI API key for Whisper and GPT access
- `SLEEP_LENGTH` - How many seconds between each live stream recording (defaults to 500 seconds)
- `LISTENING_LENGTH` - How many seconds to record for each recording (defaults to 30 seconds)
**Made with ❤️ for the Swedish radio community**
