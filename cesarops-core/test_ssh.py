#!/usr/bin/env python3
import paramiko
import logging
import sys
import io

# Force immediate flush
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

print("Connecting to 10.0.0.40:22...", flush=True)
try:
    client.connect(
        '10.0.0.40',
        port=22,
        username='cesarops',
        password='cesarops',
        timeout=15,
        allow_agent=False,
        look_for_keys=False,
        auth_timeout=15,
        banner_timeout=15,
    )
    print("SUCCESS!", flush=True)
    
    _, out, _ = client.exec_command('echo SUCCESS', timeout=5)
    print(out.read().decode().strip(), flush=True)
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}", flush=True)
finally:
    client.close()
