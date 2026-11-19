from .cli import main

if __name__ == "__main__":  # worker processes (spawn) re-import this module
    raise SystemExit(main())
