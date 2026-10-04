#!/usr/bin/env python3
"""Test NBA score update using nba_api"""

import sys
import logging

# Import the update function from NHL77FINAL
from NHL77FINAL import update_nba_scores

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

if __name__ == '__main__':
    print("=" * 60)
    print("Testing NBA Score Update with nba_api")
    print("=" * 60)
    print()
    
    try:
        update_nba_scores()
        print("\n" + "=" * 60)
        print("✓ Test completed successfully!")
        print("=" * 60)
    except Exception as e:
        print("\n" + "=" * 60)
        print(f"❌ Test failed: {e}")
        print("=" * 60)
        sys.exit(1)
