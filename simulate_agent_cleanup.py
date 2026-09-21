import subprocess
import json
import uuid
import os
import shutil

def run_cmd(cmd, env, allow_fail=False):
    print(f"Running: {cmd}")
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True, env=env)
    if res.returncode != 0 and not allow_fail:
        print(f"Error ({res.returncode}): {res.stderr}\nStdout: {res.stdout}")
        raise Exception(f"Command failed: {cmd}")
    return res.stdout, res.returncode

def main():
    host = "NTMKEYA"
    request_id = str(uuid.uuid4())
    
    # 1. Create portforge.yml
    yml_content = """version: 1
project: test-agent-cleanup
target:
  host: NTMKEYA
ports:
  web:
    protocol: tcp
    purpose: frontend
  db:
    protocol: tcp
    purpose: postgres
"""
    os.makedirs("test-agent-cleanup", exist_ok=True)
    os.chdir("test-agent-cleanup")
    
    with open("portforge.yml", "w") as f:
        f.write(yml_content)

    env = os.environ.copy()
    env["PYTHONPATH"] = r"D:\projects\portforge\agent"
    env["PORTFORGE_TEST_ENV"] = "1"

    print("1. Validating...")
    run_cmd(f"python -m portforge_agent.cli project validate --json", env, allow_fail=True)

    print("2. Preparing...")
    prep_out, prep_code = run_cmd(f"python -m portforge_agent.cli workflow prepare --json", env, allow_fail=True)
    print(prep_out)
    
    print("3. Applying...")
    apply_out, apply_code = run_cmd(f"python -m portforge_agent.cli workflow apply --request-id {request_id} --json", env, allow_fail=True)
    print(apply_out)
    
    # We parse the allocation ID out of the apply output to release it
    apply_data = json.loads(apply_out)
    allocation_id = apply_data.get("allocation", {}).get("id")
    if not allocation_id:
        print("No allocation ID created!")
        return
    
    print("4. Status...")
    status_out, status_code = run_cmd(f"python -m portforge_agent.cli workflow status --request-id {request_id} --project-root . --json", env, allow_fail=True)
    print(status_out)
    
    print("5. CLEANUP! The agent needs to clean up.")
    print("Running allocation release...")
    release_out, release_code = run_cmd(f"python -m portforge_agent.cli allocation release {allocation_id} --json", env, allow_fail=True)
    print(release_out)

    os.chdir("..")
    shutil.rmtree("test-agent-cleanup")
    print("Cleanup successful.")

if __name__ == "__main__":
    main()
