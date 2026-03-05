# OpenAgent Runtime

A real platform for executing AI agents from GitHub repositories. No simulations - this actually clones repos, detects agents, and executes code.

## Features

- **Real Repository Cloning**: Uses GitPython to clone GitHub repositories
- **Agent Detection**: Scans for `agent.json` files or common entry points
- **Code Execution**: Runs agent code in isolated subprocesses with timeouts
- **File Generation**: Agents can generate files that are stored and served
- **ZIP Download**: Download generated projects as ZIP files
- **Session Management**: 6-hour session timeout with automatic cleanup

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Frontend  │────▶│   Backend   │────▶│   GitHub    │
│  (React)    │     │  (FastAPI)  │     │     API     │
└─────────────┘     └──────┬──────┘     └─────────────┘
                           │
                    ┌──────┴──────┐
                    │  Subprocess │
                    │  Execution  │
                    └─────────────┘
```

## Quick Start

### Option 1: Using the start script

```bash
./start.sh
```

This will:
1. Install Python dependencies if needed
2. Start the backend on http://localhost:8000
3. Install frontend dependencies and start on http://localhost:5173

### Option 2: Manual start

**Backend:**
```bash
cd backend
pip3 install fastapi uvicorn gitpython pygithub python-multipart aiofiles
python3 main.py
```

**Frontend:**
```bash
cd webapp
npm install
npm run dev
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/api/clone` | POST | Clone repo and detect agents |
| `/api/agents/{session_id}` | GET | Get detected agents |
| `/api/select-agent/{session_id}/{agent_id}` | POST | Select an agent |
| `/api/chat` | POST | Send message to agent |
| `/api/files/{session_id}` | GET | Get generated files |
| `/api/download/{session_id}` | GET | Download ZIP |
| `/api/session/{session_id}` | GET | Get session details |
| `/api/session/{session_id}` | DELETE | Delete session |

## Agent Configuration

Repositories can define agents using an `agent.json` file:

```json
{
  "id": "my-agent",
  "name": "My Agent",
  "description": "What this agent does",
  "entrypoint": "agent.py",
  "type": "chat-agent",
  "outputs": ["files"],
  "icon": "code"
}
```

If no `agent.json` is found, the system auto-detects:
- `agent.py`
- `main.py`
- `run.py`
- `app.py`

## Environment Variables

The agent receives these environment variables:

- `OPENAGENT_INPUT`: The user's message
- `OPENAGENT_OUTPUT_DIR`: Where to write generated files
- `OPENAGENT_SESSION_ID`: Current session ID

## Security

- **Timeout**: 30-second execution limit
- **Sandbox**: Agents can only write to their session folder
- **No network**: Agents run without outbound network access
- **Auto-cleanup**: Sessions expire after 6 hours

## Example Agent

```python
# agent.py
import os
import json

# Read input
user_input = os.environ.get('OPENAGENT_INPUT', '')
output_dir = os.environ.get('OPENAGENT_OUTPUT_DIR', '/tmp')

# Generate a file
with open(f'{output_dir}/hello.txt', 'w') as f:
    f.write(f'You said: {user_input}')

print(f'Generated file for: {user_input}')
```

## License

MIT
