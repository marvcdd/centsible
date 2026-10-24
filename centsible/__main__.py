"""Support ``python -m centsible`` and route to the configured front end.

支持 ``python -m centsible``，并按配置路由到对应的前端。"""

from __future__ import annotations

import sys

from centsible.interface.launcher import run

if __name__ == "__main__":
    sys.exit(run())
