"""Source-install native host entry point; Chrome's launcher pins this trusted repository."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from atlas.browser_native_host import main

if __name__ == '__main__':
    main()
