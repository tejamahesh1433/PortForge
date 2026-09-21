import subprocess
import json
import uuid
import time
import urllib.request
import os
import signal
import sys

def run(cmd, **kwargs):
    print(f"Running: {cmd}")
    return subprocess.run(cmd, shell=True, text=True, check=True, **kwargs)

def run_out(cmd):
    print(f"Running: {cmd}")
    return subprocess.run(cmd, shell=True, text=True, check=True, capture_output=True).stdout

def main():
    agent_id = str(uuid.uuid4())[:8]
    print(f"=== AGENT {agent_id} STARTING ===")
    
    contract_json = run_out("python -m portforge_agent agent-contract --json")
    contract = json.loads(contract_json)
    if contract['contract_version'] != 1:
        raise Exception("Unsupported contract version")
        
    manifest = f"""version: 1
project: temp_{agent_id}
target:
  host: f90db087-f7b4-4647-958c-e8e13051ddc3
ports:
  frontend:
    purpose: frontend
    protocol: tcp
  api:
    purpose: api
    protocol: tcp
  postgres:
    purpose: postgres
    protocol: tcp
  redis:
    purpose: redis
    protocol: tcp
config:
  dotenv:
    - file: .env
      values:
        FRONTEND_PORT: frontend
        API_PORT: api
        PG_PORT: postgres
        REDIS_PORT: redis
"""
    with open("portforge.yml", "w") as f:
        f.write(manifest)
        
    prepare_json = run_out("python -m portforge_agent workflow prepare portforge.yml --json")
    print(f"Prepared: {prepare_json}")
    
    req_id = f"req-{agent_id}"
    apply_json = run_out(f"python -m portforge_agent workflow apply portforge.yml --request-id {req_id} --json")
    apply_data = json.loads(apply_json)
    alloc_id = apply_data['allocation']['id']
    print(f"Allocated: {alloc_id}")
    
    with open(".env", "r") as f:
        lines = f.read().splitlines()
    env = {}
    for line in lines:
        if "=" in line:
            k, v = line.split("=", 1)
            env[k] = int(v)
            
    print(f"Configured ports: {env}")
    
    compose = f"""services:
  postgres_{agent_id}:
    image: postgres:15-alpine
    environment:
      POSTGRES_PASSWORD: pass
    ports:
      - "{env['PG_PORT']}:5432"
  redis_{agent_id}:
    image: redis:7-alpine
    ports:
      - "{env['REDIS_PORT']}:6379"
"""
    with open("docker-compose.yml", "w") as f:
        f.write(compose)
        
    api_code = f"""from fastapi import FastAPI
app = FastAPI()
@app.get("/health")
def health():
    return {{"status": "ok", "agent": "{agent_id}"}}
"""
    with open("main.py", "w") as f:
        f.write(api_code)
        
    frontend_code = f"<html><body>Frontend {agent_id}</body></html>"
    with open("index.html", "w") as f:
        f.write(frontend_code)
        
    run("docker compose up -d")
    
    api_proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--port", str(env['API_PORT'])])
    fe_proc = subprocess.Popen([sys.executable, "-m", "http.server", str(env['FRONTEND_PORT'])])
    
    try:
        time.sleep(10) 
        req = urllib.request.Request(f"http://127.0.0.1:{env['API_PORT']}/health")
        with urllib.request.urlopen(req) as response:
            assert response.status == 200
            assert json.loads(response.read())['agent'] == agent_id
        print("API VERIFIED")
        
        req2 = urllib.request.Request(f"http://127.0.0.1:{env['FRONTEND_PORT']}/")
        with urllib.request.urlopen(req2) as response:
            assert response.status == 200
            assert str(agent_id) in response.read().decode('utf-8')
        print("FRONTEND VERIFIED")
        
        out = run_out("docker compose ps")
        assert f"postgres_{agent_id}" in out
        assert f"redis_{agent_id}" in out
        print("DOCKER SERVICES VERIFIED")
        
        print(f"=== AGENT {agent_id} SUCCESS ===")
        
        # Concurrency wait 
        time.sleep(10)
                
    finally:
        print(f"=== AGENT {agent_id} CLEANUP ===")
        api_proc.terminate()
        fe_proc.terminate()
        run("docker compose down -v")
        run(f"python -m portforge_agent allocation release {alloc_id} --json")

if __name__ == '__main__':
    main()
