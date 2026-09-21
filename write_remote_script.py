import sys

code = '''import subprocess
import json
import uuid
import time
import os
import sys

def run_out(cmd):
    print(f"Running: {cmd}")
    return subprocess.run(cmd, shell=True, text=True, check=True, capture_output=True).stdout

def main():
    agent_id = str(uuid.uuid4())[:8]
    print(f"=== REMOTE AGENT {agent_id} STARTING ===")
    
    # 9a224a91-450e-4078-a400-8cb312dcfdb6 is lenovoserver
    manifest = f\"\"\"version: 1
project: remote_{agent_id}
target:
  host: 9a224a91-450e-4078-a400-8cb312dcfdb6
ports:
  frontend:
    purpose: frontend
    protocol: tcp
\"\"\"
    with open("portforge.yml", "w") as f:
        f.write(manifest)
        
    prepare_json = run_out("python -m portforge_agent workflow prepare portforge.yml --json")
    print(f"Prepared: {prepare_json}")
    
    req_id = f"req-{agent_id}"
    apply_json = run_out(f"python -m portforge_agent workflow apply portforge.yml --request-id {req_id} --json")
    apply_data = json.loads(apply_json)
    alloc_id = apply_data['allocation']['id']
    print(f"Allocated: {alloc_id}")
    
    print("Remote allocation success!")
    
    # Cleanup
    run_out(f"python -m portforge_agent allocation release {alloc_id} --json")
    print("Cleanup success!")

if __name__ == '__main__':
    main()
'''
with open('D:/projects/portforge-temp-4/simulate_remote.py', 'w') as f:
    f.write(code.replace('\\"', '"'))
