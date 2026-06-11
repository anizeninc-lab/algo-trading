# Run this on server to find correct protobuf import path
import os, sys
upstox_path = '/usr/local/lib/python3.10/dist-packages/upstox_client'
for root, dirs, files in os.walk(upstox_path):
    for f in files:
        if 'pb2' in f or 'proto' in f.lower():
            print(os.path.join(root, f))
