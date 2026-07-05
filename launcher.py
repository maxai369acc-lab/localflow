"""PyInstaller entry point for the packaged Local Flow desktop app."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from localflow.app import main
    sys.exit(main())
