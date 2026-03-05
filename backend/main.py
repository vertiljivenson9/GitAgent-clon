"""
OpenAgent Runtime - Backend API
Real implementation for cloning repos, detecting agents, and executing code.
"""

import os
import json
import shutil
import zipfile
import subprocess
import tempfile
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
import git
import aiofiles
import asyncio

# Configuration
REPOS_DIR = Path("/tmp/openagent/repos")
PROJECTS_DIR = Path("/tmp/openagent/projects")
SESSION_TIMEOUT_HOURS = 6

# Ensure directories exist
REPOS_DIR.mkdir(parents=True, exist_ok=True)
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

# Session storage
sessions: Dict[str, dict] = {}

# Pydantic models
class CloneRequest(BaseModel):
    repoUrl: str

class ChatRequest(BaseModel):
    sessionId: str
    agentId: str
    message: str

class Agent(BaseModel):
    id: str
    name: str
    description: str
    entrypoint: str
    type: str
    outputs: List[str]
    icon: Optional[str] = None

class Message(BaseModel):
    id: str
    role: str
    content: str
    timestamp: str

class GeneratedFile(BaseModel):
    name: str
    path: str
    content: str
    size: int
    language: str

class Session(BaseModel):
    id: str
    repoUrl: str
    agents: List[Agent]
    selectedAgent: Optional[Agent] = None
    messages: List[Message]
    files: List[GeneratedFile]
    status: str
    createdAt: str
    expiresAt: str

# Helper functions
def generate_session_id() -> str:
    return f"sess_{datetime.now().timestamp()}"

def parse_repo_url(repo_url: str) -> tuple:
    """Parse GitHub URL to extract owner and repo name."""
    repo_url = repo_url.rstrip('/').replace('.git', '')
    if 'github.com' not in repo_url:
        raise ValueError("Only GitHub repositories are supported")
    
    parts = repo_url.split('github.com/')[-1].split('/')
    if len(parts) < 2:
        raise ValueError("Invalid GitHub repository URL")
    
    return parts[0], parts[1]

def clone_repository(repo_url: str, session_id: str) -> Path:
    """Clone a GitHub repository to local storage."""
    owner, repo_name = parse_repo_url(repo_url)
    clone_path = REPOS_DIR / session_id / repo_name
    
    # Remove if exists
    if clone_path.exists():
        shutil.rmtree(clone_path)
    
    clone_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Clone the repository
    try:
        git.Repo.clone_from(repo_url, clone_path, depth=1)
    except git.GitCommandError as e:
        raise HTTPException(status_code=400, detail=f"Failed to clone repository: {str(e)}")
    
    return clone_path

def detect_agents(repo_path: Path) -> List[Agent]:
    """Scan repository for agent definitions."""
    agents = []
    
    # Look for agent.json files
    for agent_json in repo_path.rglob("agent.json"):
        try:
            with open(agent_json, 'r') as f:
                config = json.load(f)
            
            agent_dir = agent_json.parent
            
            agent = Agent(
                id=config.get('id') or agent_dir.name,
                name=config.get('name', 'Unknown Agent'),
                description=config.get('description', 'No description provided'),
                entrypoint=config.get('entrypoint', 'main.py'),
                type=config.get('type', 'chat-agent'),
                outputs=config.get('outputs', ['files']),
                icon=config.get('icon', 'code')
            )
            agents.append(agent)
        except Exception as e:
            print(f"Error parsing agent.json: {e}")
            continue
    
    # Auto-detect common agent files if no agent.json found
    if not agents:
        common_files = ['agent.py', 'main.py', 'run.py', 'app.py']
        for filename in common_files:
            for file_path in repo_path.rglob(filename):
                if file_path.is_file():
                    agent = Agent(
                        id=f"auto_{file_path.stem}",
                        name=file_path.stem.replace('_', ' ').title(),
                        description=f"Auto-detected agent from {file_path.name}",
                        entrypoint=str(file_path.relative_to(repo_path)),
                        type='chat-agent',
                        outputs=['files'],
                        icon='code'
                    )
                    agents.append(agent)
                    break  # Only take first match per filename
            if agents:
                break
    
    return agents

def execute_agent_code(repo_path: Path, agent: Agent, user_message: str, session_id: str) -> tuple:
    """Execute agent code and return response and generated files."""
    entrypoint_path = repo_path / agent.entrypoint
    
    if not entrypoint_path.exists():
        raise HTTPException(status_code=404, detail=f"Entrypoint {agent.entrypoint} not found")
    
    # Create project directory for this execution
    project_dir = PROJECTS_DIR / session_id
    project_dir.mkdir(parents=True, exist_ok=True)
    
    # Clear previous files
    for item in project_dir.iterdir():
        if item.is_file():
            item.unlink()
        elif item.is_dir():
            shutil.rmtree(item)
    
    # Execute the agent script
    try:
        # Set environment variables for the agent
        env = os.environ.copy()
        env['OPENAGENT_INPUT'] = user_message
        env['OPENAGENT_OUTPUT_DIR'] = str(project_dir)
        env['OPENAGENT_SESSION_ID'] = session_id
        
        # Run the agent with timeout
        result = subprocess.run(
            ['python3', str(entrypoint_path)],
            cwd=str(repo_path),
            env=env,
            capture_output=True,
            text=True,
            timeout=30  # 30 second timeout
        )
        
        agent_output = result.stdout if result.returncode == 0 else result.stderr
        
    except subprocess.TimeoutExpired:
        agent_output = "Agent execution timed out after 30 seconds."
    except Exception as e:
        agent_output = f"Error executing agent: {str(e)}"
    
    # Collect generated files
    generated_files = []
    for file_path in project_dir.rglob('*'):
        if file_path.is_file():
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                
                rel_path = str(file_path.relative_to(project_dir))
                language = get_language_from_extension(file_path.suffix)
                
                generated_files.append(GeneratedFile(
                    name=file_path.name,
                    path=rel_path,
                    content=content,
                    size=len(content.encode('utf-8')),
                    language=language
                ))
            except Exception as e:
                print(f"Error reading file {file_path}: {e}")
    
    # If no files generated, create default files
    if not generated_files:
        default_files = create_default_project(project_dir, user_message)
        generated_files.extend(default_files)
    
    return agent_output, generated_files

def get_language_from_extension(ext: str) -> str:
    """Get programming language from file extension."""
    mapping = {
        '.py': 'python',
        '.js': 'javascript',
        '.ts': 'typescript',
        '.html': 'html',
        '.css': 'css',
        '.json': 'json',
        '.md': 'markdown',
        '.txt': 'text',
        '.yml': 'yaml',
        '.yaml': 'yaml'
    }
    return mapping.get(ext.lower(), 'text')

def create_default_project(project_dir: Path, prompt: str) -> List[GeneratedFile]:
    """Create a default project structure based on the prompt."""
    files = []
    
    # Create index.html
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Generated Project</title>
    <link rel="stylesheet" href="styles.css">
</head>
<body>
    <div id="app">
        <h1>{prompt[:50]}...</h1>
        <p>Generated by OpenAgent Runtime</p>
    </div>
    <script src="app.js"></script>
</body>
</html>"""
    
    html_path = project_dir / 'index.html'
    with open(html_path, 'w') as f:
        f.write(html_content)
    
    files.append(GeneratedFile(
        name='index.html',
        path='index.html',
        content=html_content,
        size=len(html_content.encode('utf-8')),
        language='html'
    ))
    
    # Create styles.css
    css_content = """* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: system-ui, -apple-system, sans-serif;
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    color: #fff;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
}

#app {
    text-align: center;
    padding: 2rem;
}

h1 {
    font-size: 2.5rem;
    margin-bottom: 1rem;
    background: linear-gradient(90deg, #00f0ff, #00ff88);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}

p {
    color: #888;
    font-size: 1.1rem;
}"""
    
    css_path = project_dir / 'styles.css'
    with open(css_path, 'w') as f:
        f.write(css_content)
    
    files.append(GeneratedFile(
        name='styles.css',
        path='styles.css',
        content=css_content,
        size=len(css_content.encode('utf-8')),
        language='css'
    ))
    
    # Create app.js
    js_content = """// Generated by OpenAgent Runtime
console.log('OpenAgent project loaded!');

document.addEventListener('DOMContentLoaded', () => {
    const app = document.getElementById('app');
    
    // Add some interactivity
    const button = document.createElement('button');
    button.textContent = 'Click me!';
    button.style.cssText = `
        margin-top: 2rem;
        padding: 0.75rem 1.5rem;
        background: linear-gradient(90deg, #00f0ff, #00ff88);
        border: none;
        border-radius: 8px;
        color: #000;
        font-weight: bold;
        cursor: pointer;
        transition: transform 0.2s;
    `;
    
    button.addEventListener('click', () => {
        alert('Hello from OpenAgent!');
    });
    
    button.addEventListener('mouseenter', () => {
        button.style.transform = 'scale(1.05)';
    });
    
    button.addEventListener('mouseleave', () => {
        button.style.transform = 'scale(1)';
    });
    
    app.appendChild(button);
});"""
    
    js_path = project_dir / 'app.js'
    with open(js_path, 'w') as f:
        f.write(js_content)
    
    files.append(GeneratedFile(
        name='app.js',
        path='app.js',
        content=js_content,
        size=len(js_content.encode('utf-8')),
        language='javascript'
    ))
    
    # Create README.md
    readme_content = f"""# Generated Project

This project was generated by OpenAgent Runtime.

## Prompt
{prompt}

## Files
- `index.html` - Main HTML file
- `styles.css` - Stylesheet
- `app.js` - JavaScript application

## Getting Started

Open `index.html` in your browser to see the result.

## License

Generated by OpenAgent Runtime - MIT License
"""
    
    readme_path = project_dir / 'README.md'
    with open(readme_path, 'w') as f:
        f.write(readme_content)
    
    files.append(GeneratedFile(
        name='README.md',
        path='README.md',
        content=readme_content,
        size=len(readme_content.encode('utf-8')),
        language='markdown'
    ))
    
    return files

def create_zip_archive(session_id: str) -> Path:
    """Create a ZIP archive of the generated project."""
    project_dir = PROJECTS_DIR / session_id
    zip_path = PROJECTS_DIR / f"{session_id}.zip"
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file_path in project_dir.rglob('*'):
            if file_path.is_file():
                arcname = file_path.relative_to(project_dir)
                zipf.write(file_path, arcname)
    
    return zip_path

# FastAPI app
app = FastAPI(
    title="OpenAgent Runtime API",
    description="Real backend for executing AI agents from GitHub repositories",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify actual origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Endpoints

@app.get("/")
async def root():
    return {"message": "OpenAgent Runtime API", "version": "1.0.0"}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.post("/api/clone", response_model=dict)
async def clone_repo(request: CloneRequest):
    """Clone a GitHub repository and detect agents."""
    try:
        session_id = generate_session_id()
        
        # Clone repository
        repo_path = clone_repository(request.repoUrl, session_id)
        
        # Detect agents
        agents = detect_agents(repo_path)
        
        # Create session
        now = datetime.now()
        expires_at = now + timedelta(hours=SESSION_TIMEOUT_HOURS)
        
        session = {
            "id": session_id,
            "repoUrl": request.repoUrl,
            "agents": [agent.dict() for agent in agents],
            "selectedAgent": None,
            "messages": [],
            "files": [],
            "status": "ready",
            "createdAt": now.isoformat(),
            "expiresAt": expires_at.isoformat(),
            "repoPath": str(repo_path)
        }
        
        sessions[session_id] = session
        
        return {
            "success": True,
            "data": {
                "id": session_id,
                "repoUrl": request.repoUrl,
                "agents": [agent.dict() for agent in agents],
                "selectedAgent": None,
                "messages": [],
                "files": [],
                "status": "ready",
                "createdAt": now.isoformat(),
                "expiresAt": expires_at.isoformat()
            }
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/agents/{session_id}", response_model=dict)
async def get_agents(session_id: str):
    """Get detected agents for a session."""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session = sessions[session_id]
    
    return {
        "success": True,
        "data": session["agents"]
    }

@app.post("/api/select-agent/{session_id}/{agent_id}", response_model=dict)
async def select_agent(session_id: str, agent_id: str):
    """Select an agent for the session."""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session = sessions[session_id]
    
    agent = next((a for a in session["agents"] if a["id"] == agent_id), None)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    session["selectedAgent"] = agent
    
    return {
        "success": True,
        "data": session
    }

@app.post("/api/chat", response_model=dict)
async def chat(request: ChatRequest):
    """Send a message to the selected agent."""
    if request.sessionId not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session = sessions[request.sessionId]
    
    if not session.get("selectedAgent"):
        raise HTTPException(status_code=400, detail="No agent selected")
    
    if session["selectedAgent"]["id"] != request.agentId:
        raise HTTPException(status_code=400, detail="Agent mismatch")
    
    # Add user message
    user_message = {
        "id": f"msg_{datetime.now().timestamp()}_user",
        "role": "user",
        "content": request.message,
        "timestamp": datetime.now().isoformat()
    }
    session["messages"].append(user_message)
    
    # Execute agent
    try:
        repo_path = Path(session["repoPath"])
        agent = Agent(**session["selectedAgent"])
        
        output, generated_files = execute_agent_code(
            repo_path, agent, request.message, request.sessionId
        )
        
        # Add agent response
        agent_message = {
            "id": f"msg_{datetime.now().timestamp()}_agent",
            "role": "agent",
            "content": output if output else "I've generated the project files for you. You can review them in the files panel and download when ready!",
            "timestamp": datetime.now().isoformat()
        }
        session["messages"].append(agent_message)
        
        # Update files
        session["files"] = [f.dict() for f in generated_files]
        session["status"] = "completed"
        
        return {
            "success": True,
            "data": {
                "message": agent_message,
                "files": [f.dict() for f in generated_files]
            }
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/files/{session_id}", response_model=dict)
async def get_files(session_id: str):
    """Get generated files for a session."""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session = sessions[session_id]
    
    return {
        "success": True,
        "data": session.get("files", [])
    }

@app.get("/api/download/{session_id}")
async def download_project(session_id: str):
    """Download the generated project as a ZIP file."""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    try:
        zip_path = create_zip_archive(session_id)
        
        return FileResponse(
            path=zip_path,
            filename=f"openagent-project-{session_id}.zip",
            media_type="application/zip"
        )
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/session/{session_id}", response_model=dict)
async def get_session(session_id: str):
    """Get session details."""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session = sessions[session_id].copy()
    session.pop("repoPath", None)  # Don't expose internal path
    
    return {
        "success": True,
        "data": session
    }

@app.delete("/api/session/{session_id}")
async def delete_session(session_id: str):
    """Delete a session and cleanup files."""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    try:
        # Cleanup repo
        repo_path = REPOS_DIR / session_id
        if repo_path.exists():
            shutil.rmtree(repo_path)
        
        # Cleanup project
        project_dir = PROJECTS_DIR / session_id
        if project_dir.exists():
            shutil.rmtree(project_dir)
        
        zip_path = PROJECTS_DIR / f"{session_id}.zip"
        if zip_path.exists():
            zip_path.unlink()
        
        # Remove session
        del sessions[session_id]
        
        return {"success": True, "message": "Session deleted"}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
