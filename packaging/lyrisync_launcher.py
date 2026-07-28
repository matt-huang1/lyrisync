"""Entry point for the .app bundle.

A bundle needs a script to freeze, and it must be the same entry the
`lyrisync` console script uses — one main(), one startup order. Anything
that has to happen before the window exists (the accessory activation
policy, most of all) lives inside it, not here.
"""

import sys

from lyrisync.window import main

if __name__ == "__main__":
    sys.exit(main())
