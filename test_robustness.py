import subprocess
import json
import uuid
import time
import os

def run_cmd(cmd, check=True):
    print(f"Running: {cmd}")
    result = subprocess.run(cmd, shell=True, text=True, capture_output=True)
    if check and result.returncode != 0:
        print(f"Error: {result.stderr}")
        result.check_returncode()
    return result

def main():
    agent_id = str(uuid.uuid4())[:8]
    print(f"\n=== GATE 9/10: ROBUSTNESS & RECOVERY ({agent_id}) ===")
    
    os.makedirs("robustness", exist_ok=True)
    os.chdir("robustness")
    
    manifest = f"""version: 1
project: robust_{agent_id}
target:
  host: NTMKEYA
ports:
  api:
    purpose: api
    protocol: tcp
"""
    with open("portforge.yml", "w") as f:
        f.write(manifest)

    print("\n1. Preparing workflow...")
    run_cmd("python -m portforge_agent workflow prepare portforge.yml --json")
    
    print("\n2. Applying workflow...")
    req_id = f"req-{agent_id}"
    res = run_cmd(f"python -m portforge_agent workflow apply portforge.yml --request-id {req_id} --json")
    apply_data = json.loads(res.stdout)
    alloc_id = apply_data['allocation']['id']
    print(f"Allocated: {alloc_id}")
    
    print("\n3. Testing idempotency (Agent Restart)...")
    res = run_cmd(f"python -m portforge_agent workflow apply portforge.yml --request-id {req_id} --json")
    retry_data = json.loads(res.stdout)
    assert retry_data['allocation']['id'] == alloc_id
    print("Idempotency passed.")
    
    print("\n4. Stopping Central...")
    run_cmd("docker stop portforge-api")
    
    print("\n5. Testing new workflow when Central is dead...")
    res = run_cmd("python -m portforge_agent workflow prepare portforge.yml --json", check=False)
    if res.returncode != 0 and ("No connection could be made" in res.stdout or "No connection could be made" in res.stderr):
        print("Prepare failed gracefully as expected.")
    else:
        print(f"Unexpected prepare behavior: {res.stdout} {res.stderr}")

    res = run_cmd(f"python -m portforge_agent workflow apply portforge.yml --request-id req-new --json", check=False)
    if res.returncode != 0 and ("No connection could be made" in res.stdout or "No connection could be made" in res.stderr):
        print("Apply failed gracefully as expected.")
    else:
        print(f"Unexpected apply behavior: {res.stdout} {res.stderr}")
        
    print("\n6. Starting Central...")
    run_cmd("docker start portforge-api")
    time.sleep(5)
    
    print("\n7. Releasing allocation...")
    run_cmd(f"python -m portforge_agent allocation release {alloc_id} --json")
    
    print("\n=== SUCCESS ===")

if __name__ == '__main__':
    main()
