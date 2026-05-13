"""Allow `python -m patent_hunter run ...`."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
