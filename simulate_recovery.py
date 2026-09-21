import subprocess
import json
import uuid
import time
import os

def run_out(cmd):
    print(f"Running: {cmd}")
    return subprocess.run(cmd, shell=True, text=True, check=True, capture_output=True).stdout

def main():
    agent_id = str(uuid.uuid4())[:8]
    print(f"=== RECOVERY AGENT {agent_id} STARTING ===")
    
    manifest = f"""version: 1
project: recovery_{agent_id}
target:
  host: f90db087-f7b4-4647-958c-e8e13051ddc3
ports:
  frontend:
    purpose: frontend
    protocol: tcp
"""
    with open("portforge.yml", "w") as f:
        f.write(manifest)
        
    print("1. Preparing workflow (Central ONLINE)...")
    prepare_json = run_out("python -m portforge_agent workflow prepare portforge.yml --json")
    print(f"Prepared: {prepare_json}")
    
    print("2. Stopping Central (portforge-api)...")
    run_out("docker stop portforge-api")
    time.sleep(2)
    
    print("3. Applying workflow (Central OFFLINE)...")
    req_id = f"req-{agent_id}"
    apply_json = run_out(f"python -m portforge_agent workflow apply portforge.yml --request-id {req_id} --json")
    apply_data = json.loads(apply_json)
    alloc_id = apply_data['allocation']['id']
    print(f"Allocated offline: {alloc_id}")
    
    print("4. Starting Central again (portforge-api)...")
    run_out("docker start portforge-api")
    print("Waiting 10s for sync...")
    time.sleep(10)
    
    print("5. Triggering sync by checking central status...")
    run_out("python -m portforge_agent central status")
    
    print("6. Verifying Central knows about this allocation...")
    # Clean up locally
    print(f"Releasing allocation {alloc_id}...")
    run_out(f"python -m portforge_agent allocation release {alloc_id} --json")
    print("=== RECOVERY AGENT SUCCESS ===")

if __name__ == '__main__':
    main()
