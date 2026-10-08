#!/usr/bin/env python3
# Tombstone: stays for INCOMING hops from 2.18-2.21 sources — their forward.py
# lists it in REQUIRED_LIB, so without it those runners would never forward to
# this install. Today's forward.py no longer requires it of any destination.
import sys

print("snapshot review was removed in 2.22; review the live working copy", file=sys.stderr)
sys.exit(2)
