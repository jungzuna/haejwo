#!/usr/bin/env python3
# Tombstone: exists ONLY because forward.py in 2.18-2.21 lists it in
# REQUIRED_LIB — without it those runners would never forward to this install.
import sys

print("snapshot review was removed in 2.22; review the live working copy", file=sys.stderr)
sys.exit(2)
